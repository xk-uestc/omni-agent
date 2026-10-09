"""Original-source authorization for ranked SQL -> document search references."""
import hashlib
import json
import re
from collections import Counter
from dataclasses import fields

from .fusion_constraints import SourceConstraintError, extract_source_clauses, verify_required_intent
from .nl2sql.models import QueryPlan, FilterSpec, HavingSpec, MetricSpec, DerivedMetricSpec
from .nl2sql.security import execute_read_only
from .sql_evidence import dimension_evidence


def _reject(code='sql_document_scope_unverified'):
    raise SourceConstraintError(code)


def _canonical(text):
    return re.sub(r'[\s，,。？?的]', '', text)


def _typed_result_plan(data):
    """Decode only physical intent slots; metadata cannot establish intent."""
    if not isinstance(data, dict):
        _reject()
    try:
        ordinary = {field.name for field in fields(QueryPlan)} - {'filters', 'having', 'metrics', 'derived_metrics', 'links'}
        plan = QueryPlan(**{key: value for key, value in data.items() if key in ordinary})
        plan.filters = [FilterSpec(**item) for item in data.get('filters', [])]
        plan.having = HavingSpec(**data['having']) if data.get('having') is not None else None
        plan.metrics = [MetricSpec(**{**item, 'filters': [FilterSpec(**f) for f in item.get('filters', [])]})
                        for item in data.get('metrics', [])]
        plan.derived_metrics = [DerivedMetricSpec(**item) for item in data.get('derived_metrics', [])]
        return plan
    except (TypeError, ValueError, KeyError):
        _reject()


def _output_bindings(plan):
    """Map display labels onto independently checked physical result slots."""
    outputs = []
    for column in plan.dimensions:
        outputs.append((('dimension', plan.dimension_tables.get(column, plan.table), column,
                         plan.dimension_transforms.get(column, 'raw')),
                        plan.dimension_labels.get(column, column)))
    if plan.metrics:
        metrics = {metric.id: metric for metric in [*plan.metrics, *plan.derived_metrics]}
        for key in plan.output_metrics or list(metrics):
            metric = metrics[key]
            identity = (('metric', metric.table, metric.column, metric.function)
                        if isinstance(metric, MetricSpec) else ('derived', json.dumps(metric.expression, sort_keys=True)))
            outputs.append((identity, metric.label))
    else:
        outputs.append((('metric', plan.metric_table or plan.table, plan.metric_column, plan.metric_function),
                        plan.metric_label or plan.metric_column))
    if plan.analysis_mode == 'rank':
        outputs.append((('rank',), '排名'))
    identities, labels = [identity for identity, _ in outputs], [label for _, label in outputs]
    if len(set(identities)) != len(outputs) or len(set(labels)) != len(outputs):
        _reject()
    return dict(outputs)


def _ranked_metric_identity(plan):
    if not plan.metrics:
        return ('metric', plan.metric_table or plan.table, plan.metric_column, plan.metric_function)
    selected = plan.order_metric or (plan.output_metrics or [plan.metrics[0].id])[0]
    matches = [metric for metric in [*plan.metrics, *plan.derived_metrics] if metric.id == selected]
    if len(matches) != 1:
        _reject()
    metric = matches[0]
    return (('metric', metric.table, metric.column, metric.function) if isinstance(metric, MetricSpec)
            else ('derived', json.dumps(metric.expression, sort_keys=True)))


def _physical_rows(rows, columns, bindings):
    expected = set(bindings.values())
    if len(columns) != len(expected) or set(columns) != expected:
        _reject()
    identities = sorted(bindings, key=repr)
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected or any(isinstance(value, bool) for value in row.values()):
            _reject()
        normalized.append(tuple(row[bindings[key]] for key in identities))
    return Counter(normalized)


def _reference_and_target(task):
    from .search_scope import validate_search_args, SearchScopeError
    try:
        validate_search_args(task['args'])
    except SearchScopeError:
        _reject()
    if not isinstance(task['args']['query'], list):
        _reject()
    parts = task['args']['query']
    refs = [part for part in parts if isinstance(part, dict)]
    if len(refs) != 1 or any(not isinstance(p, (str, dict)) for p in parts):
        _reject()
    ref = refs[0]
    if set(ref) != {'ref', 'path'} or not isinstance(ref['ref'], str) or not isinstance(ref['path'], list):
        _reject()
    path = ref['path']
    row_reference = (len(path) == 3 and path[0] == 'rows' and type(path[1]) is int and 0 <= path[1] < 100
                     and isinstance(path[2], str) and 0 < len(path[2]) <= 128)
    physical_reference = (len(path) == 4 and path[0] == 'dimension_values'
                          and all(isinstance(part, str) and 0 < len(part) <= 128 for part in path[1:3])
                          and type(path[3]) is int and 0 <= path[3] < 100)
    if not (row_reference or physical_reference):
        _reject()
    literals = ''.join(p for p in parts if isinstance(p, str))
    # Only optional search request verbs are nonsemantic wrappers. Constant
    # regions, extra dates, changed targets or other appended terms survive.
    literals = re.sub(r'^\s*(?:请)?(?:检索|搜索|查询|查找)?\s*', '', literals)
    return ref, _canonical(literals)


def authorize_sql_document_search(original_question, tasks, engine):
    """Return only exact document-search ranges authorized by real SQL refs.

    Call before fusion source binding. The caller may exclude source_ranges
    from SQL outside-scope guards; no other document range is exempted.
    """
    bundle = {'source_ranges': [], 'bindings': []}
    # Derive the pointer noun from real schema links, so the same grammar
    # works for regions, clients, channels or another registered dimension.
    names = set()
    for pointer in re.finditer(r'(?:检索|搜索)(?:该|这个|此)([^，,。；;？！?\n]+)', original_question):
        tail = pointer[1]
        names.update(link.source_text for link in engine.analyze_slots(tail)['dimensions']
                     if tail.startswith(link.source_text))
    dimension_pattern = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    patterns = [
        r'(?:再|然后)?(?:根据|依据)(?:该|这个|此)(?P<label>[\u4e00-\u9fffA-Za-z_]+?)(?:检索|搜索)(?P<target>[^，,。；;？！?\n]+)',
    ]
    if dimension_pattern:
        patterns.append(r'(?:再|然后)?(?:检索|搜索)(?:该|这个|此)(?P<label>' + dimension_pattern
                        + r')(?:的)?(?P<target>[^，,。；;？！?\n]+)')
    matches = sorted((match for pattern in patterns for match in re.finditer(pattern, original_question)),
                     key=lambda match: match.start())
    if not matches:
        return bundle
    if len(matches) != 1:
        _reject()
    match = matches[0]
    # Full punctuation-delimited clause: a preceding hidden qualifier cannot
    # disappear simply because a "根据该地区" substring exists later.
    left = max(original_question.rfind(c, 0, match.start()) for c in ('，', ',', '。', '；', ';', '\n')) + 1
    if original_question[left:match.start()].strip():
        _reject()
    label, target = match.group('label'), match.group('target').strip()
    if (not target or len(target) > 200 or re.search(r'\d|不含|不包括|排除|仅|只|按|条件|限定|之外|以外', target)):
        _reject()
    clauses = extract_source_clauses(original_question, engine.schema(include_row_count=False), engine=engine,
                                    verified_document_search_ranges=((match.start(), match.end()),))
    if len(clauses) != 1:
        _reject()
    required = engine.extract_required_intent(clauses[0].text)
    if len(required.dimensions) != 1:
        _reject()
    column = required.dimensions[0]
    table = required.dimension_tables.get(column, required.table)
    dimensions = {(link.table, link.column) for link in engine.analyze_slots(label)['dimensions']}
    if (required.clarification or required.coverage.get('unresolved') or required.top_n != 1
            or required.analysis_mode != 'rank' or required.order_desc is not True
            or (table, column) not in dimensions):
        _reject()
    searches = [task for task in tasks if task['tool'] == 'search']
    if not searches:
        _reject()
    provider_ids = set()
    for task in searches:
        ref, literal_target = _reference_and_target(task)
        if literal_target != _canonical(target):
            _reject()
        providers = [p for p in tasks if p['id'] == ref['ref'] and p['tool'] == 'sql']
        if len(providers) != 1:
            _reject()
        provider_ids.add(ref['ref'])
        physical = ref['path'][0] == 'dimension_values'
        if physical and ref['path'][1:3] != [table, column]:
            _reject()
        bundle['bindings'].append({'search_task_id': task['id'], 'sql_task_id': ref['ref'],
            'dimension_label': label, 'dimension_table': table, 'dimension_column': column,
            'qualifier': match.group(0)[:match.group(0).index(label) + len(label)],
            'target_text': target, 'row_index': ref['path'][3] if physical else ref['path'][1],
            'output_column': None if physical else ref['path'][2], 'reference_path': list(ref['path']),
            'scope_question': clauses[0].text})
    if len(provider_ids) != 1:
        _reject()
    bundle['source_ranges'] = [(match.start(), match.end())]
    return bundle


def validate_sql_document_search(bundle, *, task, tasks, results, engine):
    """Recheck rank output, exact query ref and all tied winners before search."""
    bindings = [b for b in bundle['bindings'] if b['search_task_id'] == task['id']]
    if not bindings:
        return None
    if len(bindings) != 1:
        _reject()
    binding = bindings[0]
    ref, target = _reference_and_target(task)
    if (ref['ref'] != binding['sql_task_id'] or ref['path'] != binding['reference_path']
            or target != _canonical(binding['target_text'])):
        _reject()
    result = results.get(binding['sql_task_id'])
    if not isinstance(result, dict) or result.get('status') != 'ok':
        _reject()
    provenance, plan = result.get('provenance', {}), result.get('plan', {})
    validation = provenance.get('source_constraint_validation', {})
    if (validation.get('status') != 'verified'
            or validation.get('scope_question_sha256') != hashlib.sha256(binding['scope_question'].encode()).hexdigest()
            or provenance.get('result_completeness') != 'within_return_limit'):
        _reject()
    dimension = binding['dimension_column']
    output_column = plan.get('dimension_labels', {}).get(dimension, dimension)
    physical = binding['reference_path'][0] == 'dimension_values'
    dimensions = {(link.table, link.column) for link in engine.analyze_slots(binding['dimension_label'])['dimensions']}
    if (binding['dimension_table'], dimension) not in dimensions:
        _reject()
    if (plan.get('top_n') != 1 or plan.get('analysis_mode') != 'rank' or plan.get('order_desc') is not True
            or plan.get('dimensions') != [dimension]
            or plan.get('dimension_tables', {}).get(dimension, plan.get('table')) != binding['dimension_table']
            or plan.get('dimension_transforms', {}).get(dimension, 'raw') != 'raw'
            or not physical and output_column != binding['output_column']):
        _reject()
    rows = result.get('rows')
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        _reject()
    relevant = [b for b in bundle['bindings'] if b['sql_task_id'] == binding['sql_task_id']]
    indices = [b['row_index'] for b in relevant]
    if len(indices) != len(set(indices)) or set(indices) != set(range(len(rows))):
        _reject('sql_document_tied_winners_not_covered')
    by_id = {t['id']: t for t in tasks}
    for candidate in relevant:
        actual_ref, actual_target = _reference_and_target(by_id[candidate['search_task_id']])
        if (actual_ref != {'ref': candidate['sql_task_id'], 'path': candidate['reference_path']}
                or actual_target != _canonical(candidate['target_text'])):
            _reject()
    value = rows[binding['row_index']].get(output_column)
    if physical:
        rebuilt = dimension_evidence(result)
        if result.get('dimension_values') != rebuilt:
            _reject()
        actual_values = rebuilt.get(binding['dimension_table'], {}).get(dimension, [])
        if len(actual_values) != len(rows) or actual_values[binding['row_index']] != value:
            _reject()
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        _reject()
    # The self-reported SQL/hash checks consistency only. Independently plan
    # the original scope through rules in this exact read snapshot, without
    # invoking/changing the configured model provider, and prove all values.
    sql, parameters = result.get('sql'), result.get('parameters')
    if not isinstance(sql, str) or not isinstance(parameters, list):
        _reject()
    digest = hashlib.sha256(json.dumps({'sql': sql, 'parameters': parameters}, ensure_ascii=False,
                                      default=str, sort_keys=True).encode()).hexdigest()[:16]
    if provenance.get('query_hash') != digest:
        _reject()
    with engine._connect() as connection:
        tables, index, revision = engine._snapshot_for(connection)
        required = engine._rules_plan(binding['scope_question'], tables, connection, index, cache_namespace=revision)
        actual_plan = _typed_result_plan(plan)
        if verify_required_intent(actual_plan, required):
            _reject()
        if _ranked_metric_identity(actual_plan) != _ranked_metric_identity(required):
            _reject()
        expected_bindings, actual_bindings = _output_bindings(required), _output_bindings(actual_plan)
        if set(expected_bindings) != set(actual_bindings):
            _reject()
        canonical_sql, canonical_parameters = (engine.metric_compiler.compile(required, tables)
            if required.metrics or required.fan_out else engine.planner.build_sql(required))
        canonical_columns, canonical_rows = execute_read_only(connection, canonical_sql, canonical_parameters,
            max_rows=engine.max_rows, max_steps=engine.max_steps, max_seconds=engine.max_seconds)
        if len(canonical_rows) >= min(required.limit, engine.max_rows):
            _reject('sql_document_tied_winners_not_covered')
        if (_physical_rows(canonical_rows, canonical_columns, expected_bindings)
                != _physical_rows(rows, result.get('columns', []), actual_bindings)):
            _reject()
        columns, actual_rows = execute_read_only(connection, sql, tuple(parameters), max_rows=engine.max_rows,
                                                max_steps=engine.max_steps, max_seconds=engine.max_seconds)
    if list(columns) != result.get('columns') or list(actual_rows) != rows:
        _reject()
    return {'status': 'verified', 'scope': 'actual_rank1_dimension_reference_and_complete_ties',
            'sql_task_id': binding['sql_task_id'], 'row_index': binding['row_index'],
            'output_column': output_column, 'reference_path': binding['reference_path'],
            'value': value, 'target_text': binding['target_text'],
            'result_verification': 'independent_original_scope_rules_and_complete_physical_result_replay',
            'independent_query_hash': hashlib.sha256(json.dumps({'sql': canonical_sql, 'parameters': canonical_parameters},
                ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()[:16]}


def normalize_sql_document_references(bundle, tasks):
    """Bind an authorized business/physical label to the stable dimension path.

    This runs only after original-question authorization, on a copied graph.
    It cannot guess arbitrary aliases or change the referenced row or source.
    The existing independent SQL replay and complete-tie verification still
    run before the search uses the value.
    """
    by_id = {task['id']: task for task in tasks}
    for binding in bundle['bindings']:
        original = binding['reference_path']
        if original[0] != 'rows':
            continue
        if original[2] not in {binding['dimension_label'], binding['dimension_column']}:
            # Preserve the original strict path contract for arbitrary labels.
            continue
        task = by_id[binding['search_task_id']]
        ref, target = _reference_and_target(task)
        if ref != {'ref': binding['sql_task_id'], 'path': original} or target != _canonical(binding['target_text']):
            _reject()
        physical = ['dimension_values', binding['dimension_table'],
                    binding['dimension_column'], binding['row_index']]
        ref['path'] = physical
        binding['original_reference_path'] = list(original)
        binding['reference_path'] = list(physical)
        binding['output_column'] = None
        binding['reference_normalization'] = 'authorized_original_dimension_to_physical_slot'
