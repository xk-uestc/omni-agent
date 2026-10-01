"""Independent, persistent document corpus with original-file provenance."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .chunk_cleaning import DocumentChunker
from .cross_source import DocumentHit, DocumentRecord, JsonDocumentRetriever, _tokenize
from .evidence_context import (MAX_EVIDENCE_CHARS, MAX_EVIDENCE_ITEMS,
                               MAX_TOTAL_EVIDENCE_CHARS, bounded_prefix, text_sha256)

SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
SUPPORTED = {'pdf', 'docx', 'xlsx', 'txt', 'md', 'image'}


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

    def ingest(self, raw: bytes, *, document_id: str, title: str, modality: str, filename: str, language='eng'):
        self.validate_id(document_id)
        if modality not in SUPPORTED or not raw or len(raw) > 20 * 1024 * 1024:
            raise ValueError('不支持的文档格式、空文件或超过 20 MiB 限制')
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
            parsed = self.chunker.parse_xlsx(raw, document_id=document_id).to_dict()
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
                if len(chunk['text']) > 900 and chunk['content_type'] not in {'image','table_row','heading'}:
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
        from .visual_tables import extract_pdf_tables
        from .visual_table_reader import VisualTableReader
        if self.generator is None:
            raise ValueError('视觉表格问数需要配置指定真实模型')
        asset = self.visual_asset(document_id, page_no=page_no, expected_source_sha256=expected_source_sha256,
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
        for payload, title in rows:
            chunk = json.loads(payload)
            # Image resource placeholders and empty OCR outputs are never answer evidence.
            if not chunk['text'].strip() or chunk['content_type'] in {'image', 'heading'}:
                continue
            document_id = chunk['document_id']
            records.append(DocumentRecord(chunk['chunk_id'], title, chunk['text'],
                f'/api/v1/knowledge/documents/{document_id}/original',
                {**chunk['metadata'], 'document_id': document_id, 'chunk_id': chunk['chunk_id'],
                 'source_locator': chunk['source_locator'], 'page_no': chunk['page_no'],
                 'sheet_name': chunk['sheet_name'], 'row_start': chunk['row_start'], 'row_end': chunk['row_end'],
                 'title_path': chunk['title_path'], 'quality': chunk['quality'], 'warnings': chunk['warnings']}))
        return records

    def search(self, query: str, *, top_k: int = 4) -> list[DocumentHit]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000 or not 1 <= top_k <= 20:
            raise ValueError('问题或检索数量超出限制')
        records = self.records()
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
        hits = JsonDocumentRetriever(records).search(query, top_k=max(20, top_k * 5))
        # Explicit years/numbers are grounding anchors. Title matches alone cannot satisfy them.
        anchors = tuple(dict.fromkeys(re.findall(r'(?<![\w.])\d{4}(?!\d)', query)))
        def ranking(hit):
            anchor_matches = sum(bool(re.search(r'(?<!\d)' + re.escape(anchor) + r'(?!\d)', contents[hit.document_id])) for anchor in anchors)
            raw = float(hit.metadata.get('rrf_score', hit.metadata.get('bm25_raw', 0)))
            return raw * (1 + 0.35 * anchor_matches), anchor_matches
        hits.sort(key=lambda hit: (-ranking(hit)[0], hit.document_id))
        if self.dense_index:
            from .dense_retrieval import reciprocal_rank_fusion
            dense = [hit for hit in self.dense_index.search(query, records, top_k=max(20, top_k * 5))
                     if hit.metadata['dense_cosine'] >= 0.35]
            hits = reciprocal_rank_fusion(hits, dense)
            hits.sort(key=lambda hit: (-ranking(hit)[0], hit.document_id))
        selected, counts = [], {}
        for hit in hits:
            source = hit.metadata['document_id']
            if counts.get(source, 0) >= 2:
                continue
            role = 'primary' if not selected else 'supporting'
            score, anchor_count = ranking(hit)
            selected.append(DocumentHit(hit.document_id, hit.title, hit.score, hit.matched_terms, hit.snippet, hit.source_uri,
                {**hit.metadata, 'ranking_score': score, 'anchor_match_count': anchor_count, 'identifier_anchors': list(identifiers), 'evidence_role': role}))
            counts[source] = counts.get(source, 0) + 1
            if len(selected) >= top_k:
                break
        # Verify only the selected source files, not the entire corpus. Dense
        # and BM25 caches cannot turn stale excerpts into verified evidence.
        for source in counts:
            self.verify_source(source)
        return selected

    def answer(self, question: str, *, top_k=4) -> dict[str, Any]:
        if not question.strip() or len(question) > 1000:
            raise ValueError('问题为空或超出长度上限')
        hits = self.search(question, top_k=top_k)
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
                                    'max_chars_per_item': MAX_EVIDENCE_CHARS, 'max_total_chars': MAX_TOTAL_EVIDENCE_CHARS})
            if not generation_citations:
                result['trace'].append({'stage': 'grounded_generation', 'status': 'no_safe_bounded_evidence',
                                        'fallback': 'attributed_extracts'})
                self._verify_citation_sources(result['citations'])
                return result
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
                result['trace'].append({'stage': 'grounded_generation', 'status': 'unavailable_or_invalid', 'fallback': 'attributed_extracts'})
            finally:
                # Neither a successful answer nor an extractive fallback may
                # silently retain evidence from a removed/replaced source.
                self._verify_citation_sources(result['citations'])
                self._verify_generation_chunks(generation_citations)
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
        """Rehydrate actual hit chunks only; never use caller-supplied full text.

        PDF rows are not expanded until geometric table/column provenance is
        reliable. This stage does not fetch whole pages, neighbors, gold or
        headings from unrelated chunks and does not alter retrieval rankings.
        """
        self._verify_citation_sources(citations)
        selected, omitted, total = [], [], 0
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
                if (chunk['modality'] == 'pdf' and chunk['content_type'] in {'row', 'table_row', 'table_header'}
                        and not _verified_pdf_row(chunk)):
                    reason = 'pdf_table_layout_not_verified'
                elif len(selected) >= MAX_EVIDENCE_ITEMS:
                    reason = 'evidence_item_limit'
                else:
                    limit = min(MAX_EVIDENCE_CHARS, MAX_TOTAL_EVIDENCE_CHARS - total)
                    text, end, truncated = bounded_prefix(chunk['text'], max(0, limit))
                    if not text.strip():
                        reason = 'no_complete_fact_within_budget'
                if reason:
                    omitted.append({'citation_id': hit['citation_id'], 'reason': reason})
                    continue
                selected.append({**hit, 'generation_evidence': {
                    'text': text, 'source_sha256': row[1],
                    'chunk_sha256': text_sha256(chunk['text']), 'evidence_sha256': text_sha256(text),
                    'offset_start': 0, 'offset_end': end, 'truncated': truncated,
                    'source_locator': chunk['source_locator'], 'page_no': chunk['page_no'],
                    'original_chars': len(chunk['text']), 'evidence_chars': len(text),
                    'mode': 'authoritative_hit_chunk_complete_prefix',
                }})
                total += len(text)
        self._verify_citation_sources(selected)
        return selected, omitted

    def _verify_generation_chunks(self, citations):
        with self.connect() as connection:
            for hit in citations:
                row = connection.execute('SELECT payload FROM chunks WHERE chunk_id=? AND document_id=?',
                    (hit['metadata']['chunk_id'], hit['metadata']['document_id'])).fetchone()
                chunk = json.loads(row[0]) if row else None
                evidence = hit['generation_evidence']
                if (chunk is None or text_sha256(chunk['text']) != evidence['chunk_sha256']
                        or chunk['metadata'].get('source_sha256') != evidence['source_sha256']
                        or chunk['source_locator'] != evidence['source_locator']
                        or chunk['page_no'] != evidence['page_no']
                        or (chunk['modality'] == 'pdf' and chunk['content_type'] in {'row', 'table_row', 'table_header'}
                            and not _verified_pdf_row(chunk))):
                    raise SourceRevisionError('执行中权威资料切片发生变化，请重新执行。')
