"""Conservative source roles from the original question and actual Schema.

JOIN descriptions may be removed from metric linking only after a complete
foreign-key constraint is verified. Physical field ownership is independent
of whether the field is a measure, filter, group key or association key.
"""
from __future__ import annotations

import re
import unicodedata

from .t2s import to_simplified
from .date_semantics import is_date_column
from .schema_profile import table_aliases, infer_rules


def normalized(text):
    return to_simplified(re.sub(r'\s+', '', unicodedata.normalize('NFKC', text or '')).lower())


def mentions(text, name):
    return list(re.finditer(r'(?<![a-z0-9_])' + re.escape(normalized(name)) + r'(?![a-z0-9_])', text))


def named_tables(text, tables):
    return {table.name for table in tables if mentions(text, table.name)}


def alias_owner_prefix(text, start, tables, alias_tables=None):
    """Bind an immediately preceding real table/known alias, never global context.

    Shared table aliases retain all owners. Unknown source words remain in the
    question for the existing coverage guard rather than becoming authority.
    """
    candidates = []
    for table in tables:
        for alias in table_aliases(table.name):
            pattern = (r'(?<![a-z0-9_])' + re.escape(normalized(alias))
                       + r'(?:表的|中的|内的|里的|的|表中|中|\.)?$')
            match = re.search(pattern, text[:start])
            # “各客户的金额” identifies a grouping subject, not the owner
            # of the subsequent metric. Negated subjects authorize no owner.
            if match and not re.search(
                    r'(?:各|每个|每一|除了|除去|除|不含|不包括|不包含|排除|剔除|非)$',
                    text[:match.start()]):
                candidates.append((match.start(), table.name, alias == table.name))
    if not candidates:
        return start, set()
    first = min(position for position, _, _ in candidates)
    chosen = {owner for position, owner, _ in candidates if position == first}
    native = any(is_native for position, _, is_native in candidates if position == first)
    # A business subject can name a related filter (“高等级客户的销售额”).
    # A Chinese table alias owns a field only if that field concept exists
    # there. A literal Schema owner never borrows a field from another table.
    if len(chosen) == 1 and not native and alias_tables is not None and not chosen & alias_tables:
        return start, set()
    return first, chosen


def _group_spans(text, metric_rules=()):
    """Local grouping syntax; 'each latest record' is not an aggregate grain."""
    spans = [(m.start(1), m.end(1)) for m in re.finditer(
        r'(?<!不)按([^,;。?!？；]{1,160}?)(?:分组|统计|计算|汇总|拆开|拆分|分开)', text)
        if not re.search(r'筛选|过滤|限定', m.group(1))]
    # Chinese ``各X指标`` and ``每个X指标`` omit an explicit group-by verb.
    # A real metric alias provides the boundary without guessing the dimension.
    metric_starts = set()
    dimension_aliases = [normalized(alias)
        for rule in metric_rules if getattr(rule, 'role', None) == 'dimension'
        for alias in getattr(rule, 'aliases', ()) if len(normalized(alias)) >= 2]
    for rule in metric_rules:
        if getattr(rule, 'role', None) != 'metric':
            continue
        for alias in getattr(rule, 'aliases', ()):
            normalized_alias = normalized(alias)
            if len(normalized_alias) < 2:
                continue
            for match in re.finditer(re.escape(normalized_alias), text):
                if not any(len(alias) > len(normalized_alias) and text.startswith(alias, match.start())
                           for alias in dimension_aliases):
                    metric_starts.add(match.start())
    for marker in re.finditer(r'各|每个|每一', text):
        metric_start = min((start for start in metric_starts if start >= marker.end()), default=None)
        if metric_start is None:
            continue
        scope = text[marker.end():metric_start]
        if (0 < len(scope) <= 80 and not re.search(
                r'筛选|过滤|限定|分组|统计|计算|汇总|按月|按年', scope)):
            spans.append((marker.end(), metric_start))
    # A bare physical identifier is admitted only at an aggregate clause,
    # not from '每个X最新记录' or an arbitrary occurrence of '各'.
    spans.extend((m.start(1), m.end(1)) for m in re.finditer(
        r'(?:各|每个|每一)([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)'
        r'(?![a-z0-9_])(?:的)?(?=[^,;。?!？；]{0,80}?(?:合计|总额|平均|均值|数量|计数|记录数|分组|汇总))', text)
        if not re.search(r'最新|最早|最近|第一|最后', text[m.end():
            next((p for p in range(m.end(), len(text)) if text[p] in ',;。?!？；'), len(text))]))
    return spans


def excluded_filter_scope(text, tables):
    """Mask only a local instruction NOT to use a date as a filter for linking.

    The original question stays untouched for planning and independent review.
    This is not value negation: '不按amount>10过滤' remains fully visible.
    """
    types = {normalized(c.name): c.data_type for t in tables for c in t.columns}
    known_tables = {normalized(t.name) for t in tables}
    date_pairs = {(t.name, c.name) for t in tables for c in t.columns
                  if is_date_column(c.name, c.data_type)}
    date_aliases = {normalized(alias) for rule in infer_rules(tables)
                    if (rule.table, rule.column) in date_pairs for alias in rule.aliases}
    spans = []
    for match in re.finditer(r'(?:不按照|不要按|不按)([^,;。?!？；]{1,120}?)(?:进行)?(?:筛选|过滤)', text):
        parts = re.split(r'或者|以及|或|和|与|、|及', match.group(1))
        valid = True
        for part in parts:
            if part in date_aliases and re.fullmatch(r'[\u3400-\u9fff]{1,12}(?:日期|时间)', part):
                continue
            # Chinese shared suffix: 租赁或归还日期.
            if (re.fullmatch(r'[\u3400-\u9fff]{1,10}', part)
                    and re.fullmatch(r'[\u3400-\u9fff]{1,12}(?:日期|时间)', parts[-1])
                    and any(part + suffix in date_aliases for suffix in ('日期', '时间'))):
                continue
            tokens = part.split('.')
            if len(tokens) not in (1, 2) or (len(tokens) == 2 and tokens[0] not in known_tables):
                valid = False
                break
            column = tokens[-1]
            if column not in types or not is_date_column(column, types[column]):
                valid = False
                break
            if len(tokens) == 2 and not any(normalized(t.name) == tokens[0]
                    and any(normalized(c.name) == column for c in t.columns) for t in tables):
                valid = False
                break
        if valid:
            spans.append(match.span())
    masked = text
    for start, end in reversed(spans):
        masked = masked[:start] + ' ' * (end-start) + masked[end:]
    return masked, spans


def alias_in_group(text, start, end, metric_rules=()):
    """Only this occurrence's local grouping clause gives a numeric alias a role."""
    return any(a <= start and end <= b for a, b in _group_spans(text, metric_rules))


def _qualified_fields(text, tables):
    """Read explicit owners without borrowing a homonymous field elsewhere."""
    refs, spans = set(), []
    known = {normalized(t.name): {normalized(c.name) for c in t.columns} for t in tables}
    for match in re.finditer(r'(?<![a-z0-9_])([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)(?![a-z0-9_])', text):
        if match[1] not in known or match[2] not in known[match[1]]:
            return refs, spans, False
    columns = {c.name for t in tables for c in t.columns}
    for table in tables:
        prefix = (r'(?<![a-z0-9_])' + re.escape(normalized(table.name))
                  + r'["\]`]?(?:\.|表的|中的|内的|里的|的|表中|中)["\[`]?')
        for match in re.finditer(prefix, text):
            candidates = [column for column in columns if re.match(
                re.escape(normalized(column)) + r'(?![a-z0-9_])', text[match.end():])]
            if len(candidates) != 1 or not any(c.name == candidates[0] for c in table.columns):
                return refs, spans, False
            column = candidates[0]
            refs.add((table.name, column))
            spans.append((match.start(), match.end() + len(normalized(column))))
    if any(not any(start <= match.start() < end for start, end in spans)
           for match in re.finditer(r'\.', text)):
        return refs, spans, False
    return refs, spans, True


def association_scope(question, tables):
    text = normalized(question)
    owners = named_tables(text, tables)
    bindings, unverified, spans = [], [], []
    for match in re.finditer(r'(?:通过|经由)[^,;。?!？；]{1,256}?(?:关联|连接)[^,;。?!？；]*', text):
        clause = match.group()
        # Never mask a filter, metric or an incomplete/unknown relationship.
        relation = re.search(r'关联|连接', clause)
        suffix = clause[relation.end():]
        if re.search(r'筛选|等于|不等于|大于|小于|为|>=|<=|(?<![<>!])=', clause):
            unverified.append(clause)
            continue
        if suffix:
            # Middle-verb syntax is valid only for an explicit physical RHS,
            # never for a natural-language filter or additional requested metric.
            right_fields, right_spans, right_valid = _qualified_fields(suffix, tables)
            remainder = suffix
            for start, end in sorted(right_spans, reverse=True):
                remainder = remainder[:start] + ' ' * (end-start) + remainder[end:]
            remainder = re.sub(r'以及|与|和|及|到|至', '', remainder)
            if not right_valid or not right_fields or remainder.strip(' ."[]`'):
                unverified.append(clause)
                continue
        boundary = max((m.end() for m in re.finditer(r'[;。?!？；]', text[:match.start()])), default=0)
        prefix = text[boundary:match.start()]
        local_owners = named_tables(prefix + clause, tables)
        if re.search(r'与|和|以及', prefix) and not re.search(r'统计|计算|合计|汇总|平均|筛选|过滤|分组', prefix):
            remainder = prefix
            for table in sorted(tables, key=lambda t: len(normalized(t.name)), reverse=True):
                for occurrence in reversed(mentions(remainder, table.name)):
                    remainder = remainder[:occurrence.start()] + ' ' + remainder[occurrence.end():]
            remainder = re.sub(r'以及|与|和|及|表|的', '', remainder)
            if remainder.strip(' ,."[]`'):
                unverified.append(clause)
                continue
        qualified, qualified_spans, valid = _qualified_fields(clause, tables)
        if not valid:
            unverified.append(clause)
            continue
        # Every word being hidden must belong to the verified relationship,
        # including bare fields with natural-language source qualification.
        residue = clause
        names = {t.name for t in tables} | {c.name for t in tables for c in t.columns}
        for name in sorted(names, key=lambda value: len(normalized(value)), reverse=True):
            for occurrence in reversed(mentions(residue, name)):
                residue = residue[:occurrence.start()] + ' ' + residue[occurrence.end():]
        residue = re.sub(r'通过|经由|关联|连接|外键|字段|对应|进行|以及|与|和|及|到|表|的|中|内|里', '', residue)
        if residue.strip(' ."[]`'):
            unverified.append(clause)
            continue
        bare_columns = {c.name for t in tables for c in t.columns if any(
            not any(start <= occurrence.start() and occurrence.end() <= end for start, end in qualified_spans)
            for occurrence in mentions(clause, c.name))}
        possibilities = []
        for child in tables:
            grouped = {}
            for fk in child.foreign_keys:
                grouped.setdefault((fk.table, fk.constraint_id), []).append(fk)
            for (parent, _), keys in grouped.items():
                columns = {(child.name, k.from_column) for k in keys} | {(parent, k.to_column) for k in keys}
                # Every key of a composite FK must be explicitly named.
                names = {k.from_column for k in keys} | {k.to_column for k in keys}
                if child.name not in owners or parent not in owners or not all(mentions(clause, n) for n in names):
                    continue
                if not local_owners.issubset({child.name, parent}):
                    continue
                relevant = qualified | {(t.name, c.name) for t in tables if t.name in {child.name, parent}
                                        for c in t.columns if c.name in bare_columns}
                if not relevant.issubset(columns):
                    continue
                possibilities.append({'child': child.name, 'parent': parent,
                    'keys': [(k.from_column, k.to_column) for k in sorted(keys, key=lambda k: k.sequence)],
                    'source_text': clause})
        if len(possibilities) != 1:
            unverified.append(clause)
        else:
            bindings.append(possibilities[0])
            spans.append(match.span())
    masked = text
    for start, end in reversed(spans):
        masked = masked[:start] + ' ' * (end-start) + masked[end:]
    return masked, bindings, unverified


def field_owners(text, tables):
    """Owner sets; an ambiguous bare occurrence keeps ALL possible owners."""
    named = named_tables(text, tables)
    groups = _group_spans(text)
    result = {}
    for column in {c.name for t in tables for c in t.columns}:
        possible = {t.name for t in tables if any(c.name == column for c in t.columns)}
        qualified = []
        for owner in possible:
            pattern = (r'(?<![a-z0-9_])' + re.escape(normalized(owner))
                       + r'(?:\.|表的|中的|内的|里的|的|表中|中)'
                       + re.escape(normalized(column)) + r'(?![a-z0-9_])')
            qualified.extend((m.start(), m.end(), owner) for m in re.finditer(pattern, text))
        chosen = {owner for _, _, owner in qualified}
        for occurrence in mentions(text, column):
            if any(start <= occurrence.start() and occurrence.end() <= end for start, end, _ in qualified):
                continue
            group = next(((a, b) for a, b in groups if a <= occurrence.start() < b), None)
            local = named_tables(text[group[0]:group[1]], tables) if group else set()
            # A separately requested '按X' grain can belong to a related
            # entity and must NOT inherit an unrelated metric's source.
            per_entity = group and re.search(r'(?:各|每个|每一)$', text[:group[0]])
            # An unknown qualified owner must not borrow some other table
            # mentioned elsewhere (OtherVendors.Name != Vendors.Name).
            unknown_qualified = bool(re.search(r'[a-z0-9_]+\.$', text[:occurrence.start()]))
            scoped = (set() if unknown_qualified else
                      possible & (local or (named if not group or per_entity else set())))
            chosen.update(scoped if len(scoped) == 1 else possible)
        if chosen:
            result[column] = chosen
    return result


def filter_only(text, column):
    occurrences = mentions(text, column)
    return bool(occurrences) and all(re.match(
        r'(?:为|是|等于|不等于|为空|非空|不为空|=|!=|>=|<=|>|<|isnull|isnotnull)',
        text[m.end():]) for m in occurrences)


def group_fields(text, tables, alias_rules=()):
    alias_rules = tuple(alias_rules) or tuple(infer_rules(tables))
    groups = _group_spans(text, alias_rules)
    ownership = field_owners(text, tables)
    fields = set()
    for start, end in groups:
        scope = text[start:end]
        for column, owners in field_owners(scope, tables).items():
            # A local explicit owner wins; a bare group name uses the whole
            # question's uniquely named source, otherwise retains ambiguity.
            if len(owners) > 1:
                owners = ownership.get(column, owners)
            fields.update((owner, column) for owner in owners)
        alias_matches = []
        for rule in alias_rules:
            if getattr(rule, 'role', None) != 'dimension':
                continue
            for alias in getattr(rule, 'aliases', ()):
                value = normalized(alias)
                if len(value) < 2:
                    continue
                score = (min(.99, .58 + len(value) * .08)
                         if getattr(rule, 'confidence', None) is None
                         else min(.99, rule.confidence + min(.2, len(value) * .02)))
                alias_matches.extend((match.start(), match.end(), score, rule.table, rule.column)
                                     for match in re.finditer(re.escape(value), scope))
        specific_matches = [item for item in alias_matches if not any(
            other[:2] != item[:2] and other[0] <= item[0] and item[1] <= other[1]
            and other[1] - other[0] > item[1] - item[0]
            for other in alias_matches)]
        for alias_start, alias_end, score, table, column in specific_matches:
            same_span = [item for item in specific_matches
                         if item[:2] == (alias_start, alias_end)]
            if score < max(item[2] for item in same_span):
                continue
            owners = []
            for candidate in tables:
                for table_alias in table_aliases(candidate.name):
                    prefix = (r'(?<![a-z0-9_])' + re.escape(normalized(table_alias))
                              + r'(?:表的|中的|内的|里的|的|表中|中|\.)?$')
                    match = re.search(prefix, scope[:alias_start])
                    if match:
                        owners.append((match.start(), candidate.name))
            if owners:
                nearest = max(position for position, _ in owners)
                scoped_owners = {owner for position, owner in owners if position == nearest}
                if len(scoped_owners) == 1 and table not in scoped_owners:
                    continue
            elif alias_end - alias_start <= 2:
                # Short labels like 地区/渠道/状态 are reused across tables
                # and may also name a dimension on the fact table. Let the
                # metric-aware dimension picker bind them after source choice.
                continue
            elif len({(item[3], item[4]) for item in same_span
                      if item[2] == max(candidate[2] for candidate in same_span)}) > 1:
                # Generic aliases such as “地区” occur on many fact tables.
                # Leave their final scope to the metric-aware dimension picker.
                continue
            fields.add((table, column))
    return fields


def group_grains(text, tables):
    """Bind explicit month/year nouns to their local, actual date field."""
    text = normalized(text)
    types = {(t.name, c.name): c.data_type for t in tables for c in t.columns}
    result = {}
    for group in re.findall(r'按([^,;。?!？；]{1,160}?)(?:分组|统计|计算|汇总)', text):
        if re.search(r'筛选|过滤|限定', group):
            continue
        for part in re.split(r'以及|和|与|、|及', group):
            fields = field_owners(part, tables)
            scope = part
            for name in sorted({*fields, *(owner for owners in fields.values() for owner in owners)}, key=len, reverse=True):
                for occurrence in reversed(mentions(scope, name)):
                    scope = scope[:occurrence.start()] + ' ' + scope[occurrence.end():]
            grains = set()
            if re.search(r'月份|月度|按月|每个月|每月|逐月|年月|的月|\bmonth(?:ly)?\b', scope):
                grains.add('month')
            if re.search(r'年份|年度|按年|每年|逐年|的年|\byear(?:ly)?\b', scope):
                grains.add('year')
            for column, owners in fields.items():
                if len(owners) != 1:
                    continue
                owner = next(iter(owners))
                if is_date_column(column, types[(owner, column)]) and grains:
                    result.setdefault((owner, column), set()).update(grains)
    return {pair: next(iter(grains)) if len(grains) == 1 else 'ambiguous' for pair, grains in result.items()}


def record_count_subject(text, tables):
    subjects = set()
    for clause in re.split(r'[,;。?!？；]', text):
        if re.search(r'记录数|记录计数|共有多少条|一共有多少条|有多少条|几条记录', clause):
            named = named_tables(clause, tables)
            if len(named) == 1:
                subjects.update(named)
    return next(iter(subjects)) if len(subjects) == 1 else None


def binding_present(plan, binding):
    def quote(name):
        return '"' + name.replace('"', '""') + '"'
    required = {f'{quote(binding["child"])}.{quote(a)} = {quote(binding["parent"])}.{quote(b)}'
                for a, b in binding['keys']}
    actual = {part.strip() for condition in plan.join_conditions for part in condition.split(' AND ')}
    return required.issubset(actual)
