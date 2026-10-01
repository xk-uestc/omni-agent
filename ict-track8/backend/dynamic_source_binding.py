"""Server-owned document-cell values for explicit deictic SQL scope.

Model text and coincidental value matches cannot authorize a filter. This
initial grammar handles one source, one returned dimension and explicit year /
percentage selectors; unsupported conditions stop rather than disappear.
"""
from copy import deepcopy
import math
import re

from .fusion_constraints import SourceConstraintError
from .nl2sql.models import FilterSpec, QueryPlan


DESCRIPTOR_KEYS = {'qualifier', 'dimension_label', 'table', 'column', 'value',
                   'provider_task_id', 'document_id', 'source_sha256', 'source_locator', 'source_selector_method'}


def _reject():
    raise SourceConstraintError('source_dynamic_binding_unverified')


def _same(left, right):
    return type(left) is type(right) and left == right


def _source_name(value):
    # File-type / conventional table suffix equivalence only; not fuzzy words.
    value = re.sub(r'[\s“”"\'《》]', '', value).casefold()
    return re.sub(r'(?:\.xlsx|\.xls|excel|xlsx|数据表|表)$', '', value)


def _authorize_provider(original_question, scope_question, provider, document, documents):
    args = provider['args']
    if set(args) != {'document_id', 'where', 'column'} or not isinstance(args['where'], dict) or not args['where']:
        _reject()
    if not all(isinstance(k, str) and k for k in args['where']) or not isinstance(args['column'], str) or not args['column']:
        _reject()
    # The SQL source clause is server-extracted from the original, not a model
    # rewrite. Selector years must not accidentally include the SQL year.
    if not scope_question or original_question.count(scope_question) != 1:
        _reject()
    prefix = original_question[:original_question.index(scope_question)]
    selected = re.search(r'从\s*[“”"\'《》]?(.{1,128}?)[“”"\'《》]?(?:中|里)(?:定位|筛选|找出|找到|选择)', prefix)
    if selected is None:
        _reject()
    if re.fullmatch(r'\s*(?:请)?(?:先)?\s*', prefix[:selected.start()]) is None:
        _reject()
    name = selected.group(1)
    wanted = _source_name(name)
    owners = [d['document_id'] for d in documents if wanted and wanted in {
        _source_name(d['document_id']), _source_name(d['title']), _source_name(d['filename'])}]
    if len(wanted) < 3 or owners != [document['document_id']] or args['document_id'] != document['document_id']:
        _reject()
    selector = prefix[selected.end():]
    if re.search(r'不含|不包括|排除|大于|小于|超过|至少|至多|或者|或|且|并且|按|排名|最高|最低', selector):
        _reject()
    if not re.search(r'的\s*' + re.escape(args['column']) + r'(?=[，,。\s]|$)', selector):
        _reject()
    years = re.findall(r'(?<!\d)((?:19|20)\d{2})年', selector)
    percentages = list(re.finditer(r'([^\s，,。]+?)(?:为|等于|=)\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[%％]', selector))
    expected = {}
    if years:
        if len(years) != 1:
            _reject()
        expected['年份'] = int(years[0])
    for match in percentages:
        key = match.group(1)
        # Remove the leading year phrase only, preserving the actual column.
        key = re.sub(r'^(?:19|20)\d{2}年', '', key)
        if not key or key in expected:
            _reject()
        value = float(match.group(2)) / 100
        if not math.isfinite(value):
            _reject()
        expected[key] = value
    if not expected or set(args['where']) != set(expected):
        _reject()
    for key, value in expected.items():
        actual = args['where'][key]
        if key == '年份':
            if type(actual) is not int or actual != value:
                _reject()
        elif type(actual) not in (int, float) or not math.isfinite(actual) or actual != value:
            _reject()
    for chunk in document['chunks']:
        metadata = chunk['metadata']
        cells = dict(zip(metadata.get('headers', []), metadata.get('values', [])))
        if all(isinstance(cells.get(key), dict) and cells[key].get('raw_value') == value
               for key, value in args['where'].items()):
            if any(cells[key].get('formula') for key in args['where']):
                _reject()
    # Complete coverage rather than a blacklist: unrecognized text such as
    # "已批准" or "仅线上" must not vanish while the numeric clauses survive.
    remaining = selector
    for match in reversed(percentages):
        remaining = remaining[:match.start()] + remaining[match.end():]
    remaining = re.sub(r'(?<!\d)(?:19|20)\d{2}年', '', remaining)
    remaining = re.sub(r'\s+', '', remaining)
    grammar = (r'[，,。]*的' + re.escape(args['column'])
               + r'[，,。]*(?:(?:并|再)?作为过滤条件)?(?:并|再)?(?:查询|查)(?:数据库|数据表)(?:中|里|的)?')
    if re.fullmatch(grammar, remaining) is None:
        _reject()


def bind_dynamic_source_filters(agent, *, task, tasks, results, required_intent, original_question):
    """Re-execute and authorize the referenced cell before the SQL tool runs."""
    if required_intent is None or not isinstance(original_question, str):
        return required_intent
    scope = getattr(required_intent, 'source_scope_question', '')
    if not re.search(r'该|这个|此', scope):
        return required_intent
    question = task['args'].get('question')
    refs = [part for part in question if isinstance(part, dict)] if isinstance(question, list) else []
    if len(refs) != 1 or set(refs[0]) != {'ref', 'path'} or refs[0]['path'] != ['value']:
        _reject()
    providers = [p for p in tasks if p['id'] == refs[0]['ref'] and p['tool'] == 'document_cell']
    if len(providers) != 1 or providers[0]['id'] not in results:
        _reject()
    provider = providers[0]
    # Dynamic selectors cannot themselves be model-created references.
    if agent.references(provider['args']):
        _reject()
    document = agent.knowledge_store.document(provider['args']['document_id'])
    _authorize_provider(original_question, scope, provider, document, agent.knowledge_store.list_documents())
    label = provider['args']['column']
    qualifiers = re.findall(r'(?:该|这个|此)' + re.escape(label), scope)
    if len(qualifiers) != 1:
        _reject()
    dimensions = {(link.table, link.column) for link in agent.sql_engine.analyze_slots(label)['dimensions']}
    if len(dimensions) != 1:
        _reject()
    old = results[provider['id']]
    if not isinstance(old, dict) or old.get('sha256') != document['sha256']:
        _reject()
    agent.knowledge_store.verify_source(document['document_id'], expected_sha256=old.get('sha256'))
    fresh = agent.execute('document_cell', provider['args'], provider['args'], {})
    if not isinstance(old, dict) or set(fresh) != set(old) or any(not _same(fresh[k], old[k]) for k in fresh):
        _reject()
    agent.knowledge_store.verify_source(document['document_id'], expected_sha256=fresh['sha256'])
    value = fresh['value']
    if (type(value) not in (str, int, float) or isinstance(value, str) and (not value or len(value) > 500)
            or type(value) is float and not math.isfinite(value)):
        _reject()
    table, column = next(iter(dimensions))
    descriptor = {'qualifier': qualifiers[0], 'dimension_label': label, 'table': table, 'column': column,
                  'value': value, 'provider_task_id': provider['id'], 'document_id': document['document_id'],
                  'source_sha256': fresh['sha256'], 'source_locator': fresh['locator'],
                  'source_selector_method': 'exact_unique_title_id_filename_with_filetype_suffix_equivalence'}
    bound = deepcopy(required_intent)
    bound.source_dynamic_filters = [descriptor]
    return bound


def apply_dynamic_source_filters(current_required, descriptors, *, tables, scope_question):
    """Revalidate descriptors in the execution schema; append expected filters.

    Never edits the actual model plan. Normal typed intent verification must
    still reject omitted/contradictory/arbitrary actual filters afterwards.
    """
    if not isinstance(current_required, QueryPlan) or not isinstance(descriptors, list) or len(descriptors) != 1:
        _reject()
    descriptor = descriptors[0]
    if not isinstance(descriptor, dict) or set(descriptor) != DESCRIPTOR_KEYS:
        _reject()
    if any(not isinstance(descriptor[k], str) or not descriptor[k] for k in DESCRIPTOR_KEYS - {'value'}):
        _reject()
    if (descriptor['qualifier'] not in {'该' + descriptor['dimension_label'], '这个' + descriptor['dimension_label'], '此' + descriptor['dimension_label']}
            or scope_question.count(descriptor['qualifier']) != 1
            or not re.fullmatch(r'[a-f0-9]{64}', descriptor['source_sha256'])
            or descriptor['source_selector_method'] != 'exact_unique_title_id_filename_with_filetype_suffix_equivalence'
            or not re.fullmatch(r'sheet:.{1,128}:row:[1-9]\d*/column:' + re.escape(descriptor['dimension_label']), descriptor['source_locator'])):
        _reject()
    found = [column for table in tables if table.name == descriptor['table']
             for column in table.columns if column.name == descriptor['column']]
    if len(found) != 1:
        _reject()
    value, kind = descriptor['value'], found[0].data_type.upper()
    if type(value) not in (str, int, float) or type(value) is float and not math.isfinite(value):
        _reject()
    if isinstance(value, str) and (not value or len(value) > 500):
        _reject()
    if any(token in kind for token in ('INT', 'REAL', 'NUM', 'DEC', 'FLOAT', 'DOUBLE')) and type(value) not in (int, float):
        _reject()
    if any(token in kind for token in ('TEXT', 'CHAR', 'CLOB')) and not isinstance(value, str):
        _reject()
    bound = deepcopy(current_required)
    target = (descriptor['table'], descriptor['column'])
    for item in bound.filters:
        if (item.table or bound.table, item.column) == target:
            if item.operator != '=' or not _same(item.value, value):
                _reject()
            return bound
    bound.filters.append(FilterSpec(descriptor['column'], '=', value, descriptor['qualifier'],
                                    '已核验文档单元格限定 SQL 来源范围', descriptor['table']))
    return bound
