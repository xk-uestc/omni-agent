"""Server-owned formula-variable to executed physical aggregate contracts.

Names, schema aliases and catalog definitions establish semantics. Numeric
equality, units, display aliases and supplied aggregate-leaf descriptors do
not establish which business quantity a formula variable denotes.
"""
import math
import re

from .fusion_constraints import SourceConstraintError
from .nl2sql.schema import normalize_text
from .sql_evidence import aggregate_evidence


def _reject(code='formula_parameter_binding_unverified'):
    raise SourceConstraintError(code)


def _stem(name):
    return re.sub(r'^(?:基准|历史|本期|总计|合计|总)', '', normalize_text(name), count=1)


def _column_names(name):
    normalized = normalize_text(name)
    return {normalized, normalized[:-2]} if normalized.endswith('金额') and len(normalized) > 2 else {normalized}


def _same(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    return type(left) is type(right) and left == right


def _actual_metric(result, path, resolved):
    if result.get('status') != 'ok' or not isinstance(result.get('rows'), list):
        _reject()
    cells, ambiguous = aggregate_evidence(result)
    metrics = result.get('plan', {}).get('metrics') or [{
        'table': result.get('plan', {}).get('metric_table') or result.get('plan', {}).get('table'),
        'column': result.get('plan', {}).get('metric_column'),
        'function': result.get('plan', {}).get('metric_function'),
        'label': result.get('plan', {}).get('metric_label') or result.get('plan', {}).get('metric_column')}]
    if len(path) == 3 and path[0] == 'rows':
        row, label = path[1:]
        selected = [metric for metric in metrics if metric.get('label') == label]
        if type(row) is not int or not 0 <= row < len(result['rows']) or len(selected) != 1:
            _reject()
        metric = selected[0]
        value = result['rows'][row].get(label)
        if not _same(value, resolved):
            _reject()
    elif len(path) == 5 and path[0] == 'aggregate_cells':
        table, column, function, row = path[1:]
        if type(row) is not int or row < 0 or not all(isinstance(item, str) for item in (table, column, function)):
            _reject()
        selected = [metric for metric in metrics if (metric.get('table'), metric.get('column'), metric.get('function')) == (table, column, function)]
        if len(selected) != 1 or [table, column, function] in ambiguous:
            _reject()
        metric = selected[0]
        try:
            # Rebuild from plan + actual rows, never trust the supplied leaf.
            evidence = cells[table][column][function][row]
        except (KeyError, IndexError, TypeError):
            _reject()
        if not isinstance(resolved, dict) or resolved != evidence:
            _reject()
        value = evidence['value']
    else:
        _reject()
    if type(value) not in (int, float) or not math.isfinite(value):
        _reject()
    if not all(isinstance(metric.get(key), str) and metric[key] for key in ('table', 'column', 'function')):
        _reject()
    return metric, row, value


def _parameter_candidates(engine, name, tables, source_tables):
    normalized, stem = normalize_text(name), _stem(name)
    distinct = bool(re.match(r'^(?:去重|不同)', stem))
    if distinct:
        stem = re.sub(r'^(?:去重|不同)', '', stem, count=1)
    available = {(table.name, column.name): column for table in tables for column in table.columns}
    candidates = set()
    catalog = getattr(engine, 'metric_catalog', None)
    if catalog:
        for metric_id, metric in catalog.sources.items():
            aliases = {_stem(metric.label), *(_stem(alias) for alias in catalog.aliases.get(metric_id, ()))}
            if stem in aliases and (metric.table, metric.column) in available:
                function = 'COUNT_DISTINCT' if distinct else metric.function
                if distinct and metric.function not in {'COUNT', 'COUNT_DISTINCT'}:
                    _reject()
                candidates.add((metric.table, metric.column, function))
    if candidates:
        return candidates
    # An explicit record count denotes rows of a uniquely attributable source,
    # not COUNT_DISTINCT of an arbitrary nullable/business column.
    if stem == '记录数':
        for table in tables:
            primary = [column for column in table.columns if column.primary_key]
            if table.name in source_tables and len(primary) == 1:
                candidates.add((table.name, primary[0].name, 'COUNT_DISTINCT' if distinct else 'COUNT'))
        return candidates
    explicit_function = ('COUNT_DISTINCT' if distinct else 'AVG' if normalized.startswith('平均')
                         else 'SUM' if normalized.startswith(('总', '合计')) else None)
    if normalized.startswith('平均'):
        stem = normalized[2:]
    for rule in engine.planner.linker.rules_for(tables):
        aliases = {_stem(alias) for alias in rule.aliases} | _column_names(rule.column)
        if rule.role != 'metric' or stem not in aliases or (rule.table, rule.column) not in available:
            continue
        function = explicit_function or rule.metric_function or 'SUM'
        candidates.add((rule.table, rule.column, function))
    for (table, column), metadata in available.items():
        if stem in _column_names(column):
            numeric = any(kind in metadata.data_type.upper() for kind in ('INT', 'REAL', 'NUM', 'DEC', 'FLOAT', 'DOUBLE'))
            if numeric and not metadata.primary_key:
                candidates.add((table, column, explicit_function or 'SUM'))
    return candidates


def _document_parameter(name, path, result, resolved):
    if path != [] or not isinstance(resolved, dict) or resolved != result:
        _reject()
    if (not isinstance(result.get('source_uri'), str) or not result['source_uri'].startswith('/api/v1/knowledge/documents/')
            or not re.fullmatch(r'[a-f0-9]{64}', result.get('sha256', ''))
            or type(result.get('value')) not in (int, float) or not math.isfinite(result['value'])):
        _reject()
    locator = result.get('locator', '')
    label = locator.rsplit('/column:', 1)[1] if '/column:' in locator else result.get('label')
    if not isinstance(label, str) or _stem(name) != _stem(label):
        _reject()
    return {'parameter': name, 'source_type': 'document', 'label': label,
            'source_uri': result['source_uri'], 'source_sha256': result['sha256'], 'locator': locator}


def validate_formula_sql_parameters(engine, formula, original_parameters, resolved_parameters, results):
    """Validate exact original refs against actual executed aggregate slots.

    Returns audit only, never rewrites/switches parameters. Optional future
    ``parameter_contracts`` must originate in the pinned formula extractor,
    not model JSON, and contain exact schema table/column/function triples.
    """
    names = formula.get('parameters')
    if (not isinstance(names, list) or len(names) > 16
            or not all(isinstance(name, str) and name for name in names)
            or len(set(names)) != len(names) or not isinstance(original_parameters, dict)
            or not isinstance(resolved_parameters, dict)
            or set(names) != set(original_parameters) or set(names) != set(resolved_parameters)):
        _reject()
    with engine._connect() as connection:
        tables, _, _ = engine._snapshot_for(connection)
    source_tables = {metric.get('table') or result.get('plan', {}).get('metric_table') or result.get('plan', {}).get('table')
                     for result in results.values() if isinstance(result, dict) and 'rows' in result
                     for metric in result.get('plan', {}).get('metrics') or [{}]}
    proofs = []
    contracts = formula.get('parameter_contracts', {})
    if not isinstance(contracts, dict) or not set(contracts) <= set(names):
        _reject()
    for name in names:
        reference = original_parameters[name]
        if (not isinstance(reference, dict) or set(reference) != {'ref', 'path'}
                or not isinstance(reference['ref'], str) or not isinstance(reference['path'], list)
                or reference['ref'] not in results):
            _reject()
        result, resolved = results[reference['ref']], resolved_parameters[name]
        if not isinstance(result, dict):
            _reject()
        if 'rows' not in result:
            proofs.append(_document_parameter(name, reference['path'], result, resolved))
            continue
        metric, row, value = _actual_metric(result, reference['path'], resolved)
        candidates = _parameter_candidates(engine, name, tables, source_tables)
        if name in contracts:
            contract = contracts[name]
            if not isinstance(contract, dict) or set(contract) != {'table', 'column', 'function'}:
                _reject()
            explicit = (contract['table'], contract['column'], contract['function'])
            if not all(isinstance(item, str) and item for item in explicit):
                _reject()
            if not any(table.name == explicit[0] and any(column.name == explicit[1] for column in table.columns) for table in tables):
                _reject()
            if explicit[2] not in {'SUM', 'AVG', 'MIN', 'MAX', 'COUNT', 'COUNT_DISTINCT'}:
                _reject()
            if candidates and explicit not in candidates:
                _reject('formula_parameter_semantics_mismatch')
            candidates = {explicit}
        actual = (metric['table'], metric['column'], metric['function'])
        if len(candidates) != 1 or actual not in candidates:
            _reject('formula_parameter_semantics_mismatch')
        proofs.append({'parameter': name, 'source_type': 'sql', 'task_id': reference['ref'],
                       'table': actual[0], 'column': actual[1], 'function': actual[2], 'row': row,
                       'verification': ('verified_document_parameter_declaration' if name in contracts
                                        else 'server_catalog_or_exact_schema_parameter_semantics')})
    return {'status': 'verified', 'scope': 'formula_parameters_to_executed_physical_aggregate_slots', 'bindings': proofs}
