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
from .nl2sql.schema import normalize_text


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
    qualifier_ranges: tuple[tuple[int, int], ...] = ()
    target_binding: dict | None = None
    document_search_ranges: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class VerifiedFormulaTarget:
    """Internal proof emitted only after a pinned document formula read.

    This type is not a model/API request field. A label selected in model
    JSON is insufficient: the caller must obtain it from the actual document
    tool result and keep the source version pinned during execution.
    """
    label: str
    document_id: str
    source_sha256: str
    task_id: str


_DATABASE = re.compile(r'数据库|数据表|(?<![A-Za-z0-9_])SQL(?![A-Za-z0-9_])', re.I)
_DOCUMENT = re.compile(r'文档|手册|知识库|资料|(?<![A-Za-z0-9_])(?:PDF|Excel|XLSX|DOCX)(?![A-Za-z0-9_])', re.I)
_MODIFIER = re.compile(r'^(?:不含|不包括|不包含|排除|仅|只取|只考虑|限定|限于|除.+(?:之外|以外))')
_EXCLUSION = re.compile(r'不含|不包括|不包含|排除|仅|只取|只考虑|限定|限于|除.+(?:之外|以外)')
_PURPOSE = re.compile(r'(?:作为|用作)(?:历史|预测|计算)?(?:基准|基数|输入|参数)|'
                      r'(?:进行|用于)(?:核算|计算|预测)|(?<=计数)计算|(?<=合计)计算')
_TEMPORAL = re.compile(r'(?<!\d)\d{4}(?:年|[-/]\d{1,2})')
_TARGET_ROLE = re.compile(r'预测|目标|基准|基数|历史|未来|预计|预估|计划')
# Unassigned non-document text must be an entire structural/read/output
# clause, not merely lack a known filter keyword. Unknown business conditions
# remain unbound even when their values are absent from the schema index.
_OUTSIDE_STRUCTURE = re.compile(
    r'\s*(?:请)?(?:'
    r'(?:先|再|然后)?(?:作为过滤条件)?(?:比较|对比|查询|查|读取|获取|统计)?(?:从|由|用|以|对|与|和|以及)?'
    r'(?:数据库|数据表|SQL)(?:中|内|里|的)?(?:取|取值|获取|查询|查|读取)?'
    r'|(?:先|再|然后)?(?:从|由|用|以|对|与|和)'
    r'|作为(?:输入|参数|基准)'
    r'|(?:并|再|然后)?(?:作为(?:输入|参数|基准))?(?:进行|用于)?(?:核算|计算|比较|对比)'
    r'|(?:并|再|然后)?(?:保留|附上|给出|返回|展示|显示|输出)(?:原文)?(?:来源|引用|证据|结果|答案)'
    r'|(?:是多少|多少|多少钱|几个|几条|几笔|是否|吗|呢))\s*', re.I)


def _known_prediction_target(text, engine, clauses):
    """A separate future target can name a complete known metric, not a filter."""
    match = re.fullmatch(r'(?:预测|预计|预估)(?:\d{4}年)(?:的)?(.{1,128})', text)
    if not match:
        return False
    metric = engine.extract_required_intent(match.group(1))
    if (not metric.clarification and not metric.coverage.get('unresolved') and bool(_metrics(metric))
            and not metric.filters and not metric.dimensions):
        return True
    # A short target descriptor (e.g. a prefix of one complete source column)
    # never defines a new SQL metric. It is allowed only as a literal prefix
    # of one independently recognized source metric, with no added qualifier.
    target = normalize_text(match.group(1))
    matches = {(link.table, link.column) for clause in clauses
               for link in engine.analyze_slots(clause.text).get('metrics', [])
               if len(target) >= 2 and normalize_text(link.matched_alias).startswith(target)}
    return len(matches) == 1


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
            target_range = (clause.target_binding or {}).get('range')
            for clause_start, clause_end in ((clause.start, clause.end), *clause.qualifier_ranges,
                                            *clause.document_search_ranges,
                                            *((target_range,) if target_range else ())):
                remainder = []
                for start, end in ranges:
                    if clause_end <= start or clause_start >= end:
                        remainder.append((start, end))
                    else:
                        if start < clause_start:
                            remainder.append((start, clause_start))
                        if clause_end < end:
                            remainder.append((clause_end, end))
                ranges = remainder
        yield from ranges


def _verified_formula_target(target_metric, clause, engine, verified_formula_targets):
    """Account for the COMPLETE target noun, including unknown qualifiers."""
    proofs = []
    for proof in verified_formula_targets:
        if (not isinstance(proof, VerifiedFormulaTarget) or not isinstance(proof.label, str)
                or not 1 <= len(proof.label) <= 128
                or not isinstance(proof.document_id, str) or not proof.document_id
                or not isinstance(proof.source_sha256, str)
                or not re.fullmatch(r'[0-9a-f]{64}', proof.source_sha256)
                or not isinstance(proof.task_id, str)
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,40}', proof.task_id)):
            raise SourceConstraintError('source_scope_unverified')
        if proof.label == target_metric:
            proofs.append(proof)
    distinct = {(proof.document_id, proof.source_sha256, proof.label) for proof in proofs}
    if len(distinct) > 1:
        raise SourceConstraintError('source_binding_ambiguous')
    if proofs:
        proof = proofs[0]
        return {'mode': 'verified_document_formula_exact_label', **proof.__dict__,
                'formula_task_ids': tuple(sorted({item.task_id for item in proofs}))}
    target_plan = engine.extract_required_intent(target_metric)
    source_plan = engine.extract_required_intent(clause.text)
    catalog = getattr(engine, 'metric_catalog', None)
    aliases = {normalize_text(item.label) for item in target_plan.derived_metrics}
    if catalog:
        aliases.update(alias for item in target_plan.derived_metrics
                       for alias in catalog.aliases.get(item.id, ()))
    normalized_target = normalize_text(target_metric)
    # coverage may discard filler words (e.g. "有效"); use a positive exact
    # label grammar too. An explicitly consumed average operator is the only
    # supported prefix, never an arbitrary business noun/condition.
    exact_target = normalized_target in aliases
    if (normalized_target.startswith('平均') and normalized_target[2:] in aliases
            and '平均' in target_plan.coverage.get('consumed', [])):
        exact_target = True
    if (not target_plan.clarification and not target_plan.coverage.get('unresolved')
            and exact_target and target_plan.derived_metrics and _metrics(target_plan)
            and not source_plan.clarification and not source_plan.coverage.get('unresolved')
            and _metrics(target_plan) <= _metrics(source_plan)):
        labels = {item.label for item in target_plan.derived_metrics}
        actual_proofs = [item for item in verified_formula_targets if item.label in labels]
        distinct = {(item.document_id, item.source_sha256, item.label) for item in actual_proofs}
        if len(distinct) > 1 or len(labels) != 1:
            raise SourceConstraintError('source_binding_ambiguous')
        if actual_proofs:
            proof = actual_proofs[0]
            return {'mode': 'verified_document_formula_catalog_target', **proof.__dict__,
                    'formula_task_ids': tuple(sorted({item.task_id for item in actual_proofs})),
                    'target': target_metric, 'derived_labels': sorted(labels)}
        # Scope extraction can use a catalog without executing a document
        # graph, but an actual calculation must still consume the verified
        # formula task/label contract. Empty task IDs cannot authorize a DAG.
        return {'mode': 'server_semantic_catalog_complete_target', 'target': target_metric,
                'label': next(iter(labels)), 'formula_task_ids': (), 'derived_labels': sorted(labels)}
    raise SourceConstraintError('source_scope_unverified')


def _attach_reverse_target_scope(question, clauses, pieces, engine, verified_formula_targets):
    """Attach literal target qualifiers to one reverse DB declaration.

    This is not general inheritance: there must be one DB read, one preceding
    calculation target, and no intervening non-document source. Values come
    from the current database index, never model-generated effective text.
    Only a contiguous literal time/value prefix is transferred; the formula
    target's metric stays out of the database input contract.
    """
    if engine is None or len(clauses) != 1:
        return clauses
    clause = clauses[0]
    source_piece = next((index for index, (left, right) in enumerate(pieces)
                         if left <= clause.start < right), None)
    if source_piece is None or not source_piece:
        return clauses
    left, right = pieces[source_piece]
    source = question[left:right]
    if not re.search(r'(?:从|由)\s*(?:数据库|数据表|SQL)\s*(?:取|取值|获取|查询|读取)?\s*$', source, re.I):
        return clauses
    # The first request is the sole possible shared target. Intermediate
    # clauses must explicitly assign only the formula to documents.
    for start, end in pieces[1:source_piece]:
        between = question[start:end]
        if (not _DOCUMENT.search(between) or not re.search(r'公式', between)
                or _DATABASE.search(between) or _TEMPORAL.search(between)
                or _EXCLUSION.search(between)):
            return clauses
    start, end = pieces[0]
    target = question[start:end]
    if _DATABASE.search(target) or _EXCLUSION.search(target):
        return clauses
    target_role = bool(_TARGET_ROLE.search(target))
    if _DOCUMENT.search(target):
        # Distinguish document-parameter dates from the requested calculation
        # target: the qualifiers must occur AFTER an explicit formula action.
        action = re.search(r'公式\s*(?:(?:文档|手册|资料|知识库)\s*)?(?:计算|核算|求|算)', target)
        if not action:
            if target_role:
                raise SourceConstraintError('source_scope_unverified')
            return clauses
        offset = action.end()
    else:
        offset = 0
    literal_target = target[offset:]
    slots = engine.analyze_slots(literal_target)
    dates = slots.get('time_spans', [])
    time_ranges = [(match.start(), match.end()) for literal in dates
                   for match in re.finditer(re.escape(literal), slots.get('normalized', literal_target))]
    # The value index can match a stored date's year head. A year already
    # accounted for by the time parser is not a separate business entity.
    values = [value for value in slots.get('values', [])
              if not any(left <= value.start and value.end <= right for left, right in time_ranges)]
    if (len(set(dates)) > 1 or any(getattr(value, 'via', '') != 'exact' for value in values)
            or len({(value.table, value.column, value.value) for value in values}) != len(values)
            or len({(value.table, value.column) for value in values}) != len(values)):
        raise SourceConstraintError('source_binding_ambiguous')
    if not dates and not values:
        return clauses
    spans = []
    for literal in dates + [value.span for value in values]:
        occurrences = list(re.finditer(re.escape(literal), literal_target))
        if len(occurrences) != 1:
            raise SourceConstraintError('source_binding_ambiguous')
        spans.append((occurrences[0].start(), occurrences[0].end()))
    spans.sort()
    qleft, qright = spans[0][0], max(item[1] for item in spans)
    prefix = literal_target[:qleft]
    # Only an explicit request verb may precede the literal qualifiers.
    if not re.fullmatch(r'\s*(?:(?:请|查询|计算|核算|求|统计|展示|看看)\s*)*', prefix):
        return clauses
    covered = [False] * (qright - qleft)
    for begin, finish in spans:
        for index in range(begin - qleft, finish - qleft):
            covered[index] = True
    residue = ''.join(char for index, char in enumerate(literal_target[qleft:qright]) if not covered[index])
    if not re.fullmatch(r'[\s的]*(?:(?:地区|区域)[\s的]*)?', residue):
        return clauses
    region_suffix = re.match(r'(?:地区|区域)?(?:的)?', literal_target[qright:])
    qright += region_suffix.end()
    target_metric = literal_target[qright:].strip()
    if not target_metric or len(target_metric) > 128:
        raise SourceConstraintError('source_scope_unverified')
    target_binding = _verified_formula_target(target_metric, clause, engine, verified_formula_targets)
    target_start, target_end = start + offset + qright, end
    while target_start < target_end and question[target_start].isspace():
        target_start += 1
    while target_end > target_start and question[target_end - 1].isspace():
        target_end -= 1
    target_binding['range'] = (target_start, target_end)
    target_binding['target_id'] = f'target:{target_start}:{target_end}'
    target_binding['source_clause_ids'] = (clause.id,)
    if _DOCUMENT.search(target):
        # Cover the prefix too: repeating the metric suffix does not authorize
        # an unknown business qualifier hidden before "formula calculate".
        before = target[:action.start()].strip()
        if before.endswith(target_metric):
            before = before[:-len(target_metric)]
        grammar = r'(?:按|根据|依据)?(?:指标|经营|核算|计算|预测)?(?:计算)?(?:文档|手册|资料|知识库)?(?:中|内|里)?(?:的)?(?:预测|目标|基准|基数|历史|未来|预计|预估|计划)?'
        if not re.fullmatch(grammar, before):
            raise SourceConstraintError('source_scope_unverified')
    qualifier = literal_target[qleft:qright]
    local_slots = engine.analyze_slots(clause.text)
    local_dates = set(local_slots.get('time_spans', []))
    local_time_ranges = [(match.start(), match.end()) for literal in local_dates
                         for match in re.finditer(re.escape(literal), local_slots.get('normalized', clause.text))]
    local_values = {(value.table, value.column, value.value) for value in local_slots.get('values', [])
                    if not any(left <= value.start and value.end <= right for left, right in local_time_ranges)}
    inherited_values = {(value.table, value.column, value.value) for value in values}
    if target_role:
        # A complete target still needs its forecast/baseline role separated
        # from the DB read. Even with explicit baseline filters, validating
        # the full target above is mandatory; unknown qualifiers may not hide.
        if ((dates and not local_dates)
                or not {(value.table, value.column) for value in values}
                <= {(table, column) for table, column, _ in local_values}):
            raise SourceConstraintError('source_scope_unverified')
        return [SourceClause(clause.id, clause.start, clause.end, clause.text, clause.role,
                             target_binding=target_binding)]
    if ((values and _EXCLUSION.search(clause.text))
            or (local_dates and dates and local_dates != set(dates))
            or (local_values and inherited_values and local_values != inherited_values)):
        raise SourceConstraintError('source_binding_ambiguous')
    absolute = (start + offset + qleft, start + offset + qright)
    return [SourceClause(clause.id, clause.start, clause.end, qualifier + ' ' + clause.text,
                         clause.role, (absolute,), target_binding)]


def _attach_postfix_formula_target(question, clauses, pieces, engine, proofs):
    """Bind a complete trailing calculation target, without borrowing its dates.

    A single explicit SQL source is the only supported antecedent. Temporal
    and exact entity prefixes qualify the calculation, never the SQL input.
    """
    requests = [(left, right, re.match(r'\s*(?:再|然后)?(?:计算|核算|求|算)\s*', question[left:right]))
                for left, right in pieces
                if left > max(clause.end for clause in clauses)
                and not any(left <= clause.start < right for clause in clauses)]
    requests = [(left, right, action) for left, right, action in requests if action]
    if not requests:
        return clauses
    if engine is None or len(requests) != 1 or len(clauses) != 1:
        raise SourceConstraintError('source_binding_ambiguous')
    left, right, action = requests[0]
    clause = clauses[0]
    if left <= clause.end or clause.target_binding:
        raise SourceConstraintError('source_binding_ambiguous')
    target = question[left + action.end():right].strip()
    year = re.match(r'(?P<year>\d{4})年(?:的)?', target)
    target_year = int(year.group('year')) if year else None
    if year:
        target = target[year.end():]
    # Consume one literal schema-index entity only at the beginning. An
    # unknown business modifier stays in the complete target noun and rejects.
    slots = engine.analyze_slots(target)
    values = [value for value in slots.get('values', []) if value.start == 0 and getattr(value, 'via', '') == 'exact']
    entity = None
    if values:
        if len({(value.table, value.column, value.value) for value in values}) != 1:
            raise SourceConstraintError('source_binding_ambiguous')
        value = values[0]
        entity = {'table': value.table, 'column': value.column, 'value': value.value}
        target = target[value.end:]
        suffix = re.match(r'(?:地区|区域)?(?:的)?', target)
        target = target[suffix.end():]
        source_values = {(item.table, item.column, item.value)
                         for item in engine.analyze_slots(clause.text).get('values', [])}
        if (value.table, value.column, value.value) not in source_values:
            raise SourceConstraintError('source_binding_ambiguous')
    target = target.strip()
    if not target or len(target) > 128 or _DATABASE.search(target) or _DOCUMENT.search(target):
        raise SourceConstraintError('source_scope_unverified')
    binding = _verified_formula_target(target, clause, engine, proofs)
    binding.update({'range': (left, right), 'target_id': f'target:{left}:{right}',
                    'source_clause_ids': (clause.id,), 'target_year': target_year,
                    'target_entity': entity, 'placement': 'explicit_postfix_calculation'})
    return [SourceClause(clause.id, clause.start, clause.end, clause.text, clause.role,
                         (*clause.qualifier_ranges, (left, right)), binding, clause.document_search_ranges)]


def extract_source_clauses(question, schema, *, engine=None, verified_formula_targets=(),
                           verified_document_search_ranges=()):
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
    clauses = _attach_reverse_target_scope(question, clauses, pieces, engine, verified_formula_targets)
    clauses = _attach_postfix_formula_target(question, clauses, pieces, engine, verified_formula_targets)
    ranges = []
    for item in verified_document_search_ranges:
        if (not isinstance(item, (tuple, list)) or len(item) != 2
                or any(not isinstance(part, int) or isinstance(part, bool) for part in item)):
            raise SourceConstraintError('source_scope_unverified')
        left, right = item
        if (not 0 <= left < right <= len(question) or (left, right) not in pieces
                or left < min(clause.end for clause in clauses)
                or any(left < clause.end and right > clause.start for clause in clauses)
                or any(left < qright and right > qleft for clause in clauses
                       for qleft, qright in clause.qualifier_ranges)):
            raise SourceConstraintError('source_scope_unverified')
        ranges.append((left, right))
    if ranges:
        clauses = [SourceClause(clause.id, clause.start, clause.end, clause.text, clause.role,
                                clause.qualifier_ranges, clause.target_binding, tuple(sorted(set(ranges))))
                   for clause in clauses]
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
            # Diagnose only an unambiguous physical metric. Static codes
            # retain no rejected model text or provider response content.
            physical = [candidate for candidate in remaining
                        if (candidate.table, candidate.column, candidate.function)
                        == (metric.table, metric.column, metric.function)]
            if len(physical) == 1:
                candidate = physical[0]
                if _filters(actual, candidate.filters) != _filters(required, metric.filters):
                    errors.append('source_metric_local_filters_mismatch')
                if candidate.missing != metric.missing:
                    errors.append('source_metric_missing_policy_mismatch')
                if metric.unit != 'unknown' and candidate.unit != metric.unit:
                    errors.append('source_metric_unit_mismatch')
                if metric.currency is not None and candidate.currency != metric.currency:
                    errors.append('source_metric_currency_mismatch')
            break
        remaining.pop(index)
    if expected_metrics and remaining:
        errors.append('source_metric_semantics_mismatch')
    if (_freeze([item.expression for item in actual.derived_metrics]) != _freeze([item.expression for item in required.derived_metrics])):
        errors.append('source_metric_semantics_mismatch')
        errors.append('source_derived_metric_expression_mismatch')
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


def bind_source_constraints(question, tasks, engine, *, verified_formula_targets=(),
                            verified_document_search_ranges=()):
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
        clauses = extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine,
                                        verified_formula_targets=verified_formula_targets,
                                        verified_document_search_ranges=verified_document_search_ranges)
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
        if outside and not _DOCUMENT.search(outside):
            if _known_prediction_target(outside, engine, clauses):
                continue
            slots = engine.analyze_slots(outside)
            if (slots.get('values') or slots.get('dimensions')
                    or _OUTSIDE_STRUCTURE.fullmatch(outside) is None):
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
