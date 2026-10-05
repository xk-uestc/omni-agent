"""Independent, persistent document corpus with original-file provenance."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import contextmanager
from math import log
from pathlib import Path
from typing import Any

from .chunk_cleaning import DocumentChunker
from .cross_source import DocumentHit, DocumentRecord, JsonDocumentRetriever, _tokenize
from .evidence_context import (MAX_EVIDENCE_CHARS, MAX_NATIVE_EVIDENCE_CHARS, MAX_EVIDENCE_ITEMS,
                               MAX_TOTAL_EVIDENCE_CHARS, bounded_prefix, text_sha256)

SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
SUPPORTED = {'pdf', 'docx', 'xlsx', 'txt', 'md', 'image'}
_RETRIEVAL_STOP_WORDS = frozenset('a an the what which who whom whose is are was were be been being '
    'do does did how to of for from in on at by with and or as this that these those '
    'it its please tell me can could would should according document report'.split())


def _page_scope_coverage(records, query):
    """Body-only, IDF-weighted coverage of each original document page.

    Page neighbors affect ranking only. They never become model evidence or
    override explicit identifier filters. Titles and metadata do not establish
    the presence of a requested entity/year in the original page body.
    """
    pages = {}
    for record in records:
        key = (record.metadata['document_id'], record.metadata.get('page_no'))
        pages.setdefault(key, set()).update(_tokenize(record.content))
    terms = set(_tokenize(query)) - _RETRIEVAL_STOP_WORDS
    if not terms or not pages:
        return {}
    frequencies = Counter(term for page in pages.values() for term in page if term in terms)
    weights = {term: log(1 + (len(pages) + .5) / (frequencies[term] + .5)) for term in terms}
    denominator = sum(weights.values())
    return {key: sum(weights[term] for term in terms.intersection(body)) / denominator
            for key, body in pages.items()}


class SourceIntegrityError(ValueError):
    """A cached excerpt cannot be used when its actual source is unavailable."""


class SourceRevisionError(SourceIntegrityError):
    """The logical document now names a different version than the used source."""


def _verified_pdf_row(chunk):
    """Authorize only parser-certified literal, current-page table relations.

    A bbox alone does not establish columns. Legacy inferred tables and
    inherited headers remain unverified; their raw excerpts stay available.
    """
    metadata = chunk.get('metadata', {})
    structure = metadata.get('pdf_table_structure', {})
    if not isinstance(structure, dict):
        return False
    if (structure.get('version') != 'pdf-explicit-delimiter-v1'
            or structure.get('status') != 'verified'
            or structure.get('method') != 'literal_pipe'
            or structure.get('uniform_column_count') is not True
            or structure.get('current_page_header') is not True
            or structure.get('header_inherited') is not False
            or structure.get('coordinate_verified') is not True
            or chunk.get('content_type') != 'row'
            or metadata.get('part_no') is not None):
        return False
    headers, cells = metadata.get('source_headers'), metadata.get('source_row_cells')
    if (not isinstance(headers, list) or not isinstance(cells, list)
            or len(headers) < 2 or len(headers) != len(cells)
            or type(structure.get('column_count')) is not int
            or structure['column_count'] != len(headers)
            or not all(isinstance(value, str) for value in headers + cells)
            or metadata.get('headers') != headers or metadata.get('values') != cells):
        return False
    digest = hashlib.sha256(json.dumps(cells, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    if digest != metadata.get('source_row_cells_sha256'):
        return False
    source_text = ' | '.join(f'{key}: {value}' for key, value in zip(headers, cells) if value != '')
    expected = DocumentChunker.clean_text(f'{metadata.get("table_name")} | ' + ' | '.join(headers) + '\n' + source_text)
    return bool(source_text) and chunk.get('text') == expected


class KnowledgeStore:
    def __init__(self, root: str | Path, *, ocr_pipeline=None, embedder=None, generator=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets = self.root / 'assets'
        self.assets.mkdir(exist_ok=True)
        self.database = self.root / 'knowledge.sqlite'
        self.chunker = DocumentChunker()
        self.ocr_pipeline = ocr_pipeline
        self.generator = generator
        with self.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    modality TEXT NOT NULL, filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE);
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
            ''')
        self.dense_index = None
        if embedder is not None:
            from .dense_retrieval import DenseIndex
            self.dense_index = DenseIndex(self, embedder)

    def retrieval_health(self):
        return {'mode': 'bm25_dense_rrf' if self.dense_index else 'bm25',
                'embedding_model': self.dense_index.embedder.identity if self.dense_index else None,
                'dense_answer_threshold': 0.6 if self.dense_index else None}

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=15)
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def validate_id(document_id):
        if not isinstance(document_id, str) or not SAFE_ID.fullmatch(document_id) or document_id in {'.', '..'}:
            raise ValueError('document_id 必须为安全的字母数字标识符')

    def ingest(self, raw: bytes, *, document_id: str, title: str, modality: str, filename: str, language='eng', excel_tables=None):
        self.validate_id(document_id)
        if modality not in SUPPORTED or not raw or len(raw) > 20 * 1024 * 1024:
            raise ValueError('不支持的文档格式、空文件或超过 20 MiB 限制')
        if excel_tables and modality != 'xlsx':
            raise ValueError('Excel表区域仅可用于xlsx文件')
        if not title.strip() or len(title) > 200:
            raise ValueError('文档标题为空或超出上限')
        filename = Path(filename.replace('\\', '/')).name
        if not filename or len(filename) > 200:
            raise ValueError('无效文件名')
        if modality in {'txt', 'md'}:
            text = raw.decode('utf-8-sig')
            from .text_structure import chunk_text
            parsed = chunk_text(self.chunker, text, document_id=document_id, modality=modality)
        elif modality == 'pdf':
            parsed = self.chunker.parse_pdf(raw, document_id=document_id, ocr_pipeline=self.ocr_pipeline, language=language).to_dict()
        elif modality == 'docx':
            parsed = self.chunker.parse_docx(raw, document_id=document_id, ocr_pipeline=self.ocr_pipeline, language=language).to_dict()
        elif modality == 'xlsx':
            parsed = self.chunker.parse_xlsx(raw, document_id=document_id, excel_tables=excel_tables).to_dict()
        else:
            parsed = self.chunker.parse_image(raw, document_id=document_id, ocr_pipeline=self.ocr_pipeline, language=language).to_dict()
        digest = hashlib.sha256(raw).hexdigest()
        extension = {'image': Path(filename).suffix.lower() if Path(filename).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'} else '.png'}.get(modality, '.' + modality)
        asset = self.assets / (digest + extension)
        if not asset.exists():
            # Content-addressed immutable files: concurrent identical ingestion is safe.
            try:
                with asset.open('xb') as stream:
                    stream.write(raw)
            except FileExistsError:
                pass
        if hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
            raise ValueError('原文件完整性校验失败')
        from .document_analysis import DocumentAnalyzer, PageSignal
        evidence_chunks = [chunk for chunk in parsed['chunks'] if chunk['content_type'] != 'image']
        page_count = int(parsed['stats'].get('page_count') or 1)
        pages = []
        for page_no in range(1, page_count+1):
            page_chunks = [chunk for chunk in evidence_chunks if (chunk['page_no'] or 1)==page_no]
            ocr_chunks = [chunk for chunk in page_chunks if chunk['content_type'].startswith('ocr') or chunk['metadata'].get('ocr_executor')]
            pages.append(PageSignal(page_no, '\n'.join(chunk['text'] for chunk in page_chunks),
                ocr_confidence=min((chunk['quality'] for chunk in ocr_chunks), default=1), scanned=bool(ocr_chunks)))
        analysis = DocumentAnalyzer().analyze(pages=pages, document_id=document_id).to_dict()
        complex_split = analysis['complexity_score'] >= 0.3
        if complex_split:
            splitter = DocumentChunker(max_chars=900, overlap_chars=80)
            resplit = []
            for chunk in parsed['chunks']:
                if len(chunk['text']) > 900 and chunk['content_type'] not in {'image','table_row','heading'} and modality != 'xlsx':
                    resplit.extend(c.to_dict() for c in splitter._make_chunks(document_id, modality, chunk['content_type'], chunk['text'],
                        locator=chunk['source_locator'], title_path=tuple(chunk['title_path']), page_no=chunk['page_no'],
                        quality=chunk['quality'], warnings=tuple(chunk['warnings']), metadata=chunk['metadata'], overlap=True))
                else:
                    resplit.append(chunk)
            parsed['chunks'] = resplit
        record = {'document_id': document_id, 'title': title, 'modality': modality,
                  'filename': filename, 'sha256': digest, 'asset': asset.name,
                  'chunk_count': len(parsed['chunks']), 'warnings': parsed['warnings'], 'stats': parsed['stats'],
                  'analysis': analysis,
                  'routing': {'chunk_strategy': 'complex_adaptive_900' if complex_split else 'hierarchical' if any(chunk['title_path'] for chunk in parsed['chunks']) else 'paragraph',
                              'formula_tool': any(formula['status'] in {'requires_parameters','evaluated'} for formula in analysis['formulas']),
                              'ocr_executed': any(chunk['content_type'].startswith('ocr') or chunk['metadata'].get('ocr_executor') for chunk in parsed['chunks']),
                              'review_required': analysis['quality_score']<0.6 or bool(parsed['warnings']) or
                                  bool(analysis['metrics']['text_quality']['typo_candidate_count']) or
                                  'unrecognized_character' in analysis['metrics']['text_quality']['warnings']}}
        with self.connect() as connection:
            connection.execute('DELETE FROM documents WHERE document_id=?', (document_id,))
            connection.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)', (document_id, title, modality, filename, digest, json.dumps(record, ensure_ascii=False)))
            for chunk in parsed['chunks']:
                chunk['metadata'].update({'source_sha256': digest, 'original_document_id': document_id})
                connection.execute('INSERT INTO chunks VALUES(?,?,?)', (chunk['chunk_id'], document_id, json.dumps(chunk, ensure_ascii=False)))
        return record

    def list_documents(self):
        with self.connect() as connection:
            return [json.loads(row[0]) for row in connection.execute('SELECT payload FROM documents ORDER BY document_id')]

    def document(self, document_id):
        self.validate_id(document_id)
        with self.connect() as connection:
            row = connection.execute('SELECT payload FROM documents WHERE document_id=?', (document_id,)).fetchone()
            if row is None:
                raise KeyError(document_id)
            result = json.loads(row[0])
            result['chunks'] = [json.loads(item[0]) for item in connection.execute('SELECT payload FROM chunks WHERE document_id=? ORDER BY rowid', (document_id,))]
        self._verified_asset(result)
        return result

    def verify_source(self, document_id, *, expected_sha256=None):
        self.validate_id(document_id)
        with self.connect() as connection:
            row = connection.execute('SELECT payload FROM documents WHERE document_id=?',(document_id,)).fetchone()
            if row is None:
                if expected_sha256 is not None:
                    raise SourceRevisionError('执行中引用的资料已移除，请重新选择资料。')
                raise KeyError(document_id)
        record = json.loads(row[0])
        if expected_sha256 is not None and record['sha256'] != expected_sha256:
            raise SourceRevisionError('资料版本在执行期间已更新，请从当前资料重新执行任务。')
        return self._verified_asset(record)

    def _verified_asset(self, document):
        path = self.assets / document['asset']
        try:
            if path.is_symlink() or path.resolve().parent != self.assets.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != document['sha256']:
                raise SourceIntegrityError('原文件完整性检查失败，请恢复匹配的原文件或重新入库。')
        except OSError as exc:
            raise SourceIntegrityError('原文件不可读取，请恢复匹配的原文件或重新入库。') from exc
        return path

    def original(self, document_id):
        document = self.document(document_id)
        path = self.assets / document['asset']
        return path, document['filename']

    def visual_asset(self, document_id, *, page_no, expected_source_sha256, crop_display_pt=None):
        from .visual_work_budget import visual_work_slot
        with visual_work_slot():
            return self._visual_asset(document_id, page_no=page_no, expected_source_sha256=expected_source_sha256,
                                      crop_display_pt=crop_display_pt)

    def _visual_asset(self, document_id, *, page_no, expected_source_sha256, crop_display_pt=None):
        """Render only a registered, pinned original; recheck after rendering."""
        from .visual_evidence import render_pdf_evidence
        document = self.document(document_id)
        if document['modality'] != 'pdf':
            raise ValueError('视觉页证据仅支持PDF原件')
        if not isinstance(expected_source_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_source_sha256):
            raise ValueError('必须提供已核对的原文件SHA256')
        path = self.verify_source(document_id, expected_sha256=expected_source_sha256)
        asset = render_pdf_evidence(path.read_bytes(), page_no=page_no,
                                    expected_source_sha256=expected_source_sha256,
                                    crop_display_pt=crop_display_pt)
        self.verify_source(document_id, expected_sha256=expected_source_sha256)
        asset.manifest['document_id'] = document_id
        asset.manifest['original_uri'] = f'/api/v1/knowledge/documents/{document_id}/original'
        return asset

    def visual_table_answer(self, document_id, *, question, page_no, expected_source_sha256, crop_display_pt=None):
        from .visual_work_budget import visual_work_slot
        with visual_work_slot():
            return self._visual_table_answer(document_id, question=question, page_no=page_no,
                                             expected_source_sha256=expected_source_sha256, crop_display_pt=crop_display_pt)

    def _visual_table_answer(self, document_id, *, question, page_no, expected_source_sha256, crop_display_pt=None):
        from .visual_tables import extract_pdf_tables
        from .visual_table_reader import VisualTableReader
        if self.generator is None:
            raise ValueError('视觉表格问数需要配置指定真实模型')
        asset = self._visual_asset(document_id, page_no=page_no, expected_source_sha256=expected_source_sha256,
                                  crop_display_pt=crop_display_pt)
        path = self.verify_source(document_id, expected_sha256=expected_source_sha256)
        tables = extract_pdf_tables(path.read_bytes(), page_no=page_no, expected_source_sha256=expected_source_sha256,
                                     crop_display_pt=crop_display_pt)
        result = VisualTableReader(self.generator.client, self.ocr_pipeline).answer(question, asset, tables)
        self.verify_source(document_id, expected_sha256=expected_source_sha256)
        result['document_id'] = document_id
        result['original_uri'] = asset.manifest['original_uri']
        return result

    def records(self):
        with self.connect() as connection:
            rows = connection.execute('SELECT c.payload, d.title FROM chunks c JOIN documents d USING(document_id) ORDER BY c.rowid').fetchall()
        records = []
        from .answer_contract import substantive_numbered_heading
        for payload, title in rows:
            chunk = json.loads(payload)
            # Image resource placeholders and empty OCR outputs are never answer evidence.
            if (not chunk['text'].strip() or chunk['content_type'] == 'image'
                    or chunk['content_type'] == 'heading' and not substantive_numbered_heading(chunk['text'])):
                continue
            document_id = chunk['document_id']
            records.append(DocumentRecord(chunk['chunk_id'], title, chunk['text'],
                f'/api/v1/knowledge/documents/{document_id}/original',
                {**chunk['metadata'], 'document_id': document_id, 'chunk_id': chunk['chunk_id'],
                 'source_locator': chunk['source_locator'], 'page_no': chunk['page_no'],
                 'sheet_name': chunk['sheet_name'], 'row_start': chunk['row_start'], 'row_end': chunk['row_end'],
                 'title_path': chunk['title_path'], 'quality': chunk['quality'], 'warnings': chunk['warnings']}))
        return records

    def search(self, query: str, *, top_k: int = 4, document_id=None, page_no=None) -> list[DocumentHit]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000 or not 1 <= top_k <= 20:
            raise ValueError('问题或检索数量超出限制')
        records = self.records()
        if document_id is not None:
            document = self.document(document_id)
            records = [record for record in records if record.metadata['document_id'] == document_id]
            if page_no is not None:
                if document['modality'] != 'pdf' or type(page_no) is not int or not 1 <= page_no <= int(document['stats'].get('page_count') or 1):
                    raise ValueError('指定PDF页码超出资料范围')
                records = [record for record in records if record.metadata.get('page_no') == page_no]
        elif page_no is not None:
            raise ValueError('指定页码时必须选择PDF资料')
        # Exact entity identifiers are mandatory scope constraints, not soft synonyms.
        # Dense similarity can confuse contracts differing only in a serial number.
        normalized_query = unicodedata.normalize('NFKC', query)
        identifiers = tuple(dict.fromkeys(re.findall(
            r'(?<![A-Za-z0-9_])(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z][A-Za-z0-9_-]{2,63}(?![A-Za-z0-9_])', normalized_query)))
        if identifiers:
            def eligible(record):
                text = unicodedata.normalize('NFKC', record.title + '\n' + record.content)
                return all(re.search(r'(?<![A-Za-z0-9_])' + re.escape(identifier) + r'(?![A-Za-z0-9_])', text, re.I)
                           for identifier in identifiers)
            records = [record for record in records if eligible(record)]
            if not records:
                return []
        contents = {record.document_id: record.content for record in records}
        from .answer_contract import body_answer_affinity, substantive_numbered_heading
        navigation_contents = dict(contents)
        for previous, record in zip(records, records[1:]):
            left = re.fullmatch(r'page:(\d+):(?:block|heading):(\d+)', previous.metadata.get('source_locator', ''))
            right = re.fullmatch(r'page:(\d+):(?:block|heading):(\d+)', record.metadata.get('source_locator', ''))
            if (left and right and previous.metadata['document_id'] == record.metadata['document_id']
                    and left[1] == right[1] and int(right[2]) == int(left[2]) + 1
                    and substantive_numbered_heading(previous.content)):
                # Adjacency changes navigation only. It cannot join facts or
                # authorize a continuation; native source replay owns that.
                navigation_contents[record.document_id] = previous.content + '\n' + record.content
        page_coverage = _page_scope_coverage(records, query)
        hits = JsonDocumentRetriever(records).search(query, top_k=max(20, top_k * 5), candidate_limit=100)
        # Explicit years/numbers are grounding anchors. Title matches alone cannot satisfy them.
        anchors = tuple(dict.fromkeys(re.findall(r'(?<![\w.])\d{4}(?!\d)', query)))
        def ranking(hit):
            anchor_matches = sum(bool(re.search(r'(?<!\d)' + re.escape(anchor) + r'(?!\d)', contents[hit.document_id])) for anchor in anchors)
            raw = float(hit.metadata.get('rrf_score', hit.metadata.get('bm25_raw', 0)))
            coverage = page_coverage.get((hit.metadata['document_id'], hit.metadata.get('page_no')), 0)
            affinity = body_answer_affinity(query, navigation_contents[hit.document_id])
            return raw * (1 + 0.35 * anchor_matches) * (1 + 2 * coverage) * affinity, anchor_matches
        hits.sort(key=lambda hit: (-ranking(hit)[0], hit.document_id))
        query_language = ('en' if not re.search(r'[\u3400-\u9fff]', query)
                          and len(re.findall(r'[A-Za-z]{2,}', query)) >= 3 else 'zh_or_mixed')
        encoder_languages = (getattr(self.dense_index.embedder, 'supported_query_languages', None)
                             if self.dense_index else None)
        unsupported_language = bool(query_language == 'en' and encoder_languages
                                    and 'en' not in encoder_languages)
        if self.dense_index and not unsupported_language:
            from .dense_retrieval import reciprocal_rank_fusion
            dense = [hit for hit in self.dense_index.search(query, records, top_k=max(20, top_k * 5))
                     if hit.metadata['dense_cosine'] >= 0.35]
            hits = reciprocal_rank_fusion(hits, dense)
            hits.sort(key=lambda hit: (-ranking(hit)[0], hit.document_id))
        from .evidence_coverage import select_coverage_hits
        hits = hits[:max(20, top_k * 5)]
        coverage_selection = select_coverage_hits(query, hits, contents, ranking, top_k)
        selected, counts = [], {}
        for hit, selection_step in zip(coverage_selection.hits, coverage_selection.audit['steps']):
            source = hit.metadata['document_id']
            role = 'primary' if not selected else 'supporting'
            score, anchor_count = ranking(hit)
            selected.append(DocumentHit(hit.document_id, hit.title, hit.score, hit.matched_terms, hit.snippet, hit.source_uri,
                {**hit.metadata, 'ranking_score': score, 'anchor_match_count': anchor_count,
                 'retrieval_language_policy': {'query_language': query_language,
                     'encoder_languages': list(encoder_languages) if encoder_languages else None,
                     'strategy': 'lexical_unsupported_encoder_language' if unsupported_language else 'configured_retrieval'},
                 'page_scope_coverage': page_coverage.get((source, hit.metadata.get('page_no')), 0),
                 'page_scope_method': 'body_only_idf_coverage_rerank_not_generation_evidence',
                 'question_shape_navigation': {'method': 'generic_purpose_body_affinity_not_semantic_proof',
                     'body_affinity': body_answer_affinity(query, navigation_contents[hit.document_id])},
                 'identifier_anchors': list(identifiers), 'evidence_role': role,
                 'evidence_selection': {
                     'method': coverage_selection.audit['method'],
                     'semantic_sufficiency': 'not_evaluated',
                     'new_lexical_facets': selection_step['new_lexical_facets'],
                     'body_sha256': selection_step['body_sha256'],
                     'utility': selection_step['utility'],
                     'uncovered_lexical_facets': coverage_selection.audit['uncovered_lexical_facets'],
                     'selection_budget_exhausted': coverage_selection.audit['selection_budget_exhausted'],
                 }}))
            counts[source] = counts.get(source, 0) + 1
            if len(selected) >= top_k:
                break
        # Verify only the selected source files, not the entire corpus. Dense
        # and BM25 caches cannot turn stale excerpts into verified evidence.
        for source in counts:
            self.verify_source(source)
        return selected

    def _question_part_hits(self, question, hits, *, top_k, document_id=None, page_no=None):
        """Bounded explicit-clause navigation before answering; no model facts.

        A user's top_k is a starting retrieval budget, not permission to omit
        later subquestions. All new candidates face the original whole query
        and ordinary source reconstruction and independent answer review.
        """
        from .answer_contract import question_contract
        from .evidence_coverage import select_coverage_hits
        parts = question_contract(question)['requested_parts']
        if not 2 <= len(parts) <= 6:
            return hits, None
        scope = {**({'document_id': document_id} if document_id is not None else {}),
                 **({'page_no': page_no} if page_no is not None else {})}
        identifiers = list(dict.fromkeys(re.findall(
            r'(?<![A-Za-z0-9_])(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)'
            r'[A-Za-z][A-Za-z0-9_-]{2,63}(?![A-Za-z0-9_])', question)))
        candidates = {hit.document_id: hit for hit in hits}
        queries = []
        for part in parts:
            # A pronoun-only clause ("by whom", "its budget") must not
            # navigate unrelated documents. Retain the entire original topic
            # and filters as context; repeating the part supplies focus only.
            contextual = part + ' ' + question
            query = contextual if len(contextual) <= 1000 else part
            for identifier in identifiers:
                if not re.search(r'(?<![A-Za-z0-9_])' + re.escape(identifier) + r'(?![A-Za-z0-9_])', query, re.I):
                    query += ' ' + identifier
            if len(query) > 1000:
                continue
            queries.append(query)
            for hit in self.search(query, top_k=3, **scope):
                candidates.setdefault(hit.document_id, hit)
        records = {record.document_id: record for record in self.records()
                   if record.document_id in candidates}
        terms = set(_tokenize(question))
        rebound = [DocumentHit(hit.document_id, hit.title, hit.score,
                    tuple(sorted(terms.intersection(_tokenize(records[hit.document_id].content)))),
                    hit.snippet, hit.source_uri, hit.metadata)
                   for hit in candidates.values() if hit.document_id in records]
        rank = lambda hit: float(hit.metadata.get('ranking_score', hit.score))
        bodies = {key: record.content for key, record in records.items()}
        budget = min(MAX_EVIDENCE_ITEMS, max(top_k, 8))
        selection = select_coverage_hits(question, rebound, bodies, rank, budget)
        sources = list(dict.fromkeys(hit.metadata['document_id'] for hit in selection.hits))
        if len(sources) > 4:
            selection = select_coverage_hits(question,
                [hit for hit in rebound if hit.metadata['document_id'] in set(sources[:4])], bodies, rank, budget)
        return selection.hits, {'stage': 'explicit_question_part_retrieval',
            'question_preserved': True, 'navigation_only': True, 'model_called': False,
            'semantic_sufficiency': 'not_established', 'queries': queries,
            'candidate_count': len(candidates), 'selected_count': len(selection.hits),
            'selected_source_budget': 4, 'unselected_source_ids': sources[4:],
            'selection': selection.audit}

    def answer(self, question: str, *, top_k=4, document_id=None, page_no=None) -> dict[str, Any]:
        from .evidence_recovery import audit_boundary
        start = audit_boundary(getattr(self.generator, 'client', None))
        result = self._answer_with_recovery(question, top_k=top_k, document_id=document_id, page_no=page_no)
        from .document_parts_answer import compose_document_parts
        composed, trace = compose_document_parts(self, question, result, top_k=top_k,
            document_id=document_id, page_no=page_no, answer_audit_start=start)
        if composed is not None:
            return composed
        if trace['status'] != 'not_applicable':
            result['trace'].append(trace)
        return result

    def _answer_with_recovery(self, question: str, *, top_k=4, document_id=None, page_no=None) -> dict[str, Any]:
        if not question.strip() or len(question) > 1000:
            raise ValueError('问题为空或超出长度上限')
        from .evidence_recovery import audit_boundary, recovery_eligible, plan_evidence_recovery
        client = getattr(self.generator, 'client', None)
        answer_audit_start = audit_boundary(client)
        hits = self.search(question, top_k=top_k, **({'document_id': document_id} if document_id is not None else {}),
                           **({'page_no': page_no} if page_no is not None else {}))
        hits, part_audit = self._question_part_hits(question, hits, top_k=top_k,
                                                   document_id=document_id, page_no=page_no)
        result = self._answer_hits(question, hits, document_id=document_id, page_no=page_no)
        if part_audit is not None:
            result['trace'].insert(0, part_audit)
        if not recovery_eligible(result, client, answer_audit_start=answer_audit_start):
            return result
        navigation_sources = [{'metadata': hit.metadata} for hit in hits]
        self._verify_citation_sources(navigation_sources)
        self._verify_citation_sources(result['citations'])
        navigation_citations, navigation_omitted = self._generation_citations([
            {'citation_id': index, **hit.to_dict()} for index, hit in enumerate(hits, 1)])
        self._verify_generation_chunks(navigation_citations)
        queries, audit = plan_evidence_recovery(question, hits, client,
                                              source_contexts=navigation_citations)
        self._verify_generation_chunks(navigation_citations)
        audit['navigation_omitted'] = navigation_omitted
        self._verify_citation_sources(navigation_sources)
        if not queries:
            result['trace'].append(audit)
            self._verify_citation_sources(result['citations'])
            return result
        scope = {**({'document_id': document_id} if document_id is not None else {}),
                 **({'page_no': page_no} if page_no is not None else {})}
        candidates = {hit.document_id: hit for hit in hits}
        for query in queries:
            for hit in self.search(query, top_k=4, **scope):
                candidates.setdefault(hit.document_id, hit)
        # Rank candidate bodies against the original request, not a model's
        # guessed answer. Search queries never become evidence themselves.
        from .evidence_coverage import select_coverage_hits
        records = {record.document_id: record for record in self.records()
                   if record.document_id in candidates}
        query_terms = set(_tokenize(question))
        rebound = []
        for key, hit in candidates.items():
            body = records[key].content if key in records else ''
            matched = tuple(sorted(query_terms.intersection(_tokenize(body))))
            rebound.append(DocumentHit(hit.document_id, hit.title, hit.score, matched,
                                       hit.snippet, hit.source_uri, hit.metadata))
        selection = select_coverage_hits(question, rebound,
            {key: record.content for key, record in records.items()},
            lambda hit: float(hit.metadata.get('ranking_score', hit.score)),
            min(MAX_EVIDENCE_ITEMS, max(top_k, 8)))
        # Existing all-page visual/table scans support four selected sources.
        # Bound navigation before execution, rather than discovering an eight
        # source budget failure after discarding a usable ordinary answer.
        source_ids = list(dict.fromkeys(hit.metadata['document_id'] for hit in selection.hits))
        allowed_sources = set(source_ids[:4])
        if len(source_ids) > 4:
            selection = select_coverage_hits(question,
                [hit for hit in rebound if hit.metadata['document_id'] in allowed_sources],
                {key: record.content for key, record in records.items()},
                lambda hit: float(hit.metadata.get('ranking_score', hit.score)),
                min(MAX_EVIDENCE_ITEMS, max(top_k, 8)))
        old_ids = {hit.document_id for hit in hits}
        added = [hit.document_id for hit in selection.hits if hit.document_id not in old_ids]
        audit.update(new_chunk_ids=added, candidate_count=len(candidates),
                     selected_count=len(selection.hits), selection=selection.audit,
                     explicit_document_id=document_id, explicit_page_no=page_no,
                     selected_source_budget=4, unselected_source_ids=source_ids[4:])
        if not added:
            audit['status'] = 'no_new_source_evidence'
            result['trace'].append(audit)
            self._verify_citation_sources(result['citations'])
            return result
        # Re-run once through the SAME source, numeric, scope, and independent
        # review checks. No recursive recovery, no reuse of old model facts.
        recovered = self._answer_hits(question, selection.hits,
                                      document_id=document_id, page_no=page_no)
        audit['status'] = 'executed_once'
        recovered['evidence_recovery'] = {
            'original_question': question,
            'prior_status': result['status'], 'prior_answer_mode': result['answer_mode'],
            'prior_answer': result['answer'], 'prior_trace': result['trace'],
            'prior_generation_attempts': result.get('generation_attempts', []),
            'navigation_audit': audit,
        }
        recovered['trace'].insert(0, audit)
        self._verify_citation_sources(result['citations'])
        self._verify_citation_sources(recovered['citations'])
        return recovered

    def _answer_hits(self, question, hits, *, document_id=None, page_no=None):
        from .visual_routing import route_visual_table_question
        visual_result, visual_trace = route_visual_table_question(self, question, hits, document_id=document_id, page_no=page_no)
        if visual_result is not None:
            return visual_result
        from .visual_chart_routing import route_visual_chart_question
        chart_result, chart_trace = route_visual_chart_question(self, question, hits, document_id=document_id, page_no=page_no)
        if chart_result is not None:
            return chart_result
        from .native_table_question import route_native_table_question
        table_result, table_trace = route_native_table_question(self, question, hits, document_id=document_id, page_no=page_no)
        if table_result is not None:
            if table_result['status'] != 'ok':
                from .visual_source_answer import route_visual_source_fallback
                recovered, recovery_trace = route_visual_source_fallback(self, question, hits,
                    document_id=document_id, page_no=page_no)
                if recovered is not None:
                    recovered['trace'].insert(0, table_trace)
                    return recovered
                if recovery_trace['status'] != 'not_applicable':
                    table_result['trace'].append(recovery_trace)
            return table_result
        query_terms = set(_tokenize(question))
        # This local baseline returns attributed quotations, not inferred factual claims.
        selected = [hit for hit in hits if len(query_terms.intersection(hit.matched_terms)) >= min(2, len(query_terms))
                    or hit.metadata.get('dense_cosine', -1) >= 0.6]
        result = {'status': 'ok' if selected else 'insufficient_evidence', 'question': question,
                'answer_mode': 'attributed_extracts',
                'answer': '\n\n'.join(f'[{i}] {hit.snippet}' for i, hit in enumerate(selected, 1)) if selected else '没有足够的文档证据，暂不能回答。',
                'citations': [{'citation_id': i, **hit.to_dict()} for i, hit in enumerate(selected, 1)],
                'retrieval': self.retrieval_health(),
                'trace': [{'stage': 'document_retrieval', 'channel': self.retrieval_health()['mode'], 'candidate_count': len(hits)},
                          {'stage': 'evidence_selection', 'selected_count': len(selected), 'generation': 'extractive'}]}
        result['trace'].append(visual_trace)
        result['trace'].append(chart_trace)
        result['trace'].append(table_trace)
        if self.generator and selected:
            from .responses_client import GenerationError
            generation_citations, omitted = self._generation_citations(result['citations'])
            by_id = {hit['citation_id']: hit for hit in generation_citations}
            # Keep the public retrieval excerpt and ordering unchanged. The
            # separate field identifies the exact authoritative model input.
            result['citations'] = [by_id.get(hit['citation_id'], hit) for hit in result['citations']]
            result['trace'].append({'stage': 'generation_evidence', 'selected_count': len(generation_citations),
                                    'total_chars': sum(len(hit['generation_evidence']['text']) for hit in generation_citations),
                                    'omitted': omitted, 'max_items': MAX_EVIDENCE_ITEMS,
                                    'max_chars_per_item': MAX_EVIDENCE_CHARS,
                                    'max_native_chars_per_item': MAX_NATIVE_EVIDENCE_CHARS,
                                    'max_total_chars': MAX_TOTAL_EVIDENCE_CHARS})
            if not generation_citations:
                result['trace'].append({'stage': 'grounded_generation', 'status': 'no_safe_bounded_evidence',
                                        'fallback': 'attributed_extracts'})
                self._verify_citation_sources(result['citations'])
                return result
            from .answer_contract import question_contract
            early_contract = question_contract(question)
            multi_source_attempted = False
            early_client = self.generator.client
            if ((early_contract['multiple_requested_fields'] or early_contract['exhaustive_selection_required'])
                    and getattr(early_client, 'model', None) == 'gpt-6-luna'
                    and getattr(early_client, 'reasoning', None) == 'medium'):
                from .source_multi_span_answer import bind_multi_source_answer, replay_multi_source_proof
                multi_source_attempted = True
                try:
                    literal = bind_multi_source_answer(question, generation_citations, early_client)
                    verified = False
                    if literal['status'] == 'model_reviewed':
                        fresh, fresh_omitted = self._generation_citations(result['citations'])
                        verified = (fresh == generation_citations and fresh_omitted == omitted
                                    and replay_multi_source_proof(question, literal, fresh))
                        self._verify_generation_chunks(fresh)
                    result['trace'].append({'stage': 'evidence_first_multi_source_span',
                        'status': 'model_reviewed' if verified else 'source_replay_failed'
                            if literal['status'] == 'model_reviewed' else literal['status'],
                        'reason': literal.get('reason'), 'model_audits': literal['model_audits'],
                        'evidence_contract': 'raw_source_only_no_validated_facts'})
                    if verified:
                        result.update(status='ok', answer=literal['answer_value'], claims=[],
                            answer_mode='source_multi_span_model_reviewed',
                            answer_strategy='evidence_first_literal_multi_span', answer_span_result=literal)
                        return result
                    if literal.get('reason') in {
                            'multi_provider_failed', 'multi_selection_provider_invalid', 'multi_review_provider_invalid'}:
                        result.update(status='insufficient_evidence', answer_mode='extractive_fallback',
                            answer_completeness='multi_part_provider_unavailable')
                        return result
                finally:
                    self._verify_citation_sources(result['citations'])
                    self._verify_generation_chunks(generation_citations)
            try:
                generated = self.generator.answer(question, generation_citations)
                result.update(generated)
                result['answer_mode'] = 'model_grounded'
                result['trace'].append({'stage': 'grounded_generation', 'status': 'validated', 'model': self.generator.client.model})
            except GenerationError as exc:
                result['answer_mode'] = 'extractive_fallback'
                attempts = getattr(exc, 'generation_attempts', None)
                if attempts:
                    result['generation_attempts'] = attempts
                    result['generation_repaired'] = False
                    if attempts[-1].get('error_category') == 'incomplete_question_answer':
                        # Keep the attributed source excerpts visible, but a
                        # repeated subject title is not a completed answer.
                        # A later independently reviewed source answer may
                        # still recover this status using the same evidence.
                        result['status'] = 'insufficient_evidence'
                        result['answer_completeness'] = 'subject_only_answer_not_complete'
                result['trace'].append({'stage': 'grounded_generation', 'status': 'unavailable_or_invalid', 'fallback': 'attributed_extracts'})
            finally:
                # Neither a successful answer nor an extractive fallback may
                # silently retain evidence from a removed/replaced source.
                self._verify_citation_sources(result['citations'])
                self._verify_generation_chunks(generation_citations)
            # Recover a literal answer from the source when fact generation
            # abstains or fails semantic validation. Provider failures are not
            # evidence failures and must never trigger further API requests.
            if (result['answer_mode'] == 'extractive_fallback'
                    or result['answer_mode'] == 'model_grounded' and result.get('status') == 'insufficient_evidence'):
                from .grounded_span_answer import GroundedSpanAnswer, _completed
                from .source_span_answer import replay_source_span_proof
                from .answer_contract import question_contract
                contract = question_contract(question)
                multi_source = contract['multiple_requested_fields'] or contract['exhaustive_selection_required']
                if multi_source:
                    from .source_multi_span_answer import replay_multi_source_proof
                replay_source = replay_multi_source_proof if multi_source else replay_source_span_proof
                client = self.generator.client
                attempts = result.get('generation_attempts', [])
                audits = [attempt.get('provider_audit', {}) for attempt in attempts]
                last_audit = getattr(client, 'audit', {})
                if (getattr(client, 'model', None) == 'gpt-6-luna'
                        and getattr(client, 'reasoning', None) == 'medium'
                        and not multi_source_attempted
                        and attempts and all(attempt.get('validation_status') in {'validated', 'rejected'}
                            and attempt.get('error_category') != 'provider_unavailable' for attempt in attempts)
                        and _completed(last_audit) and all(_completed(audit) for audit in audits)):
                    try:
                        source_span = GroundedSpanAnswer(client).source_answer(question, generation_citations)
                        verified = False
                        if source_span['status'] == 'model_reviewed':
                            fresh, fresh_omitted = self._generation_citations(result['citations'])
                            verified = (fresh == generation_citations and fresh_omitted == omitted
                                        and replay_source(question, source_span, fresh))
                            self._verify_generation_chunks(fresh)
                        if verified:
                            result['prior_generation_output'] = result['answer']
                            result.update(status='ok', answer=source_span['answer_value'], claims=[],
                                answer_mode='source_multi_span_model_reviewed' if multi_source else 'source_span_model_reviewed',
                                answer_strategy='evidence_first_literal_multi_span' if multi_source else 'evidence_first_literal_source_span',
                                answer_span_result=source_span)
                            result.pop('answer_completeness', None)
                        result['trace'].append({'stage': 'evidence_first_multi_source_span' if multi_source else 'evidence_first_source_span',
                            'status': 'model_reviewed' if verified else 'source_replay_failed'
                                if source_span['status'] == 'model_reviewed' else source_span['status'],
                            'reason': source_span.get('reason'), 'model_audits': source_span['model_audits'],
                            'evidence_contract': 'raw_source_only_no_validated_facts'})
                        if source_span.get('literal_error_code') is not None:
                            result['trace'][-1]['literal_error_code'] = source_span['literal_error_code']
                        if multi_source and not verified and result['answer_mode'] == 'extractive_fallback':
                            # Attributed excerpts remain available as evidence,
                            # but are not a complete answer to a multi-part query.
                            result['status'] = 'insufficient_evidence'
                            result['answer_completeness'] = 'multi_part_literal_answer_not_verified'
                    finally:
                        self._verify_citation_sources(result['citations'])
                        self._verify_generation_chunks(generation_citations)
            if multi_source_attempted and result['answer_mode'] == 'extractive_fallback':
                result['status'] = 'insufficient_evidence'
                result['answer_completeness'] = 'multi_part_literal_answer_not_verified'
            from .answer_contract import question_contract
            display_contract = question_contract(question)
            preserve_complete_answer = (display_contract['multiple_requested_fields']
                                        or display_contract['exhaustive_selection_required'])
            if result['answer_mode'] == 'model_grounded' and result.get('claims') and preserve_complete_answer:
                result['answer_strategy'] = 'complete_reviewed_claims_without_single_span_projection'
                result['trace'].append({'stage': 'answer_display_completeness', 'status': 'full_claims_retained',
                    'reason': 'compound_or_exhaustive_answer_cannot_be_compressed_by_single_span',
                    'question_contract': display_contract})
            if result['answer_mode'] == 'model_grounded' and result.get('claims') and not preserve_complete_answer:
                def unchanged_generation_snapshot(fresh, fresh_omitted):
                    # Omissions known before generation are legitimate scope
                    # limits. Only an exactly unchanged selected snapshot and
                    # omission ledger may authorize the later projection.
                    return fresh_omitted == omitted and fresh == generation_citations
                from .typed_answer import bind_answer_slot, replay_answer_proof
                projection = bind_answer_slot(question, result['claims'], generation_citations)
                if projection['status'] == 'verified':
                    # Rehydrate from the pinned original and database, rather
                    # than replaying against the model's mutable input object.
                    fresh, fresh_omitted = self._generation_citations(result['citations'])
                    verified = (unchanged_generation_snapshot(fresh, fresh_omitted)
                                and replay_answer_proof(question, projection, fresh))
                    self._verify_citation_sources(result['citations'])
                    self._verify_generation_chunks(fresh)
                    if verified:
                        result['answer_projection'] = projection
                        result['full_fact_answer'] = result['answer']
                        result['answer'] = projection['answer_value']
                        result['answer_strategy'] = 'deterministic_typed_projection'
                    result['trace'].append({'stage': 'typed_answer_projection',
                                            'status': 'verified' if verified else 'proof_replay_failed',
                                            'complete_facts_retained': True,
                                            'full_fact_answer_field': 'full_fact_answer' if verified else 'answer'})
                else:
                    result['trace'].append({'stage': 'typed_answer_projection', 'status': 'unsupported',
                                            'reason': projection['reason'], 'original_answer_retained': True})
                    client = self.generator.client
                    if (getattr(client, 'model', None) == 'gpt-6-luna'
                            and getattr(client, 'reasoning', None) == 'medium'):
                        from .grounded_span_answer import GroundedSpanAnswer, replay_literal_proof
                        span_result = None
                        try:
                            span_result = GroundedSpanAnswer(client).answer(question, result['claims'], generation_citations)
                            if span_result['status'] == 'model_reviewed':
                                fresh, fresh_omitted = self._generation_citations(result['citations'])
                                verified = (unchanged_generation_snapshot(fresh, fresh_omitted)
                                            and replay_literal_proof(question, span_result, fresh))
                                self._verify_generation_chunks(fresh)
                                if verified:
                                    result['full_fact_answer'] = result['answer']
                                    result['answer'] = span_result['answer_value']
                                    result['answer_strategy'] = 'model_reviewed_source_span'
                                    result['answer_span_result'] = span_result
                                result['trace'].append({'stage': 'grounded_span_answer',
                                    'status': 'model_reviewed' if verified else 'literal_replay_failed',
                                    'semantic_verification': 'independent_model_review_not_formal_entailment',
                                    'model_audits': span_result['model_audits'], 'complete_facts_retained': True,
                                    'full_fact_answer_field': 'full_fact_answer' if verified else 'answer'})
                            else:
                                result['trace'].append({'stage': 'grounded_span_answer', 'status': 'unsupported',
                                    'reason': span_result['reason'], 'model_audits': span_result['model_audits'],
                                    'original_answer_retained': True})
                                if span_result.get('literal_error_code') is not None:
                                    result['trace'][-1]['literal_error_code'] = span_result['literal_error_code']
                        finally:
                            self._verify_citation_sources(result['citations'])
                            self._verify_generation_chunks(generation_citations)
            if result['answer_mode'] == 'model_grounded' and result.get('status') == 'ok':
                from .typed_span_execution import boolean_question
                span = result.get('answer_span_result') or {}
                answer = result.get('answer', '')
                if (boolean_question(question) and '%' in answer
                        and not (span.get('status') == 'model_reviewed' and span.get('answer_type') == 'boolean')):
                    result['full_fact_answer'] = answer
                    result['answer'] = '当前证据含阈值，但尚未完成对问题的可核验判断。'
                    result['status'] = 'insufficient_evidence'
                    result['answer_strategy'] = 'incomplete_boolean_abstention'
                    result['trace'].append({'stage': 'whole_boolean_answer', 'status': 'incomplete',
                        'reason': 'threshold_quote_is_not_boolean_answer', 'complete_facts_retained': True})
        if self.generator and (result['status'] != 'ok' or result['answer_mode'] == 'extractive_fallback'):
            from .visual_source_answer import route_visual_source_fallback
            recovered, recovery_trace = route_visual_source_fallback(self, question, hits,
                document_id=document_id, page_no=page_no)
            if recovered is not None:
                recovered['prior_text_answer'] = {'status':result['status'],
                    'answer_mode':result['answer_mode'], 'trace':result['trace']}
                self._verify_citation_sources(recovered['citations'])
                return recovered
            if recovery_trace['status'] != 'not_applicable':
                result['trace'].append(recovery_trace)
        return result

    def _verify_citation_sources(self, citations):
        expected = {}
        for hit in citations:
            metadata = hit['metadata']
            source, digest = metadata['document_id'], metadata['source_sha256']
            if source in expected and expected[source] != digest:
                raise SourceRevisionError('引用包含同一资料的不同版本，请重新执行。')
            expected[source] = digest
        for source, digest in expected.items():
            self.verify_source(source, expected_sha256=digest)

    def _generation_citations(self, citations):
        """Rehydrate hit chunks and bounded, original-native PDF contexts.

        A native context must be reconstructed from the pinned PDF bytes,
        never from raw_page_text or caller-provided metadata. Unverified
        table rows retain their existing rejection gate. Retrieval excerpts
        and rankings stay unchanged; model input has its own source ledger.
        """
        self._verify_citation_sources(citations)
        from .answer_contract import substantive_numbered_heading
        selected, omitted, total, originals = [], [], 0, {}
        with self.connect() as connection:
            for hit in citations:
                metadata = hit['metadata']
                row = connection.execute(
                    'SELECT c.payload,d.sha256 FROM chunks c JOIN documents d USING(document_id) '
                    'WHERE c.chunk_id=? AND c.document_id=?',
                    (metadata['chunk_id'], metadata['document_id'])).fetchone()
                if row is None or row[1] != metadata['source_sha256']:
                    raise SourceRevisionError('执行中引用的资料切片已移除或换版，请重新执行。')
                chunk = json.loads(row[0])
                if (chunk['metadata'].get('source_sha256') != row[1]
                        or chunk['source_locator'] != metadata['source_locator']
                        or chunk['page_no'] != metadata.get('page_no')
                        or hit['document_id'] != chunk['chunk_id']
                        or hit['source_uri'] != f'/api/v1/knowledge/documents/{chunk["document_id"]}/original'
                        or not hit['snippet'].strip() or hit['snippet'] not in chunk['text']):
                    raise SourceIntegrityError('检索摘录与权威切片或来源不一致。')
                reason = None
                native_eligible = (chunk['modality'] == 'pdf'
                                   and (chunk['content_type'] == 'paragraph'
                                       or chunk['content_type'] == 'heading' and substantive_numbered_heading(chunk['text']))
                                   and not chunk['metadata'].get('ocr_status'))
                if (chunk['modality'] == 'pdf' and chunk['content_type'] in {'row', 'table_row', 'table_header'}
                        and not _verified_pdf_row(chunk)):
                    reason = 'pdf_table_layout_not_verified'
                elif len(selected) >= MAX_EVIDENCE_ITEMS:
                    reason = 'evidence_item_limit'
                else:
                    limit = min(MAX_NATIVE_EVIDENCE_CHARS if native_eligible else MAX_EVIDENCE_CHARS,
                                MAX_TOTAL_EVIDENCE_CHARS - total)
                    text, end, truncated = bounded_prefix(chunk['text'], max(0, limit))
                    if not text.strip() and not native_eligible:
                        reason = 'no_complete_fact_within_budget'
                if reason:
                    omitted.append({'citation_id': hit['citation_id'], 'reason': reason})
                    continue
                native_context = None
                if native_eligible:
                    from .pdf_native_context import extract_native_page_context
                    document_id = chunk['document_id']
                    if document_id not in originals:
                        originals[document_id] = self.verify_source(document_id, expected_sha256=row[1]).read_bytes()
                    native_context = extract_native_page_context(originals[document_id], page_no=chunk['page_no'],
                                                            anchor_text=chunk['text'], max_chars=limit)
                    if native_context is None:
                        # Missing required headings, ambiguous native anchors
                        # and complete-block budget failures cannot authorize
                        # the old, smaller excerpt with its scope removed.
                        omitted.append({'citation_id': hit['citation_id'],
                                        'reason': 'native_complete_context_unavailable'})
                        continue
                    text, end, truncated = native_context['text'], None, False
                evidence = {
                    'text': text, 'source_sha256': row[1],
                    'chunk_sha256': text_sha256(chunk['text']), 'evidence_sha256': text_sha256(text),
                    'chunk_payload_sha256': text_sha256(json.dumps(chunk, ensure_ascii=False, sort_keys=True)),
                    'offset_start': None if native_context else 0, 'offset_end': end, 'truncated': truncated,
                    'source_locator': chunk['source_locator'], 'page_no': chunk['page_no'],
                    'original_chars': len(text) if native_context else len(chunk['text']), 'evidence_chars': len(text),
                    'mode': 'authoritative_pdf_native_block_context' if native_context else 'authoritative_hit_chunk_complete_prefix',
                }
                if native_context:
                    evidence['anchor_original_chars'] = len(chunk['text'])
                    evidence['native_context'] = native_context
                    evidence['native_context_max_chars'] = limit
                selected.append({**hit, 'generation_evidence': evidence})
                total += len(text)
        self._verify_citation_sources(selected)
        return selected, omitted

    def _verify_generation_chunks(self, citations):
        originals = {}
        with self.connect() as connection:
            for hit in citations:
                row = connection.execute('SELECT payload FROM chunks WHERE chunk_id=? AND document_id=?',
                    (hit['metadata']['chunk_id'], hit['metadata']['document_id'])).fetchone()
                chunk = json.loads(row[0]) if row else None
                evidence = hit['generation_evidence']
                if (chunk is None or text_sha256(chunk['text']) != evidence['chunk_sha256']
                        or text_sha256(json.dumps(chunk, ensure_ascii=False, sort_keys=True)) != evidence['chunk_payload_sha256']
                        or chunk['metadata'].get('source_sha256') != evidence['source_sha256']
                        or chunk['source_locator'] != evidence['source_locator']
                        or chunk['page_no'] != evidence['page_no']
                        or (chunk['modality'] == 'pdf' and chunk['content_type'] in {'row', 'table_row', 'table_header'}
                            and not _verified_pdf_row(chunk))):
                    raise SourceRevisionError('执行中权威资料切片发生变化，请重新执行。')
                if evidence.get('native_context') is not None:
                    from .pdf_native_context import extract_native_page_context
                    document_id = chunk['document_id']
                    if document_id not in originals:
                        originals[document_id] = self.verify_source(document_id, expected_sha256=evidence['source_sha256']).read_bytes()
                    current = extract_native_page_context(originals[document_id], page_no=chunk['page_no'],
                                                     anchor_text=chunk['text'], max_chars=evidence['native_context_max_chars'])
                    if current != evidence['native_context'] or evidence['text'] != current['text']:
                        raise SourceRevisionError('执行中原PDF上下文或原生布局发生变化，请重新执行。')
