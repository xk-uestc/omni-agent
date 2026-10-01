"""Server-owned, source-scoped preservation of explicit fusion SQL intent.

This deliberately supports only unambiguous literal source clauses. A model's
task text is not evidence that an omitted user condition never existed. Scope
ambiguity stops execution rather than assigning every date to every source.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re

from .nl2sql.models import MetricSpec, QueryPlan


class SourceConstraintError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__('跨源查询的来源范围尚未核验，请明确各数据库取数的表、指标、时间和包含/排除条件。')


@dataclass(frozen=True)
class SourceClause:
    id: str
    start: int
    end: int
    text: str
    role: str


_DATABASE = re.compile(r'数据库|数据表|(?<![A-Za-z0-9_])SQL(?![A-Za-z0-9_])', re.I)
_DOCUMENT = re.compile(r'文档|手册|知识库|资料|(?<![A-Za-z0-9_])(?:PDF|Excel|XLSX|DOCX)(?![A-Za-z0-9_])', re.I)
_MODIFIER = re.compile(r'^(?:不含|不包括|不包含|排除|仅|只取|只考虑|限定|限于|除.+(?:之外|以外))')
_EXCLUSION = re.compile(r'不含|不包括|不包含|排除|仅|只取|只考虑|限定|限于|除.+(?:之外|以外)')
_PURPOSE = re.compile(r'(?:作为|用作)(?:历史|预测|计算)?(?:基准|基数|输入|参数)|'
                      r'(?:进行|用于)(?:核算|计算|预测)|(?<=计数)计算|(?<=合计)计算')
_TEMPORAL = re.compile(r'(?<!\d)\d{4}(?:年|[-/]\d{1,2})')


def _pieces(question):
    """Top-level punctuation, preserving brackets around source qualifiers."""
    start, depth = 0, 0
    for index, char in enumerate(question):
        if char in '（([{':
            depth += 1
        elif char in '）)]}':
            depth = max(0, depth - 1)
        elif not depth and char in '，,。；;？！?\n':
            if question[start:index].strip():
                yield start, index
            start = index + 1
    if question[start:].strip():
        yield start, len(question)


def _table_pattern(name):
    if re.search(r'[A-Za-z0-9_]', name):
        return re.compile(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', re.I)
    return re.compile(re.escape(name))


def _outside_ranges(question, clauses):
    for left, right in _pieces(question):
        ranges = [(left, right)]
        for clause in clauses:
            remainder = []
            for start, end in ranges:
                if clause.end <= start or clause.start >= end:
                    remainder.append((start, end))
                else:
                    if start < clause.start:
                        remainder.append((start, clause.start))
                    if clause.end < end:
                        remainder.append((clause.end, end))
            ranges = remainder
        yield from ranges


def extract_source_clauses(question, schema):
    """Extract literal DB/table spans; never use the model's effective text.

    Dates in a separate prediction/Excel clause are not appended to baseline
    SQL. An unassigned year with no SQL year, or an unassigned exclusion, is
    rejected instead of disappearing. Unfamiliar source grammar may clarify.
    """
    if not isinstance(question, str) or not 1 <= len(question) <= 1000:
        raise SourceConstraintError('source_scope_unverified')
    tables = [_table_pattern(table['name']) for table in schema.get('tables', [])]
    pieces = list(_pieces(question))
    clauses, consumed = [], set()
    for number, (left, right) in enumerate(pieces):
        if number in consumed:
            continue
        text = question[left:right]
        db = list(_DATABASE.finditer(text))
        table_hits = [hit for pattern in tables for hit in pattern.finditer(text)]
        if not db and not table_hits:
            continue
        if len(db) > 1:
            raise SourceConstraintError('source_binding_ambiguous')
        # A document clause mentioning a SQL table is not sufficient evidence
        # of a database read; require a literal read connector in that case.
        if _DOCUMENT.search(text) and not db:
            read = re.search(r'(?:从|用|以|对)(?=' + '|'.join(pattern.pattern for pattern in tables) + ')', text) if tables else None
            if not read:
                raise SourceConstraintError('source_scope_unverified')
            left += read.end()
        elif db:
            marker = db[0]
            suffix = text[marker.end():]
            if re.fullmatch(r'(?:取|取值|获取|查询|读取)?\s*', suffix):
                # "金额和记录数从数据库取值" is a reverse source declaration.
                prefix = text[:marker.start()]
                connector = re.search(r'(?:从|由)\s*$', prefix)
                if connector:
                    right = left + connector.start()
                else:
                    raise SourceConstraintError('source_scope_unverified')
            else:
                left += marker.end()
                tail = question[left:right]
                wrapper = re.match(r'(?:中|内|里)?(?:的)?\s*', tail)
                left += wrapper.end()
        else:
            # Strip only a literal leading read connective, not arbitrary
            # unknown business words or qualifiers preceding a table name.
            prefix = re.match(r'\s*(?:先)?(?:从|用|以|对)\s*', text)
            if prefix:
                left += prefix.end()
        purpose = _PURPOSE.search(question[left:right])
        if purpose:
            right = left + purpose.start()
        # Adjacent explicit include/exclude modifiers belong to the same read.
        next_number = number + 1
        while next_number < len(pieces):
            nleft, nright = pieces[next_number]
            following = question[nleft:nright].strip()
            if not _MODIFIER.match(following) or _DATABASE.search(following) or _DOCUMENT.search(following):
                break
            if any(pattern.search(following) for pattern in tables):
                break
            right = nright
            consumed.add(next_number)
            next_number += 1
        while left < right and question[left].isspace():
            left += 1
        while right > left and question[right - 1].isspace():
            right -= 1
        if left == right:
            raise SourceConstraintError('source_scope_unverified')
        clauses.append(SourceClause(f'sql_scope_{len(clauses)+1}', left, right,
                                    question[left:right], 'baseline' if purpose and re.search(r'基准|基数', purpose.group()) else 'observation'))
        consumed.add(number)
    if not clauses:
        raise SourceConstraintError('source_scope_unverified')
    source_dates = {match.group() for clause in clauses for match in _TEMPORAL.finditer(clause.text)}
    for start, end in _outside_ranges(question, clauses):
        outside = question[start:end].strip()
        if _EXCLUSION.search(outside):
            raise SourceConstraintError('source_scope_unverified')
        dates = list(_TEMPORAL.finditer(outside))
        if not dates or all(match.group() in source_dates for match in dates):
            continue
        # Explicit prediction target / document parameter has a separate
        # role, not a SQL filter. All other unmatched dates are ambiguous.
        document = _DOCUMENT.search(outside)
        doc_parameter = bool(document and not re.search(r'计算|核算|算', outside[document.end():dates[0].start()]))
        target = bool(re.search(r'预测|目标', outside))
        if not doc_parameter and not target:
            raise SourceConstraintError('source_scope_unverified')
    return clauses


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return ('bool', value) if isinstance(value, bool) else value


def _filters(plan, filters):
    result = set()
    for item in filters:
        operator = item.operator.upper()
        if operator == '<>':
            operator = '!='
        value = _freeze(item.value)
        if operator in {'IN', 'NOT IN'} and isinstance(value, tuple):
            value = tuple(sorted(value, key=repr))
        result.add((item.table or plan.table, item.column, operator, value))
    return result


def _metrics(plan):
    if plan.metrics:
        return {(metric.table, metric.column, metric.function) for metric in plan.metrics}
    if plan.metric_column and plan.metric_function:
        return {(plan.metric_table or plan.table, plan.metric_column, plan.metric_function)}
    return set()


def _tables(plan):
    names = {plan.table, plan.metric_table, *plan.join_tables}
    names.update(table for table, _, _ in _metrics(plan))
    names.update(item.table or plan.table for item in plan.filters)
    names.update(plan.dimension_tables.get(column, plan.table) for column in plan.dimensions)
    return names - {None}


def _semantic_metrics(plan):
    if plan.metrics:
        return list(plan.metrics)
    if plan.metric_column and plan.metric_function:
        return [MetricSpec('legacy_scalar', plan.metric_table or plan.table,
                           plan.metric_column, plan.metric_function, plan.metric_label or '',
                           unit='count' if plan.metric_function in {'COUNT', 'COUNT_DISTINCT'} else 'unknown')]
    return []


def verify_required_intent(actual: QueryPlan, required: QueryPlan):
    """Compare executable typed slots, never display labels/question text."""
    if not isinstance(actual, QueryPlan) or not isinstance(required, QueryPlan):
        return ['source_scope_unverified']
    if required.clarification or required.coverage.get('unresolved') or not _metrics(required):
        return ['source_scope_unverified']
    errors = []
    if _tables(actual) != _tables(required):
        errors.append('source_table_mismatch')
    if _metrics(actual) != _metrics(required):
        errors.append('source_metric_mismatch')
    if _filters(actual, actual.filters) != _filters(required, required.filters):
        errors.append('source_filter_mismatch')
    def dimensions(plan):
        return {(plan.dimension_tables.get(column, plan.table), column,
                 plan.dimension_transforms.get(column, 'raw')) for column in plan.dimensions}
    if dimensions(actual) != dimensions(required):
        errors.append('source_dimension_mismatch')
    def aggregation(plan):
        having = (plan.having.operator, plan.having.mode, _freeze(plan.having.value)) if plan.having else None
        direction = plan.order_desc if plan.top_n is not None or plan.analysis_mode != 'aggregate' else None
        return (having, plan.analysis_mode, plan.top_n, direction, plan.comparison_mode, _freeze(plan.comparison_period))
    if aggregation(actual) != aggregation(required):
        errors.append('source_aggregation_mismatch')
    expected_metrics = _semantic_metrics(required)
    remaining = _semantic_metrics(actual)
    for metric in expected_metrics:
        def match(candidate):
            return ((candidate.table, candidate.column, candidate.function) == (metric.table, metric.column, metric.function)
                    and _filters(actual, candidate.filters) == _filters(required, metric.filters)
                    and candidate.missing == metric.missing
                    and (metric.unit == 'unknown' or candidate.unit == metric.unit)
                    and (metric.currency is None or candidate.currency == metric.currency))
        index = next((index for index, candidate in enumerate(remaining) if match(candidate)), None)
        if index is None:
            errors.append('source_metric_semantics_mismatch')
            break
        remaining.pop(index)
    if expected_metrics and remaining:
        errors.append('source_metric_semantics_mismatch')
    if (_freeze([item.expression for item in actual.derived_metrics]) != _freeze([item.expression for item in required.derived_metrics])):
        errors.append('source_metric_semantics_mismatch')
    return list(dict.fromkeys(errors))


def server_project_required_intent(required: QueryPlan, subset):
    """Project server-accounted metrics; keep every common scope obligation.

    The caller re-extracts the original whole source in the execution snapshot.
    Internal subsets never originate in model JSON or an evidence reference.
    """
    if (not isinstance(required, QueryPlan) or not isinstance(subset, (set, frozenset, list, tuple))
            or not subset or any(not isinstance(item, tuple) or len(item) != 3
                                or any(not isinstance(part, str) or not part for part in item) for item in subset)):
        raise SourceConstraintError('source_scope_unverified')
    selected = set(subset)
    if not selected <= _metrics(required):
        raise SourceConstraintError('source_scope_unverified')
    projected = deepcopy(required)
    if selected == _metrics(required):
        return projected
    # A derived whole-query formula/ranking is not separable by merely
    # dropping input metrics. Keep those operations on the whole source.
    if required.derived_metrics or required.top_n is not None or not required.metrics:
        raise SourceConstraintError('source_binding_ambiguous')
    if len({table for table, _, _ in _metrics(required)}) != 1:
        raise SourceConstraintError('source_binding_ambiguous')
    projected.metrics = [metric for metric in projected.metrics if (metric.table, metric.column, metric.function) in selected]
    first = projected.metrics[0]
    projected.metric_table, projected.metric_column, projected.metric_function = first.table, first.column, first.function
    projected.metric_label = first.label
    projected.output_metrics = [metric.id for metric in projected.metrics]
    return projected


def bind_source_constraints(question, tasks, engine):
    """Server generates QueryPlans and accounts for every explicit SQL scope.

    One clear source maps directly to one SQL step. Literal metric subsets may
    split across steps only with exact whole-source union coverage. Multiple
    sources require unique columns or an exact verified typed intent match.
    Dynamic textual refs are supported only for a unique single-source step;
    its resolved executable plan is still checked against this server plan.
    """
    sql_tasks = [task for task in tasks if task['tool'] == 'sql']
    if not sql_tasks:
        # Do not turn an explicitly requested database read into a successful
        # document-only graph by omitting all SQL tasks. Pure document DAGs
        # without a database/table anchor remain applicable to their own tools.
        if _DATABASE.search(question):
            raise SourceConstraintError('source_scope_unverified')
        schema = engine.schema(include_row_count=False)
        if any(_table_pattern(table['name']).search(question) for table in schema.get('tables', [])):
            raise SourceConstraintError('source_scope_unverified')
        return {}, []
    try:
        clauses = extract_source_clauses(question, engine.schema(include_row_count=False))
    except SourceConstraintError:
        # A self-contained ordinary SQL question can arrive on a fusion route
        # with an actual SQL task. Preserve and verify its entire original
        # text; do not require an artificial literal "database" declaration.
        # This is not an escape for documents or an ambiguous short followup.
        if _DATABASE.search(question) or _DOCUMENT.search(question):
            raise
        complete = engine.extract_required_intent(question)
        if complete.clarification or complete.coverage.get('unresolved') or not _metrics(complete):
            raise
        clauses = [SourceClause('sql_scope_1', 0, len(question), question, 'observation')]
    # A standalone value qualifier before a source clause must not disappear
    # merely because it omitted an explicit include/exclude word. Actual DB
    # values are discovered by the existing schema/value index, not a domain
    # dictionary. Explicit document/target clauses have separate scopes.
    for start, end in _outside_ranges(question, clauses):
        outside = question[start:end].strip()
        if outside and not _DOCUMENT.search(outside) and not re.search(r'预测|目标', outside):
            slots = engine.analyze_slots(outside)
            if slots.get('values') or slots.get('dimensions'):
                raise SourceConstraintError('source_scope_unverified')
    plans = [engine.extract_required_intent(clause.text) for clause in clauses]
    if any(plan.clarification or plan.coverage.get('unresolved') or not _metrics(plan) for plan in plans):
        raise SourceConstraintError('source_scope_unverified')
    if len(clauses) == len(sql_tasks) == 1:
        return {sql_tasks[0]['id']: plans[0]}, [{'task_id': sql_tasks[0]['id'], **clauses[0].__dict__}]
    bound, audit, covered = {}, [], {index: set() for index in range(len(plans))}
    for task in sql_tasks:
        text = task['args'].get('question')
        if not isinstance(text, str):
            raise SourceConstraintError('source_binding_ambiguous')
        candidate = engine.extract_required_intent(text)
        candidate_columns = {(table, column) for table, column, _ in _metrics(candidate)}
        matches = [index for index, plan in enumerate(plans)
                   if candidate_columns and candidate_columns <= {(table, column) for table, column, _ in _metrics(plan)}]
        if len(matches) > 1:
            matches = [index for index in matches if not verify_required_intent(candidate, plans[index])]
        if len(matches) != 1:
            raise SourceConstraintError('source_binding_ambiguous')
        index = matches[0]
        subset = _metrics(candidate)
        if not subset or not subset <= _metrics(plans[index]):
            raise SourceConstraintError('source_scope_unverified')
        required = server_project_required_intent(plans[index], subset)
        required.source_metric_subset = frozenset(subset)
        covered[index].update(subset)
        bound[task['id']] = required
        audit.append({'task_id': task['id'], **clauses[index].__dict__})
    if any(covered[index] != _metrics(plan) for index, plan in enumerate(plans)):
        raise SourceConstraintError('source_scope_unverified')
    return bound, audit
