"""Bounded PDF-document page search with explicit candidate-scope disclosure.

Search selects documents, not answers. All pages of each selected PDF must be
inspected before a unique complete key may select one model/image request.
This is not a global visual index: image-only documents without retrieval hits
require an explicit source selector, and large sources require a page tool.
"""
from __future__ import annotations

from .visual_table_reader import bind_visual_table_question, label_present
from .visual_tables import extract_pdf_tables
from .visual_work_budget import visual_work_slot


MAX_CANDIDATE_DOCUMENTS = 4
MAX_CANDIDATE_PAGES = 12
MAX_CANDIDATE_BYTES = 40 * 1024 * 1024


def route_visual_table_question(store, question, hits, *, document_id=None, page_no=None):
    documents = {d['document_id']: d for d in store.list_documents()}
    if document_id is not None:
        candidates = [documents[document_id]]
        scope = 'explicit_document_page' if page_no is not None else 'explicit_document_all_pages'
    else:
        selected = dict.fromkeys(h.metadata['document_id'] for h in hits)
        candidates = [documents[key] for key in selected if key in documents and documents[key]['modality'] == 'pdf']
        scope = 'retrieved_pdf_documents_all_pages_not_global_corpus'
    candidates = [d for d in candidates if d['modality'] == 'pdf']
    if page_no is not None and (not candidates or type(page_no) is not int or not 1 <= page_no <= int(candidates[0]['stats'].get('page_count') or 1)):
        raise ValueError('指定PDF页码超出资料范围')
    trace = {'stage': 'visual_page_routing', 'scope': scope,
             'candidate_document_ids': [d['document_id'] for d in candidates],
             'status': 'no_pdf_candidates', 'model_requests_attempted': 0,
             'limits': {'documents': MAX_CANDIDATE_DOCUMENTS, 'pages': MAX_CANDIDATE_PAGES,
                        'source_bytes': MAX_CANDIDATE_BYTES}}
    if not candidates or store.generator is None:
        trace['status'] = 'no_pdf_candidates' if not candidates else 'visual_model_unconfigured'
        return None, trace
    def budget_incomplete(reason):
        trace['status'] = reason
        return {'status': 'incomplete', 'question': question, 'answer_mode': 'visual_grid_routed',
                'answer': None, 'clarification': '候选资料超出完整页核验预算，请明确选择一份PDF及页码后重试。',
                'clarification_code': reason, 'citations': [], 'trace': [trace],
                'retrieval': store.retrieval_health()}, trace
    if len(candidates) > MAX_CANDIDATE_DOCUMENTS:
        return budget_incomplete('candidate_budget_exceeded')
    pages = 1 if page_no is not None else sum(int(d['stats'].get('page_count') or 1) for d in candidates)
    trace['candidate_pages'] = pages
    if pages > MAX_CANDIDATE_PAGES:
        return budget_incomplete('candidate_page_budget_exceeded')
    with visual_work_slot():
        sources, source_bytes = {}, 0
        for d in candidates:
            raw = store.verify_source(d['document_id'], expected_sha256=d['sha256']).read_bytes()
            source_bytes += len(raw)
            if source_bytes > MAX_CANDIDATE_BYTES:
                return budget_incomplete('candidate_byte_budget_exceeded')
            sources[d['document_id']] = raw
        bindings, related = [], []
        for d in candidates:
            candidate_pages = [page_no] if page_no is not None else range(1, int(d['stats'].get('page_count') or 1) + 1)
            for candidate_page in candidate_pages:
                tables = extract_pdf_tables(sources[d['document_id']], page_no=candidate_page,
                                            expected_source_sha256=d['sha256'])
                binding = bind_visual_table_question(question, tables)
                if binding['status'] == 'bound':
                    bindings.append((d, candidate_page, tables, binding))
                elif any(label_present(f['fact_key']['row_header'], question)
                         and all(label_present(p, question) for p in f['fact_key']['column_header_path'])
                         for t in tables['tables'] for f in t['facts']):
                    related.append({'document_id': d['document_id'], 'page_no': candidate_page,
                                    'source_sha256': d['sha256'], 'reason': binding['clarification_code']})
        # Sources that established uniqueness matter even if they didn't win.
        def recheck():
            for d in candidates:
                store.verify_source(d['document_id'], expected_sha256=d['sha256'])
        recheck()
        trace['complete_key_matches'] = len(bindings)
        trace['scope_rejections'] = related
        if not bindings and not related:
            trace['status'] = 'no_complete_grid_key'
            return None, trace
        if len(bindings) != 1 or related:
            trace['status'] = 'ambiguous_or_unbound_grid_scope'
            options = [{'document_id': d['document_id'], 'title': d['title'], 'page_no': page,
                        'source_sha256': d['sha256']} for d, page, _, _ in bindings]
            return {'status': 'incomplete', 'question': question, 'answer_mode': 'visual_grid_routed',
                    'answer': None, 'clarification': '请明确资料与页码，并使用完整行列名称；当前无法唯一核定单元格。',
                    'clarification_code': 'visual_source_or_cell_scope_ambiguous', 'source_options': options,
                    'citations': [], 'trace': [trace], 'retrieval': store.retrieval_health()}, trace
        d, page_no, tables, _ = bindings[0]
        asset = store._visual_asset(d['document_id'], page_no=page_no, expected_source_sha256=d['sha256'])
        from .visual_table_reader import VisualTableReader
        from .responses_client import GenerationError
        try:
            result = VisualTableReader(store.generator.client, store.ocr_pipeline).answer(question, asset, tables)
        except GenerationError:
            result = {'status': 'incomplete', 'answer': None, 'clarification_code': 'visual_model_unavailable',
                      'generation_audit': store.generator.client.audit}
        finally:
            recheck()
        trace['status'] = result['status']
        trace['model_requests_attempted'] = 1
        metadata = {'document_id': d['document_id'], 'page_no': page_no, 'source_sha256': d['sha256'],
                    'source_locator': f'page:{page_no}', 'retrieval_channel': 'bounded_visual_grid',
                    'render_sha256': asset.manifest['render_sha256'],
                    'validation_scope': result.get('validation_scope'),
                    'visible_cell_verification': result.get('visible_cell_verification')}
        if result.get('fact'):
            metadata.update({key: result['fact'][key] for key in ('fact_id', 'table_id', 'bbox_display_pt', 'fact_key')})
        citations = [{'citation_id': 1, 'document_id': d['document_id'], 'title': d['title'],
                      'snippet': result.get('answer') or '所选页图未通过数值核验；不作为答案。',
                      'source_uri': asset.manifest['original_uri'], 'score': None, 'matched_terms': [],
                      'metadata': metadata}]
        if result['status'] != 'ok':
            result['clarification'] = '所选页图或单元格核验未通过，请查看来源和验证详情。'
        return {**result, 'question': question, 'document_id': d['document_id'],
                'answer_mode': 'visual_grid_routed', 'citations': citations, 'trace': [trace],
                'retrieval': store.retrieval_health()}, trace
