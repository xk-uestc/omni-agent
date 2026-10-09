"""Parse bounded colloquial follow-ups into existing atomic scope operations.

This grammar grants no execution authority. Every generated command is still
checked against the saved successful query and independently reparsed plans.
Unknown words, duplicate slots and unclear field ownership are never removed.
"""
from dataclasses import dataclass
import re

from .pending_scope_edit import PendingScopeEditAgent


@dataclass(frozen=True)
class ConversationalEdit:
    command: str | None
    reason: str | None = None


_KEEP_PREFIX = re.compile(
    r'^(?:(?:沿用|保持)(?:原来的?|原有|刚才的?)?|同样的?|相同的?|同一|刚才的?|之前的?)'
    r'(?:查询)?(?:条件|范围)(?:下)?(?:再)?[，,：:\s]*')
_LOOK_PREFIX = re.compile(r'^(?:只看|只查|仅看|仅查|再给我|再给出|再查一下|也查一下|也看一下)')
_LOOK_SUFFIX = re.compile(r'(?:也查一下|也看一下|也查查|也看看)$')
_KEEP = re.compile(r'^(?:其他|其余)(?:(?:查询)?条件|筛选|范围)?(?:都)?(?:不变|保持不变|沿用)$')


def conversational_edit(question, base, engine):
    """Return a consumed grammar or None when the established parser owns it.

    Bare metric + time/entity remains a new independent request. Inheriting
    those slots together requires an explicit unchanged-conditions phrase or
    conversational punctuation; this prevents accidental filter inheritance.
    """
    if not isinstance(question, str) or not isinstance(base, str):
        return None
    text = question.strip().rstrip('。？！!?')
    keep = _KEEP_PREFIX.match(text)
    clauses = re.split(r'[，,；;]', text)
    looks = bool(_LOOK_PREFIX.match(text) or _LOOK_SUFFIX.search(text))
    mixed = (len(clauses) > 1 and bool(re.match(r'^(?:那么|那)', text))
             and not re.search(r'呢[，,；;]|仍|继续|不含|排除', text))
    explicit_keep = any(_KEEP.fullmatch(clause.strip()) for clause in clauses)
    # Existing canonical edits retain their original grammar and decisions.
    if not (keep or looks or mixed or explicit_keep):
        return None
    if len(text) > 160 or not 1 <= len(clauses) <= 4:
        return ConversationalEdit(None, 'conversational_edit_too_complex')
    if keep:
        text = text[keep.end():]
    text = re.sub(r'^(?:那么|那)', '', text)
    text = re.sub(r'呢$', '', text)
    parts = re.split(r'[，,；;]', text)
    if not parts or any(not part.strip() for part in parts):
        return ConversationalEdit(None, 'conversational_edit_empty_clause')
    before = engine.analyze_slots(base)
    preferred = {metric.table for metric in before['metrics']}
    output = []
    for part in parts:
        part = part.strip()
        if _KEEP.fullmatch(part):
            output.append('其他条件不变')
            continue
        if PendingScopeEditAgent.command(part) is not None:
            # Confirmations and malformed canonical mutations still undergo
            # the original all-or-none verifier, without rewriting their data.
            output.append(part)
            continue
        part = _LOOK_PREFIX.sub('', part)
        part = _LOOK_SUFFIX.sub('', part)
        part = re.sub(r'^(?:看|查)(?=.+)', '', part)
        part = re.sub(r'(?:呢|吧|即可)$', '', part).strip()
        supplied = engine.analyze_slots(part, preferred_tables=preferred)
        kinds = [kind for kind in ('metrics', 'values', 'time_spans', 'dimensions') if supplied[kind]]
        # Date labels are dimensions in schema; remove only an exact label at
        # the beginning, then prove that the entire remaining value is a slot.
        named = re.match(r'^(时间范围|时间|年份|月份|日期|统计指标|指标)(.+)$', part)
        if named:
            value = named[2]
            if named[1] in PendingScopeEditAgent._TIME_LABELS and re.fullmatch(r'\d{4}', value):
                value += '年'
            check = engine.analyze_slots(value, preferred_tables=preferred)
            kind = 'time_spans' if named[1] in PendingScopeEditAgent._TIME_LABELS else 'metrics'
            if (len(check[kind]) == 1 and not any(check[other] for other in
                    ('metrics', 'values', 'time_spans', 'dimensions') if other != kind)
                    and check['normalized'] == (check[kind][0] if kind == 'time_spans'
                                              else check[kind][0].matched_alias)):
                output.append(named[1] + '改成' + value)
                continue
        # A field label plus its value, e.g. 地区华南, is accepted only when
        # the lexer binds that label and value to the same physical column.
        if kinds == ['values', 'dimensions'] and len(supplied['values']) == len(supplied['dimensions']) == 1:
            value, field = supplied['values'][0], supplied['dimensions'][0]
            if ((value.table, value.column) == (field.table, field.column)
                    and supplied['normalized'] == field.matched_alias + value.span):
                part = value.span
                supplied = engine.analyze_slots(part, preferred_tables=preferred)
                kinds = ['values']
        if len(kinds) != 1:
            return ConversationalEdit(None, 'conversational_edit_unconsumed_or_ambiguous')
        kind = kinds[0]
        if kind == 'time_spans' and len(supplied[kind]) == 1 and supplied['normalized'] == supplied[kind][0]:
            output.append('时间改成' + supplied[kind][0])
        elif kind == 'metrics':
            aliases = list(dict.fromkeys(metric.matched_alias for metric in supplied[kind]))
            residual = supplied['normalized']
            for alias in sorted(aliases, key=len, reverse=True):
                residual = residual.replace(alias, '')
            from .sql_history_scope import _metric_alias_groups_verified
            if (not _metric_alias_groups_verified(supplied, engine) or re.sub(r'[和与及、\s]', '', residual)):
                return ConversationalEdit(None, 'conversational_edit_metric_not_exact')
            output.append('指标改成' + '和'.join(aliases))
        elif kind == 'values' and len(supplied[kind]) == 1:
            value = supplied[kind][0]
            if value.via != 'exact' or value.negated or supplied['normalized'] != value.span:
                return ConversationalEdit(None, 'conversational_edit_filter_not_exact')
            field = f'{value.table}.{value.column}'
            existing = [item for item in before['values'] if (item.table, item.column) == (value.table, value.column)]
            output.append(field + '改成' + value.span if existing else '增加' + field + '筛选为' + value.span)
        else:
            return ConversationalEdit(None, 'conversational_edit_unconsumed_or_ambiguous')
    if all(part == '其他条件不变' for part in output):
        return ConversationalEdit(None, 'conversational_edit_no_operation')
    return ConversationalEdit('，'.join(output))
