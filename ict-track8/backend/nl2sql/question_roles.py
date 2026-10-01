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


def normalized(text):
    return to_simplified(re.sub(r'\s+', '', unicodedata.normalize('NFKC', text or '')).lower())


def mentions(text, name):
    return list(re.finditer(r'(?<![a-z0-9_])' + re.escape(normalized(name)) + r'(?![a-z0-9_])', text))


def named_tables(text, tables):
    return {table.name for table in tables if mentions(text, table.name)}


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
    groups = list(re.finditer(r'按([^,;。?!？；]{1,160}?)(?:分组|统计|计算|汇总)', text))
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
            group = next((g for g in groups if g.start(1) <= occurrence.start() < g.end(1)), None)
            scoped = possible & (named_tables(group.group(1), tables) if group else named)
            chosen.update(scoped if len(scoped) == 1 else possible)
        if chosen:
            result[column] = chosen
    return result


def filter_only(text, column):
    occurrences = mentions(text, column)
    return bool(occurrences) and all(re.match(
        r'(?:为|是|等于|不等于|为空|非空|不为空|=|!=|>=|<=|>|<|isnull|isnotnull)',
        text[m.end():]) for m in occurrences)


def group_fields(text, tables):
    groups = re.findall(r'按([^,;。?!？；]{1,160}?)(?:分组|统计|计算|汇总)', text)
    fields = set()
    for group in groups:
        if re.search(r'筛选|过滤|限定', group):
            continue
        for column, owners in field_owners(group, tables).items():
            fields.update((owner, column) for owner in owners)
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
