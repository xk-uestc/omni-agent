"""Independent, persistent document corpus with original-file provenance."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from copy import deepcopy
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
    'it its please tell me can could would should according document report '
    '怎么 如何 什么 哪些 哪个 是否 可以 能否 能不能 可不可以 请问 加入 添加 放入 倒入'.split())


def _publish_rag_progress(callback, step, status, summary, *, input=None, output=None,
                          latency_ms=None, executed=None):
    if callback is None:
        return
    event = {'stage': 'rag_process', 'tool': f'rag.{step}', 'rag_step': step,
             'status': status, 'summary': summary, 'input': input or {}, 'output': output or {}}
    if latency_ms is not None:
        event['latency_ms'] = round(float(latency_ms), 3)
    if executed is not None:
        event['executed'] = executed
    try:
        callback(event)
    except Exception:
        pass


def _page_scope_coverage(records, query):
    """Body-only, IDF-weighted coverage of each original document page.

    Page neighbors affect ranking only. They never become model evidence or
    override explicit identifier filters. Titles and metadata do not establish
    the presence of a requested entity/year in the original page body.
    """
    pages = {}
    for record in records:
        page_no = record.metadata.get('page_no')
        if type(page_no) is not int or page_no < 1:
            continue
        key = (record.metadata['document_id'], page_no)
        pages.setdefault(key, set()).update(_tokenize(record.content))
    terms = set(_tokenize(query)) - _RETRIEVAL_STOP_WORDS
    if not terms or not pages:
        return {}
    frequencies = Counter(term for page in pages.values() for term in page if term in terms)
    weights = {term: log(1 + (len(pages) + .5) / (frequencies[term] + .5)) for term in terms}
    denominator = sum(weights.values())
    return {key: sum(weights[term] for term in terms.intersection(body)) / denominator
            for key, body in pages.items()}


def _query_term_weights(retriever, query):
    """Weight specific query terms for non-paginated chunk navigation."""
    terms = set(_tokenize(query)) - _RETRIEVAL_STOP_WORDS
    if not terms:
        return {}, 0.0
    frequencies = retriever._document_frequency
    weights = {term: log(1 + (len(retriever.documents) + .5) / (frequencies[term] + .5))
               for term in terms}
    return weights, sum(weights.values())


def _matched_query_term_coverage(hit, weights, denominator):
    if not denominator:
        return 0.0
    return sum(weights.get(term, 0.0) for term in set(hit.matched_terms)) / denominator


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

    def warm_dense_index(self):
        if self.dense_index is None:
            return 0
        records = self.records()
        if records:
            self.dense_index.vectors(records)
            self.dense_index.embedder.embed(['启动时预热检索编码器'], query=True)
        return len(records)

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

    def delete(self, document_id, *, expected_sha256):
        """Remove a pinned logical revision atomically; keep immutable originals.

        This internal store operation introduces no public deletion endpoint.
        Old handles fail source validation; shared content-addressed assets and
        audit evidence are never removed by logical deletion.
        """
        self.validate_id(document_id)
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
            raise ValueError('删除资料必须指定当前来源SHA256')
        with self.connect() as connection:
            cursor = connection.execute('DELETE FROM documents WHERE document_id=? AND sha256=?',
                                        (document_id, expected_sha256))
            if cursor.rowcount != 1:
                raise SourceRevisionError('资料已移除或版本变化，未删除当前版本。')

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

    def records(self, document_id=None):
        if document_id is not None:
            self.validate_id(document_id)
        with self.connect() as connection:
            query = 'SELECT c.payload, d.title FROM chunks c JOIN documents d USING(document_id)'
            params = ()
            if document_id is not None:
                query += ' WHERE c.document_id=?'
                params = (document_id,)
            rows = connection.execute(query + ' ORDER BY c.rowid', params).fetchall()
        records = []
        from .answer_contract import substantive_numbered_heading
        from .raysource_media import image_refs_for_chunk
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
                 'title_path': chunk['title_path'], 'quality': chunk['quality'], 'warnings': chunk['warnings'],
                 'raysource_images': image_refs_for_chunk(document_id, chunk['text'])}))
        return records

    def search(self, query: str, *, top_k: int = 4, document_id=None, page_no=None,
               with_audit: bool = False, progress_callback=None) -> list[DocumentHit] | tuple[list[DocumentHit], dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000 or not 1 <= top_k <= 20:
            raise ValueError('问题或检索数量超出限制')
        binding_started = time.perf_counter()
        def finish(hits, audit):
            return (hits, audit) if with_audit else hits

        records = self.records(document_id) if document_id is not None else self.records()
        if document_id is not None:
            document = self.document(document_id)
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
        _publish_rag_progress(progress_callback, 'source_binding', 'success',
            f"已限定到 {len({record.metadata['document_id'] for record in records})} 份资料、{len(records)} 个可检索片段。",
            input={'document_id': document_id, 'page_no': page_no, 'exact_identifiers': list(identifiers)},
            output={'document_count': len({record.metadata['document_id'] for record in records}),
                    'eligible_chunk_count': len(records), 'scope': 'selected_source' if document_id else 'all_sources'},
            latency_ms=(time.perf_counter() - binding_started) * 1000,
            executed=True)
        if identifiers and not records:
            _publish_rag_progress(progress_callback, 'bm25_retrieval', 'skipped',
                '精确标识范围内没有匹配片段，未执行关键词召回。', executed=False)
            _publish_rag_progress(progress_callback, 'dense_retrieval', 'skipped',
                '没有可检索片段，未执行向量召回。', executed=False)
            _publish_rag_progress(progress_callback, 'hybrid_ranking', 'skipped',
                '没有召回候选，未执行融合排序。', executed=False)
            _publish_rag_progress(progress_callback, 'evidence_selection', 'attention',
                '没有候选片段可供证据选择。', output={'candidate_count': 0, 'selected_count': 0}, executed=True)
            return finish([], {'version': 'ict8-rag-audit-v1', 'query': query,
                'scope': {'document_id': document_id, 'page_no': page_no,
                          'exact_identifiers': list(identifiers)},
                'retrieval': self.retrieval_health(), 'candidate_count': 0,
                'dense_status': 'not_run_no_exact_identifier_match',
                'channels': [], 'selected_count': 0, 'candidates': [],
                'termination': 'exact_identifier_scope_had_no_matching_chunks'})
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
        retriever = JsonDocumentRetriever(records)
        query_term_weights, query_term_weight_total = _query_term_weights(retriever, query)
        bm25_started = time.perf_counter()
        _publish_rag_progress(progress_callback, 'bm25_retrieval', 'running',
            '正在按词项相关度查找手册片段。', input={'query': query, 'candidate_limit': 100})
        try:
            bm25_hits = retriever.search(query, top_k=max(20, top_k * 5), candidate_limit=100)
        except Exception:
            _publish_rag_progress(progress_callback, 'bm25_retrieval', 'error',
                '关键词召回执行失败。', latency_ms=(time.perf_counter() - bm25_started) * 1000,
                executed=False)
            raise
        _publish_rag_progress(progress_callback, 'bm25_retrieval', 'success',
            f'关键词召回得到 {len(bm25_hits)} 个候选片段。',
            output={'candidate_count': len(bm25_hits)},
            latency_ms=(time.perf_counter() - bm25_started) * 1000, executed=True)
        bm25_ranks = {hit.document_id: rank for rank, hit in enumerate(bm25_hits, 1)}
        bm25_hits = [DocumentHit(hit.document_id, hit.title, hit.score, hit.matched_terms,
            hit.snippet, hit.source_uri, {**hit.metadata, 'bm25_rank': bm25_ranks[hit.document_id]})
            for hit in bm25_hits]
        hits = bm25_hits
        # Explicit years/numbers are grounding anchors. Title matches alone cannot satisfy them.
        anchors = tuple(dict.fromkeys(re.findall(r'(?<![\w.])\d{4}(?!\d)', query)))
        def ranking(hit):
            anchor_matches = sum(bool(re.search(r'(?<!\d)' + re.escape(anchor) + r'(?!\d)', contents[hit.document_id])) for anchor in anchors)
            raw = float(hit.metadata.get('rrf_score', hit.metadata.get('bm25_raw', 0)))
            coverage = page_coverage.get((hit.metadata['document_id'], hit.metadata.get('page_no')), 0)
            body_term_coverage = (_matched_query_term_coverage(hit, query_term_weights, query_term_weight_total)
                                  if hit.metadata.get('page_no') is None else 0.0)
            affinity = body_answer_affinity(query, navigation_contents[hit.document_id])
            return (raw * (1 + 0.35 * anchor_matches) * (1 + 2 * coverage)
                    * (1 + 1.5 * body_term_coverage) * affinity,
                    anchor_matches, body_term_coverage)
        query_language = ('en' if not re.search(r'[\u3400-\u9fff]', query)
                          and len(re.findall(r'[A-Za-z]{2,}', query)) >= 3 else 'zh_or_mixed')
        encoder_languages = (getattr(self.dense_index.embedder, 'supported_query_languages', None)
                             if self.dense_index else None)
        unsupported_language = bool(query_language == 'en' and encoder_languages
                                    and 'en' not in encoder_languages)
        dense_status = ('skipped_unsupported_query_language' if unsupported_language else
                        'enabled' if self.dense_index else 'not_configured')
        dense = []
        dense_started = time.perf_counter()
        if self.dense_index and not unsupported_language:
            _publish_rag_progress(progress_callback, 'dense_retrieval', 'running',
                '正在计算问题与片段的向量相似度。',
                input={'query': query, 'embedding_model': self.dense_index.embedder.identity})
            from .dense_retrieval import reciprocal_rank_fusion
            try:
                dense = self.dense_index.search(query, records, top_k=max(20, top_k * 5))
            except Exception:
                _publish_rag_progress(progress_callback, 'dense_retrieval', 'error',
                    '向量召回执行失败。', latency_ms=(time.perf_counter() - dense_started) * 1000,
                    executed=False)
                raise
            _publish_rag_progress(progress_callback, 'dense_retrieval', 'success',
                f'向量召回得到 {len(dense)} 个候选片段。',
                output={'candidate_count': len(dense), 'embedding_model': self.dense_index.embedder.identity},
                latency_ms=(time.perf_counter() - dense_started) * 1000, executed=True)
            hits = reciprocal_rank_fusion(bm25_hits, dense)
            hits = [DocumentHit(hit.document_id, hit.title, hit.score, hit.matched_terms,
                hit.snippet, hit.source_uri,
                {**hit.metadata, 'bm25_rank': bm25_ranks.get(hit.document_id)}) for hit in hits]
        else:
            reason = ('当前向量模型不支持本轮查询语言' if unsupported_language else 'Dense 向量索引未配置')
            _publish_rag_progress(progress_callback, 'dense_retrieval', 'skipped',
                reason + '，本轮保留关键词召回。',
                output={'dense_status': dense_status}, executed=False)
        ranking_started = time.perf_counter()
        _publish_rag_progress(progress_callback, 'hybrid_ranking', 'running',
            '正在融合召回名次，并按明确的年份、页范围和正文覆盖调整顺序。',
            input={'dense_status': dense_status})
        hits.sort(key=lambda hit: (-ranking(hit)[0], hit.document_id))
        _publish_rag_progress(progress_callback, 'hybrid_ranking', 'success',
            '融合排序完成。' if dense_status == 'enabled' else 'BM25 候选已按页范围与正文覆盖重排；本轮未执行 RRF。',
            output={'dense_status': dense_status, 'rrf_executed': dense_status == 'enabled',
                    'ranking_method': ('rrf_then_page_scope_and_body_affinity' if dense_status == 'enabled'
                                       else 'bm25_then_page_scope_and_body_affinity')},
            latency_ms=(time.perf_counter() - ranking_started) * 1000, executed=True)
        selection_started = time.perf_counter()
        _publish_rag_progress(progress_callback, 'evidence_selection', 'running',
            '正在应用资料范围约束，并裁剪相关证据片段。',
            input={'candidate_count': len(hits), 'selection_limit': top_k})
        from .evidence_coverage import select_coverage_hits
        hits = hits[:max(20, top_k * 5)]
        coverage_selection = select_coverage_hits(query, hits, contents, ranking, top_k)
        selected_evidence = [{
            'title': hit.title,
            'chunk_id': hit.metadata.get('chunk_id'),
            'source_locator': hit.metadata.get('source_locator'),
            'page_no': hit.metadata.get('page_no'),
            'snippet': str(hit.snippet or '')[:320],
            'image_count': len(hit.metadata.get('raysource_images') or []),
        } for hit in coverage_selection.hits]
        _publish_rag_progress(progress_callback, 'evidence_selection', 'success',
            f'从 {len(hits)} 个排序候选中选出 {len(coverage_selection.hits)} 个证据片段。',
            output={'candidate_count': len(hits), 'selected_count': len(coverage_selection.hits),
                    'selection_method': coverage_selection.audit.get('method'),
                    'selected_evidence': selected_evidence},
            latency_ms=(time.perf_counter() - selection_started) * 1000, executed=True)
        steps_by_id = {str(step['chunk_id']): step for step in coverage_selection.audit['steps']}
        selected_roles = {hit.document_id: ('primary' if index == 0 else 'supporting')
                          for index, hit in enumerate(coverage_selection.hits)}
        retrieval_audit = {
            'version': 'ict8-rag-audit-v1',
            'query': query,
            'scope': {'document_id': document_id, 'page_no': page_no,
                      'exact_identifiers': list(identifiers)},
            'retrieval': self.retrieval_health(),
            'query_language': query_language,
            'dense_status': dense_status,
            'channels': ['bm25', 'dense', 'rrf'] if dense_status == 'enabled' else ['bm25'],
            'ranking_method': ('true_channel_rrf_then_idf_query_coverage_page_scope_and_body_affinity'
                               if dense_status == 'enabled' else
                               'bm25_then_idf_query_coverage_page_scope_and_body_affinity'),
            'candidate_pool_limit': max(20, top_k * 5),
            'candidate_count': len(hits),
            'selection_budget': top_k,
            'selected_count': len(coverage_selection.hits),
            'selection': coverage_selection.audit,
            'candidates': [],
        }
        for rank, hit in enumerate(hits, 1):
            metadata = hit.metadata
            selected_step = steps_by_id.get(str(hit.document_id))
            retrieval_audit['candidates'].append({
                'chunk_id': hit.document_id,
                'source_document_id': metadata.get('document_id'),
                'source_sha256': metadata.get('source_sha256'),
                'title': hit.title,
                'snippet': hit.snippet,
                'source_locator': metadata.get('source_locator'),
                'page_no': metadata.get('page_no'),
                'sheet_name': metadata.get('sheet_name'),
                'row_start': metadata.get('row_start'),
                'row_end': metadata.get('row_end'),
                'ranking_rank': rank,
                'bm25_rank': metadata.get('bm25_rank'),
                'bm25_raw': metadata.get('bm25_raw'),
                'bm25_relative': metadata.get('bm25_relative'),
                'dense_rank': metadata.get('dense_rank'),
                'dense_cosine': metadata.get('dense_cosine'),
                'rrf_rank': metadata.get('rrf_rank'),
                'rrf_score': metadata.get('rrf_score'),
                'ranking_score': ranking(hit)[0],
                'body_query_term_coverage': ranking(hit)[2],
                'matched_terms': list(hit.matched_terms),
                'retrieval_channels': metadata.get('retrieval_channels',
                    [metadata.get('retrieval_channel', 'bm25')]),
                'selected_in_initial_coverage': hit.document_id in selected_roles,
                'selection_role': selected_roles.get(hit.document_id),
                'selection_utility': selected_step.get('utility') if selected_step else None,
                'new_lexical_facets': selected_step.get('new_lexical_facets', []) if selected_step else [],
                'selection_note': ('coverage_selector_selected' if selected_step else 'not_selected_by_coverage_selector'),
                'evidence_status': 'retrieval_candidate_not_fact_verification',
            })
        selected, counts = [], {}
        for hit, selection_step in zip(coverage_selection.hits, coverage_selection.audit['steps']):
            source = hit.metadata['document_id']
            role = 'primary' if not selected else 'supporting'
            score, anchor_count, body_term_coverage = ranking(hit)
            selected.append(DocumentHit(hit.document_id, hit.title, hit.score, hit.matched_terms, hit.snippet, hit.source_uri,
                {**hit.metadata, 'ranking_score': score, 'anchor_match_count': anchor_count,
                 'retrieval_language_policy': {'query_language': query_language,
                     'encoder_languages': list(encoder_languages) if encoder_languages else None,
                     'strategy': 'lexical_unsupported_encoder_language' if unsupported_language else 'configured_retrieval'},
                 'page_scope_coverage': page_coverage.get((source, hit.metadata.get('page_no')), 0),
                 'page_scope_method': 'body_only_idf_coverage_rerank_not_generation_evidence',
                 'body_query_term_coverage': body_term_coverage,
                 'body_query_term_method': ('idf_weighted_query_term_coverage_for_non_paginated_chunks'
                                            if hit.metadata.get('page_no') is None else 'not_applied_to_paginated_sources'),
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
        return finish(selected, retrieval_audit)

    @staticmethod
    def _attach_retrieval_audit(result, retrieval_audit):
        if not isinstance(retrieval_audit, dict):
            return result
        candidates = retrieval_audit.setdefault('candidates', [])
        by_chunk = {str(item.get('chunk_id')): item for item in candidates}
        for citation in result.get('citations', []):
            metadata = citation.get('metadata') or {}
            chunk_id = str(metadata.get('chunk_id') or citation.get('document_id') or '')
            if not chunk_id:
                continue
            candidate = by_chunk.get(chunk_id)
            if candidate is None:
                candidate = {
                    'chunk_id': chunk_id,
                    'source_document_id': metadata.get('document_id'),
                    'source_sha256': metadata.get('source_sha256'),
                    'title': citation.get('title', ''),
                    'snippet': citation.get('snippet', ''),
                    'source_locator': metadata.get('source_locator'),
                    'page_no': metadata.get('page_no'),
                    'sheet_name': metadata.get('sheet_name'),
                    'row_start': metadata.get('row_start'),
                    'row_end': metadata.get('row_end'),
                    'ranking_rank': None,
                    'bm25_rank': metadata.get('bm25_rank'),
                    'bm25_raw': metadata.get('bm25_raw'),
                    'bm25_relative': metadata.get('bm25_relative'),
                    'dense_rank': metadata.get('dense_rank'),
                    'dense_cosine': metadata.get('dense_cosine'),
                    'rrf_rank': metadata.get('rrf_rank'),
                    'rrf_score': metadata.get('rrf_score'),
                    'ranking_score': metadata.get('ranking_score'),
                    'matched_terms': list(citation.get('matched_terms') or []),
                    'retrieval_channels': metadata.get('retrieval_channels',
                        [metadata.get('retrieval_channel', 'unknown')]),
                    'selected_in_initial_coverage': False,
                    'selection_role': metadata.get('evidence_role') or 'later_evidence_selection',
                    'selection_utility': None,
                    'new_lexical_facets': [],
                    'selection_note': 'final_citation_added_after_initial_candidate_selection',
                    'evidence_status': 'final_citation_source_checked_separately',
                }
                candidates.append(candidate)
                by_chunk[chunk_id] = candidate
            citation_ids = candidate.setdefault('final_citation_ids', [])
            citation_id = citation.get('citation_id')
            if citation_id is not None and citation_id not in citation_ids:
                citation_ids.append(citation_id)
        result['retrieval_audit'] = retrieval_audit
        return result

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

    def answer(self, question: str, *, top_k=4, document_id=None, page_no=None,
               audit_callback=None, progress_callback=None, context_resolution=None,
               image_attachments=None, retrieval_context=None) -> dict[str, Any]:
        from .evidence_recovery import audit_boundary
        start = audit_boundary(getattr(self.generator, 'client', None))
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise ValueError('问题为空或超出长度上限')
        question_analysis_started = time.perf_counter()
        _publish_rag_progress(progress_callback, 'question_analysis', 'running',
            '正在整理本轮问题与检索目标。', input={'question': question})
        retrieval_query = question
        if isinstance(retrieval_context, str) and retrieval_context.strip():
            extra = retrieval_context.strip()
            retrieval_query = (question + '\n' + extra[:max(0, 1000 - len(question) - 1)])[:1000]
        _publish_rag_progress(progress_callback, 'question_analysis', 'success',
            '检索文本已整理；BM25 与 Dense 使用同一条本轮检索问题。',
            output={'question': question, 'retrieval_query': retrieval_query,
                    'query_rewrite': 'none'},
            latency_ms=(time.perf_counter() - question_analysis_started) * 1000, executed=True)
        context_started = time.perf_counter()
        context = context_resolution if isinstance(context_resolution, dict) else {'mode': 'current_question_only'}
        mode = context.get('mode')
        context_summary = ('已通过服务端校验并绑定上一轮来源。'
            if mode in {'server_verified_dialogue_source', 'server_verified_sql_entity_document_followup'}
            else '已采用本轮确定的上下文问题。'
            if mode == 'verified_context_resolution'
            else '本轮附带图片或检索上下文，与文字问题共同处理。'
            if image_attachments or retrieval_context
            else '按本轮完整问题处理；没有沿用未经核验的历史文档来源。')
        context_output = {key: context[key] for key in
            ('mode', 'source_question', 'actual_question', 'effective_question', 'context_turns', 'document_id', 'entity')
            if key in context and isinstance(context[key], (str, int, float, bool, type(None)))}
        _publish_rag_progress(progress_callback, 'context_resolution', 'running',
            '正在核对多轮上下文、指代和附加输入。',
            input={'mode': mode or 'current_question_only'})
        _publish_rag_progress(progress_callback, 'context_resolution', 'success', context_summary,
            output={**context_output, 'image_count': len(image_attachments or []),
                    'retrieval_context_attached': bool(retrieval_context)},
            latency_ms=(time.perf_counter() - context_started) * 1000, executed=True)
        _publish_rag_progress(progress_callback, 'source_binding', 'running',
            '正在应用资料范围和页码限制。',
            input={'document_id': document_id, 'page_no': page_no,
                   'scope': 'selected_source' if document_id else 'all_sources'})
        result = self._answer_with_recovery(question, top_k=top_k, document_id=document_id,
                                            page_no=page_no, audit_callback=audit_callback,
                                            progress_callback=progress_callback,
                                            image_attachments=image_attachments,
                                            retrieval_context=retrieval_context,
                                            retrieval_query=retrieval_query)
        from .document_parts_answer import compose_document_parts
        completion_started = time.perf_counter()
        _publish_rag_progress(progress_callback, 'completion', 'running',
            '正在整理最终回答与来源信息。')
        composed, trace = compose_document_parts(self, question, result, top_k=top_k,
            document_id=document_id, page_no=page_no, answer_audit_start=start,
            image_attachments=image_attachments, retrieval_context=retrieval_context)
        if composed is not None:
            result = composed
        elif trace['status'] != 'not_applicable':
            result['trace'].append(trace)
        _publish_rag_progress(progress_callback, 'completion',
            'success' if result.get('status') == 'ok' else 'attention',
            '文档处理已完成。' if result.get('status') == 'ok' else '处理已结束，当前结果需要补充条件或证据。',
            output={'status': result.get('status'), 'answer_mode': result.get('answer_mode'),
                    'citation_count': len(result.get('citations') or [])},
            latency_ms=(time.perf_counter() - completion_started) * 1000, executed=True)
        return result

    def _answer_with_recovery(self, question: str, *, top_k=4, document_id=None, page_no=None,
                              audit_callback=None, progress_callback=None, image_attachments=None,
                              retrieval_context=None, retrieval_query=None) -> dict[str, Any]:
        if not question.strip() or len(question) > 1000:
            raise ValueError('问题为空或超出长度上限')
        from .evidence_recovery import audit_boundary, recovery_eligible, plan_evidence_recovery
        client = getattr(self.generator, 'client', None)
        answer_audit_start = audit_boundary(client)
        if retrieval_query is None:
            question_analysis_started = time.perf_counter()
            retrieval_query = question
            if isinstance(retrieval_context, str) and retrieval_context.strip():
                extra = retrieval_context.strip()
                retrieval_query = (question + '\n' + extra[:max(0, 1000 - len(question) - 1)])[:1000]
            _publish_rag_progress(progress_callback, 'question_analysis', 'success',
                '检索文本已整理；BM25 与 Dense 使用同一条本轮检索问题。',
                output={'question': question, 'retrieval_query': retrieval_query,
                        'query_rewrite': 'none'},
                latency_ms=(time.perf_counter() - question_analysis_started) * 1000, executed=True)
        hits, retrieval_audit = self.search(retrieval_query, top_k=top_k,
                           **({'document_id': document_id} if document_id is not None else {}),
                           **({'page_no': page_no} if page_no is not None else {}), with_audit=True,
                           progress_callback=progress_callback)
        hits, part_audit = self._question_part_hits(question, hits, top_k=top_k,
                                                   document_id=document_id, page_no=page_no)
        if part_audit is not None:
            retrieval_audit['question_part_navigation'] = part_audit
            _publish_rag_progress(progress_callback, 'context_resolution', 'success',
                f"问题拆分导航完成，追加 {len(part_audit.get('queries') or [])} 条检索查询。",
                output={'question_parts': list(part_audit.get('queries') or []),
                        'part_candidate_count': part_audit.get('candidate_count', 0),
                        'part_selected_count': part_audit.get('selected_count', 0),
                        'navigation_only': True}, executed=True)
        if audit_callback is not None:
            try:
                audit_callback({'phase': 'retrieval_complete',
                                'retrieval_audit': deepcopy(retrieval_audit)})
            except Exception:
                pass
        evidence_started = time.perf_counter()
        _publish_rag_progress(progress_callback, 'generation_evidence', 'running',
            '正在整理选中片段、原文定位和关联图片。',
            input={'selected_count': min(len(hits), MAX_EVIDENCE_ITEMS)})
        model_evidence = [{
            'title': hit.title,
            'chunk_id': hit.metadata.get('chunk_id'),
            'source_locator': hit.metadata.get('source_locator'),
            'page_no': hit.metadata.get('page_no'),
            'snippet': str(hit.snippet or '')[:320],
            'image_count': len(hit.metadata.get('raysource_images') or []),
        } for hit in hits[:MAX_EVIDENCE_ITEMS]]
        _publish_rag_progress(progress_callback, 'generation_evidence', 'success',
            f'已整理 {len(model_evidence)} 个入选片段，交由回答与来源核验流程处理。',
            output={'evidence_count': len(model_evidence), 'evidence': model_evidence,
                    'user_image_count': len(image_attachments or [])},
            latency_ms=(time.perf_counter() - evidence_started) * 1000, executed=True)
        generation_started = time.perf_counter()
        _publish_rag_progress(progress_callback, 'answer_generation', 'running',
            '正在依据入选证据生成或核验回答。',
            input={'question': question, 'evidence_count': len(model_evidence)})
        try:
            result = self._answer_hits(question, hits, document_id=document_id, page_no=page_no,
                                       image_attachments=image_attachments)
        except Exception:
            _publish_rag_progress(progress_callback, 'answer_generation', 'error',
                '答案生成或来源核验执行失败。',
                latency_ms=(time.perf_counter() - generation_started) * 1000, executed=False)
            raise
        generation_ok = result.get('status') == 'ok'
        _publish_rag_progress(progress_callback, 'answer_generation',
            'success' if generation_ok else 'attention',
            ('已返回回答并完成来源核验。' if generation_ok else '回答处理完成，但当前证据不足或需要补充条件。'),
            output={'status': result.get('status'), 'answer_mode': result.get('answer_mode'),
                    'citation_count': len(result.get('citations') or [])},
            latency_ms=(time.perf_counter() - generation_started) * 1000, executed=True)
        if part_audit is not None:
            result['trace'].insert(0, part_audit)
        if not recovery_eligible(result, client, answer_audit_start=answer_audit_start):
            return self._attach_retrieval_audit(result, retrieval_audit)
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
            retrieval_audit['evidence_recovery_navigation'] = audit
            return self._attach_retrieval_audit(result, retrieval_audit)
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
            retrieval_audit['evidence_recovery_navigation'] = audit
            return self._attach_retrieval_audit(result, retrieval_audit)
        # Re-run once through the SAME source, numeric, scope, and independent
        # review checks. No recursive recovery, no reuse of old model facts.
        recovered = self._answer_hits(question, selection.hits,
                                      document_id=document_id, page_no=page_no,
                                      image_attachments=image_attachments)
        audit['status'] = 'executed_once'
        retrieval_audit['evidence_recovery_navigation'] = audit
        candidate_by_id = {str(item.get('chunk_id')): item
                           for item in retrieval_audit.get('candidates', [])}
        for recovery_rank, hit in enumerate(selection.hits, 1):
            metadata = hit.metadata
            candidate = candidate_by_id.get(str(hit.document_id))
            if candidate is None:
                candidate = {
                    'chunk_id': hit.document_id,
                    'source_document_id': metadata.get('document_id'),
                    'source_sha256': metadata.get('source_sha256'),
                    'title': hit.title,
                    'snippet': hit.snippet,
                    'source_locator': metadata.get('source_locator'),
                    'page_no': metadata.get('page_no'),
                    'sheet_name': metadata.get('sheet_name'),
                    'row_start': metadata.get('row_start'),
                    'row_end': metadata.get('row_end'),
                    'ranking_rank': None,
                    'bm25_rank': metadata.get('bm25_rank'),
                    'bm25_raw': metadata.get('bm25_raw'),
                    'bm25_relative': metadata.get('bm25_relative'),
                    'dense_rank': metadata.get('dense_rank'),
                    'dense_cosine': metadata.get('dense_cosine'),
                    'rrf_rank': metadata.get('rrf_rank'),
                    'rrf_score': metadata.get('rrf_score'),
                    'ranking_score': metadata.get('ranking_score'),
                    'matched_terms': list(hit.matched_terms),
                    'retrieval_channels': metadata.get('retrieval_channels',
                        [metadata.get('retrieval_channel', 'unknown')]),
                    'selected_in_initial_coverage': False,
                    'selection_role': None,
                    'selection_utility': None,
                    'new_lexical_facets': [],
                    'selection_note': 'selected_by_evidence_recovery_navigation',
                    'evidence_status': 'retrieval_candidate_not_fact_verification',
                }
                retrieval_audit['candidates'].append(candidate)
                candidate_by_id[str(hit.document_id)] = candidate
            candidate['evidence_recovery_rank'] = recovery_rank
            candidate['evidence_recovery_role'] = 'primary' if recovery_rank == 1 else 'supporting'
        if audit_callback is not None:
            try:
                audit_callback({'phase': 'recovery_retrieval_complete',
                                'retrieval_audit': deepcopy(retrieval_audit)})
            except Exception:
                pass
        recovered['evidence_recovery'] = {
            'original_question': question,
            'prior_status': result['status'], 'prior_answer_mode': result['answer_mode'],
            'prior_answer': result['answer'], 'prior_trace': result['trace'],
            'prior_generation_attempts': result.get('generation_attempts', []),
            'navigation_audit': audit,
        }
        recovered['trace'].insert(0, audit)
        self._attach_retrieval_audit(recovered, retrieval_audit)
        self._verify_citation_sources(result['citations'])
        self._verify_citation_sources(recovered['citations'])
        return recovered

    def _answer_hits(self, question, hits, *, document_id=None, page_no=None, image_attachments=None):
        from .native_row_selection import route_native_row_selection
        selection_result, selection_trace = ((None, {'stage': 'native_row_selection', 'status': 'not_applicable'})
            if image_attachments else route_native_row_selection(self, question, hits, document_id=document_id, page_no=page_no))
        if selection_result is not None:
            return selection_result
        from .native_row_comparison import route_native_row_comparison
        row_result, row_trace = ((None, {'stage': 'native_row_comparison', 'status': 'not_applicable'})
            if image_attachments else route_native_row_comparison(self, question, hits, document_id=document_id, page_no=page_no))
        if row_result is not None:
            return row_result
        from .visual_routing import route_visual_table_question
        visual_result, visual_trace = ((None, {'stage': 'visual_table_routing', 'status': 'not_applicable'})
            if image_attachments else route_visual_table_question(self, question, hits, document_id=document_id, page_no=page_no))
        if visual_result is not None:
            return visual_result
        from .visual_chart_routing import route_visual_chart_question
        chart_result, chart_trace = ((None, {'stage': 'visual_chart_routing', 'status': 'not_applicable'})
            if image_attachments else route_visual_chart_question(self, question, hits, document_id=document_id, page_no=page_no))
        if chart_result is not None:
            return chart_result
        from .native_table_question import route_native_table_question
        table_result, table_trace = ((None, {'stage': 'native_table_question', 'status': 'not_applicable'})
            if image_attachments else route_native_table_question(self, question, hits, document_id=document_id, page_no=page_no))
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
        from .native_total_question import route as route_native_totals
        total_result, total_trace = (route_native_totals(self, question, hits, document_id=document_id, page_no=page_no)
            if not image_attachments and table_trace['status'] in {'not_applicable', 'no_native_aligned_tables',
                'no_related_literal_row_labels', 'native_table_whole_question_unsupported'}
            else (None, {'stage': 'native_total_routing', 'status': 'not_applicable'}))
        if total_result is not None:
            return total_result
        from .source_answer_dossier import route as route_native_dossier
        dossier_result, dossier_trace = ((None, {'stage': 'source_answer_dossier', 'status': 'not_applicable'})
            if image_attachments else route_native_dossier(self, question, hits,
                document_id=document_id, page_no=page_no))
        if dossier_result is not None:
            return dossier_result
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
        result['trace'].append(total_trace)
        result['trace'].append(dossier_trace)
        result['trace'].append(row_trace)
        result['trace'].append(selection_trace)
        if self.generator and selected:
            from .responses_client import GenerationError
            from .answer_contract import question_contract
            if page_no is None and question_contract(question)['exhaustive_selection_required']:
                result['citations'],chain_trace=self._table_chain_citations(result['citations'])
                result['trace'].append(chain_trace)
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
            single_source_attempted = False
            early_client = self.generator.client
            from .source_span_answer import source_first_eligible, bind_source_span_answer, replay_source_span_proof
            if not image_attachments and source_first_eligible(question, early_client):
                single_source_attempted = True
                try:
                    literal = bind_source_span_answer(question, generation_citations, early_client)
                    verified = False
                    if literal['status'] == 'model_reviewed':
                        fresh, fresh_omitted = self._generation_citations(result['citations'])
                        verified = (fresh == generation_citations and fresh_omitted == omitted
                                    and replay_source_span_proof(question, literal, fresh))
                        self._verify_generation_chunks(fresh)
                    result['trace'].append({'stage': 'evidence_first_source_span',
                        'status': 'model_reviewed' if verified else 'source_replay_failed'
                            if literal['status'] == 'model_reviewed' else literal['status'],
                        'dispatch': 'direct_identifier_fact_before_claim_generation',
                        'reason': literal.get('reason'), 'model_audits': literal['model_audits'],
                        'evidence_contract': 'raw_source_only_no_validated_facts'})
                    if literal.get('literal_error_code') is not None:
                        result['trace'][-1]['literal_error_code'] = literal['literal_error_code']
                    if verified:
                        result.update(status='ok', answer=literal['answer_value'], claims=[],
                            answer_mode='source_span_model_reviewed',
                            answer_strategy='evidence_first_literal_source_span', answer_span_result=literal)
                        return result
                    if (literal['status'] == 'model_reviewed' or literal.get('reason') in {
                            'source_provider_failed', 'source_semantic_review_rejected', 'source_snapshot_changed'}):
                        # Do not turn a failed provider/review/source check into
                        # successful claim generation against the same evidence.
                        result.update(status='insufficient_evidence',
                            answer='原件事实未通过完整核对，暂不能确认答案。',
                            answer_mode='source_span_unverified',
                            answer_completeness='source_first_projection_not_verified')
                        return result
                finally:
                    self._verify_citation_sources(result['citations'])
                    self._verify_generation_chunks(generation_citations)
            if (not image_attachments
                    and (early_contract['multiple_requested_fields'] or early_contract['exhaustive_selection_required'])
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
                generated = self.generator.answer(question, generation_citations,
                    **({'image_attachments': image_attachments} if image_attachments else {}))
                result.update(generated)
                result['answer_mode'] = 'model_grounded'
                result['trace'].append({'stage': 'grounded_generation', 'status': 'validated', 'model': self.generator.client.model})
            except GenerationError as exc:
                if image_attachments:
                    raise ValueError('图片内容未能完成分析，本次没有生成忽略图片的回答，请稍后重试。') from exc
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
            if not image_attachments and (
                    result['answer_mode'] == 'extractive_fallback'
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
                        and not single_source_attempted
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

    def _table_chain_citations(self,citations):
        """Add sibling-page anchors only; no table semantics or closure assumed."""
        from .native_table_chain import probe
        result=list(citations); added=[];visited=set()
        records=self.records()
        for hit in citations:
            metadata=hit['metadata'];did=metadata['document_id']
            if did in visited or len(visited)>=4:
                continue
            visited.add(did)
            doc=self.document(did)
            if doc['modality']!='pdf':
                continue
            raw=self.verify_source(did,expected_sha256=metadata['source_sha256']).read_bytes()
            chains=probe(raw)['candidates']
            matching=[c for c in chains if metadata.get('page_no') in [p['page_no'] for p in c['pages']]]
            if len(matching)!=1:
                continue
            for page in matching[0]['pages']:
                if any(h['metadata']['document_id']==did and h['metadata'].get('page_no')==page['page_no'] for h in result):
                    continue
                anchors=[r for r in records if r.metadata['document_id']==did
                    and r.metadata.get('page_no')==page['page_no']
                    and DocumentChunker.clean_text(r.content)==page['header_text']]
                if len(anchors)!=1 or len(result)>=MAX_EVIDENCE_ITEMS:
                    continue
                record=anchors[0]
                extra=DocumentHit(record.document_id,record.title,0,(),record.content,record.source_uri,
                    {**record.metadata,'navigation_only':'native_repeated_header_sibling_page'}).to_dict()
                extra['citation_id']=max(h['citation_id'] for h in result)+1
                result.append(extra);added.append({'document_id':did,'page_no':page['page_no']})
        return result,{'stage':'native_table_chain_navigation','added_pages':added,
            'semantic_sample_identity_verified':False,'exhaustive_table_closure_verified':False,
            'model_called':False}

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
        native_contexts = {}
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
                    # Reconstruct a complete native region before accounting
                    # for duplicates. Repeated anchors must not consume the
                    # budget or force a later distinct page into a short prefix.
                    limit = (MAX_NATIVE_EVIDENCE_CHARS if native_eligible else
                             min(MAX_EVIDENCE_CHARS, MAX_TOTAL_EVIDENCE_CHARS - total))
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
                    # Only the anchor selecting the SAME reconstructed region
                    # may differ. Keep document identity, all geometry, scope,
                    # extraction version and permissions in the key. Identical
                    # text in another source/page is never merged.
                    context_key = (document_id, json.dumps(
                        {key: value for key, value in native_context.items() if key != 'anchor_match'},
                        ensure_ascii=False, sort_keys=True))
                    if context_key in native_contexts:
                        omitted.append({'citation_id': hit['citation_id'],
                                        'reason': 'duplicate_verified_native_context',
                                        'retained_citation_id': native_contexts[context_key]})
                        continue
                    if total + len(text) > MAX_TOTAL_EVIDENCE_CHARS:
                        omitted.append({'citation_id': hit['citation_id'],
                                        'reason': 'native_complete_context_unavailable'})
                        continue
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
                if native_context:
                    native_contexts[context_key] = hit['citation_id']
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
