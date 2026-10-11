"""Exact-object scope expansion using explicit body/section/page relationships."""
from collections import defaultdict
import re

from .source_binding import contains_id, identifiers, normalize


def expand_identifier_scope(records, codes, *, verify, document, max_pages=12):
    direct = [r for r in records if all(contains_id(r.content, c) for c in codes)]
    allowed = {r.document_id for r in direct}
    grouped = defaultdict(list)
    for r in records:
        grouped[r.metadata['document_id']].append(r)
    links, omitted = {}, []
    for did, source in grouped.items():
        seeds = [r for r in direct if r.metadata['document_id'] == did]
        if not seeds:
            continue
        pages = sorted({r.metadata.get('page_no') for r in source if type(r.metadata.get('page_no')) is int})
        # The budget applies to document/section navigation even when chunk
        # bodies are already indexed. Never infer unscanned record closure.
        page_count = int(document(did)['stats'].get('page_count') or 1)
        if page_count > max_pages:
            omitted.append({'document_id': did, 'reason': 'document_scope_page_budget', 'pages': page_count})
            continue
        sha = seeds[0].metadata['source_sha256']
        verify(did, expected_sha256=sha)
        page_records = defaultdict(list)
        for r in source:
            page_records[r.metadata.get('page_no')].append(r)
        def objects(items):
            return {i.casefold() for r in items for i in identifiers(r.content)}
        expected = {c.casefold() for c in codes}
        # A first-page self declaration gives a document object scope only if
        # no other exact object/sample appears anywhere in that bounded source.
        declarations = [r for r in seeds if r.metadata.get('page_no') in (None, 1)
            and all(re.search(r'(?:对象代码|对象编号|样本编号)\s*[:：]?\s*'+re.escape(c)+r'(?![A-Za-z0-9_-])', normalize(r.content), re.I) for c in codes)]
        document_seed = declarations[0] if declarations and objects(source) == expected else None
        for r in source:
            if r.document_id in allowed:
                continue
            page = r.metadata.get('page_no')
            scope_seed, relation = None, None
            if document_seed:
                scope_seed, relation = document_seed, 'first_page_single_object_declaration'
            else:
                same_page = [s for s in seeds if page is not None and s.metadata.get('page_no') == page]
                if same_page and objects(page_records[page]) <= expected:
                    scope_seed, relation = same_page[0], 'same_native_page_single_object'
                path = r.metadata.get('title_path')
                section = [s for s in seeds if path and s.metadata.get('title_path') == path]
                section_rows = [s for s in source if path and s.metadata.get('title_path') == path]
                if section and objects(section_rows) <= expected:
                    scope_seed, relation = section[0], 'same_explicit_section_single_object'
                previous = [s for s in seeds if type(page) is int and s.metadata.get('page_no') == page-1]
                explicit_continuation = any(re.search(r'续上页|接上页|前页继续|(?:本对象|本记录|说明)[(（]续[)）]', normalize(s.content)) for s in page_records[page])
                if previous and explicit_continuation and objects(page_records[page]+page_records[page-1]) <= expected:
                    scope_seed, relation = previous[0], 'explicit_next_page_continuation'
            if scope_seed:
                allowed.add(r.document_id)
                links[r.document_id] = {'relation': relation, 'anchor_chunk_id': scope_seed.document_id,
                    'anchor_quote': scope_seed.content, 'document_id': did, 'source_sha256': sha,
                    'anchor_page_no': scope_seed.metadata.get('page_no'), 'page_no': page,
                    'exact_identifiers': list(codes), 'authority': 'navigation_only_original_body_scope'}
        verify(did, expected_sha256=sha)
    return [r for r in records if r.document_id in allowed], {
        'version': 'bounded-object-links-v1', 'status': 'budget_exhausted' if omitted else 'bounded',
        'max_document_pages': max_pages, 'direct_chunk_count': len(direct), 'linked_chunk_count': len(links),
        'links': links, 'omitted': omitted, 'extra_retrieval_calls': 0, 'extra_native_page_reads': 0,
        'unknown_relationship_policy': 'retain_exact_identifier_filter'}
