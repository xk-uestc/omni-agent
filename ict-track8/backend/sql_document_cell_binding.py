"""Server-derived ranked SQL -> literal document-cell contracts.

The model chooses tools, never the winning entity or the user's source scope.
QueryPlan remains the SQL semantic object; ranked-result replay is shared with
SQL -> search. This module adds the pinned document/column/selector contract.
"""
from dataclasses import dataclass
import re

from .fusion_constraints import SourceConstraintError, _pieces, extract_source_clauses
from .pdf_table_values import label


def _reject(code='sql_document_cell_scope_unverified'):
    raise SourceConstraintError(code)


@dataclass(frozen=True)
class RankedCellIntent:
    source_range: tuple
    scope_question: str
    dimension_label: str
    dimension_table: str
    dimension_column: str
    document_id: str
    source_sha256: str
    column: str
    selector_columns: tuple

    def navigation(self):
        # No graph, values, formula or answer supplied to the model.
        return {**self.__dict__, 'authority': 'server_original_question_and_replayed_table',
                'top_n': 1, 'order_desc': True,
                'dimension_reference_prefix': ['dimension_values', self.dimension_table, self.dimension_column],
                'selector_mapping': 'literal_value_equality_only_not_a_public_business_mapping'}


def ranked_cell_intent(question, engine, knowledge):
    """Recognize one complete literal source request, independently of tasks."""
    documents = [d for d in knowledge.list_documents()
                 if d['title'] and d['title'] in question]
    pieces = list(_pieces(question))
    candidates = []
    for document in documents:
        for left, right in pieces:
            text = question[left:right]
            if document['title'] not in text or not re.search(r'(?:该|这个|此)', text):
                continue
            # Also separate a same-sentence explicit coordination, preserving
            # offsets. No unknown qualifier before the read may disappear.
            title_start = text.index(document['title'])
            starts = list(re.finditer(r'(?:并|然后|再|接着)(?=(?:从|查询|查找|读取|获取|查阅))', text[:title_start]))
            if starts:
                left += starts[-1].start(); text = question[left:right]
            pointer = re.search(r'(?:该|这个|此)(.+)', text)
            if pointer is None:
                continue
            source = r'(?:文档|PDF|教材)?(?:《' + re.escape(document['title']) + r'》|' + re.escape(document['title']) + r')'
            action = r'(?:查询|查找|读取|获取|查阅)'
            prefix = r'\s*(?:请)?(?:并|再|然后|接着)?(?:从)?(?:' + action + r')?' + source + r'(?:中|内|里)?(?:的)?(?:' + action + r')?\s*'
            if re.fullmatch(prefix, text[:pointer.start()]) is None:
                _reject()
            tail = pointer[1]
            links = [link for link in engine.analyze_slots(tail)['dimensions']
                     if tail.startswith(link.source_text)]
            identities = {(link.table, link.column, link.source_text) for link in links}
            if len(identities) != 1:
                _reject()
            table, dimension, noun = next(iter(identities))
            target = re.sub(r'^的', '', tail[len(noun):]).strip()
            target = re.sub(r'(?:值)?(?:是多少|多少)?$', '', target).strip()
            detail = knowledge.document(document['document_id'])
            if detail['modality'] != 'pdf':
                continue
            relations = [e for e in detail.get('elements', [])
                         if e.get('table_structure_status') == 'native_geometry_verified']
            headers = set()
            for relation in relations:
                knowledge.verify_native_table(detail['document_id'], relation['element_id'],
                                              expected_sha256=detail['sha256'])
            for chunk in detail['chunks']:
                headers.update(chunk['metadata'].get('headers', []))
            columns = [h for h in headers if label(target).startswith(h)]
            if len(columns) != 1:
                _reject()
            column = columns[0]
            suffix = label(target)[len(column):]
            if suffix:
                # A longer requested metric must be a literal source heading,
                # never an arbitrary model-added qualifier or a unit inference.
                if not re.fullmatch(r'[\u3400-\u9fff]{2,24}', suffix):
                    _reject()
                raw = knowledge.verify_source(detail['document_id'], expected_sha256=detail['sha256']).read_bytes()
                import pymupdf
                with pymupdf.open(stream=raw, filetype='pdf') as pdf:
                    original_text = label(' '.join(p.get_text() for p in pdf))
                if label(target) not in original_text:
                    _reject()
            clauses = extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine,
                                            verified_document_search_ranges=((left, right),))
            if len(clauses) != 1:
                _reject()
            required = engine.extract_required_intent(clauses[0].text)
            if (required.clarification or required.coverage.get('unresolved') or required.top_n != 1
                    or required.order_desc is not True or required.dimensions != [dimension]
                    or required.dimension_tables.get(dimension, required.table) != table
                    or required.dimension_transforms.get(dimension, 'raw') != 'raw'
                    or required.analysis_mode not in {'rank', 'aggregate'}):
                _reject()
            candidates.append(RankedCellIntent((left, right), clauses[0].text, noun, table, dimension,
                              detail['document_id'], detail['sha256'], column,
                              tuple(sorted(headers - {column}))))
    if len(candidates) > 1:
        _reject('source_binding_ambiguous')
    return candidates[0] if candidates else None


def authorize_sql_document_cells(question, tasks, engine, knowledge):
    intent = ranked_cell_intent(question, engine, knowledge)
    if intent is None:
        sql_ids = {t['id'] for t in tasks if t['tool'] == 'sql'}
        from .dependency_agent import DependencyAgent
        if any(t['tool'] == 'document_cell' and DependencyAgent.references(t['args']) & sql_ids for t in tasks):
            _reject()
        return {'source_ranges': [], 'bindings': []}
    cells = [t for t in tasks if t['tool'] == 'document_cell']
    sql = [t for t in tasks if t['tool'] == 'sql']
    if not cells or len(sql) != 1:
        _reject('sql_document_cell_dependency_missing')
    bindings = []
    for task in cells:
        args = task['args']
        if (set(args) != {'document_id', 'where', 'column'} or args['document_id'] != intent.document_id
                or args['column'] != intent.column or not isinstance(args['where'], dict) or len(args['where']) != 1):
            _reject()
        selector, ref = next(iter(args['where'].items()))
        if selector not in intent.selector_columns or not isinstance(ref, dict) or set(ref) != {'ref', 'path'}:
            _reject()
        path = ref['path']
        if not isinstance(path, list) or ref['ref'] != sql[0]['id']:
            _reject()
        if (len(path) == 3 and path[0] == 'rows' and type(path[1]) is int
                and path[2] in {intent.dimension_label, intent.dimension_column}):
            path = ['dimension_values', intent.dimension_table, intent.dimension_column, path[1]]
        if (len(path) != 4 or path[:3] != ['dimension_values', intent.dimension_table, intent.dimension_column]
                or type(path[3]) is not int or not 0 <= path[3] < 100):
            _reject()
        bindings.append({**intent.__dict__, 'search_task_id': task['id'], 'sql_task_id': sql[0]['id'],
                         'target_text': intent.column, 'row_index': path[3], 'output_column': None,
                         'reference_path': path, 'selector_column': selector, 'original_reference_path': ref['path']})
    return {'source_ranges': [intent.source_range], 'bindings': bindings}


def normalize_cell_references(bundle, tasks):
    by_id = {t['id']: t for t in tasks}
    for binding in bundle['bindings']:
        by_id[binding['search_task_id']]['args']['where'][binding['selector_column']]['path'] = list(binding['reference_path'])


def validate_sql_document_cell(bundle, *, task, tasks, results, engine, knowledge):
    from .sql_document_binding import validate_sql_document_search
    selected = [b for b in bundle['bindings'] if b['search_task_id'] == task['id']]
    if not selected:
        return None
    binding = selected[0]
    knowledge.verify_source(binding['document_id'], expected_sha256=binding['source_sha256'])
    # Adapt the literal selector to the existing independent physical-result
    # replay. No search is executed and no evidence is synthesized.
    projected = []
    by_binding = {b['search_task_id']: b for b in bundle['bindings']}
    for item in tasks:
        b = by_binding.get(item['id'])
        if b:
            args = item['args']
            if (args.get('document_id') != b['document_id'] or args.get('column') != b['column']
                    or args.get('where') != {b['selector_column']: {'ref': b['sql_task_id'], 'path': b['reference_path']}}):
                _reject()
            item = {'id': item['id'], 'tool': 'search', 'args': {'query': [
                args['where'][b['selector_column']], b['column']]}}
        projected.append(item)
    proof = validate_sql_document_search(bundle, task=next(t for t in projected if t['id'] == task['id']),
                                        tasks=projected, results=results, engine=engine,
                                        allow_ranked_aggregate=True)
    return {**proof, 'scope': 'rank1_dimension_and_pinned_literal_document_cell',
            'document_id': binding['document_id'], 'source_sha256': binding['source_sha256'],
            'column': binding['column'], 'selector_column': binding['selector_column'],
            'selector_mapping': 'literal_value_equality_only_not_a_public_business_mapping'}
