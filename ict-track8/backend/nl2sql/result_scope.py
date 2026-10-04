"""Separate result semantics from presentation caps; never infer missing rows."""
from __future__ import annotations

import re

from .lexicon import cn_to_int
from .security import SqlSafetyError


_ROW_VERBS = r'(?:return|show|display|list|get|fetch|retrieve|output|emit)'
_EXPLICIT_ROWS = re.compile(
    r'(?:返回|展示|显示|只取|仅取|最多|只要|只看|仅看|取)\s*'
    r'(?:(?:最多|仅|只要|前)\s*)*(?P<cn>[0-9零一二三四五六七八九十百两]+)\s*(?:条|行)'
    rf'|\b{_ROW_VERBS}\s+(?:(?:only|at\s+most)\s+)*(?P<en>\d+)\s+(?:rows?|records?)\b'
    r'|\blimit\s+(?P<limit>\d+)\b', re.I)
_UNPARSED_ROWS = re.compile(
    r'[0-9零一二三四五六七八九十百千万亿两]+\s*(?:条|行|rows?\b|records?\b)|\blimit\b|限制(?:返回|条数|行数)', re.I)
_PREVIEW_ROWS = re.compile(
    r'(?:(?:仅|只)?(?:展示|显示|返回|查看)\s*(?:(?:最多|至多|前)\s*)*)?'
    r'(?P<after>[0-9零一二三四五六七八九十百两]+)\s*(?:条|行)\s*(?:的|结果)?\s*预览'
    r'|(?:仅|只)?预览\s*(?:(?:展示|显示|最多|至多|前)\s*)*'
    r'(?P<before>[0-9零一二三四五六七八九十百两]+)\s*(?:条|行)'
    rf'|\b{_ROW_VERBS}\s+(?:(?:only|at\s+most|up\s+to|first)\s+)*'
    r'(?P<en_after>\d+)\s+(?:rows?|records?)\s+(?:(?:as|of|in)\s+)?(?:a\s+)?preview\b'
    r'|\bpreview\s+(?:(?:only|at\s+most|up\s+to|first)\s+)*'
    r'(?P<en_before>\d+)\s+(?:rows?|records?)\b', re.I)
_TIE_QUALIFIERS_CN = r'(?:(?:所有|全部|全|同分|相同|的)\s*)*'
_WITH_TIES = re.compile(
    rf'(?:保留|含|包含|包括)\s*{_TIE_QUALIFIERS_CN}(?:并列|同分)'
    r'|\b(?:with|include|including|preserve|keep|retain)\s+'
    r'[^\n,.!?;，。；！？]*?\b(?:ties|tied\s+(?:rows?|records?|entities|groups?))\b', re.I)
_ALL_ROWS = re.compile(rf'\b{_ROW_VERBS}\s+(?:all|every|complete)\s+(?:rows?|records?)\b', re.I)
_UNKNOWN_RETURN_ROWS = re.compile(
    rf'\b{_ROW_VERBS}\s+[^\n,.!?;，。；！？]*\b(?:rows?|records?)\b', re.I)

_NEGATED_ROW_REQUEST = re.compile(
    r'(?:不要|别|无需|不需要|不要求|不再|不)(?:仅|只)?'
    r'(?:返回|展示|显示|取|看|限制|设置上限)(?:为|到|成)?\s*'
    r'(?:(?:最多|至多|仅|只|前)\s*)*(?P<cn>[0-9零一二三四五六七八九十百两]+)\s*(?:条|行)'
    r'(?P<cn_preview>\s*(?:的|结果)?\s*预览)?'
    rf'|\b(?:do\s+not|don\x27t|never)\s+(?:only\s+)?(?:{_ROW_VERBS}|limit)'
    r'\s+(?:(?:only|at\s+most|to|first)\s+)*(?P<en>\d+)\s+(?:rows?|records?)\b'
    r'(?P<en_preview>\s+(?:(?:as|of|in)\s+)?(?:a\s+)?preview\b)?', re.I)

_ROW_NEGATION = re.compile(
    r'不|别|无需|无须|未|'
    r"\b(?:not|never|no|without)\b|\b\w+n['’]t\b", re.I)


def _unresolved_negative_prefix(text, position):
    # Only a clause-local prefix is inspected. Do not silently extract the
    # affirmative substring from an unsupported negative/double negative.
    # A separate earlier business exclusion must not taint a positive cap.
    # ASCII dots also occur in physical column names and decimal values.
    # Treat them conservatively as part of the clause, never as permission
    # to discard an earlier negation and extract an affirmative row cap.
    prefix = re.split(r'[,;!?，。；！？\n]', text[:position])[-1]
    return bool(_ROW_NEGATION.search(prefix))

_COMPLETE_REQUEST = re.compile(
    r'(?:列出|列举|返回|输出|展示|显示)\s*(?:所有|全部|完整)'
    r'|(?:所有|全部|完整)[^。；！？\n]{0,60}(?:返回|输出|列出|列举)'
    r'|(?:不|无)(?:设置)?(?:数量|行数|条数)(?:上限|限制)|不限制(?:行数|条数|数量)'
    r'|(?:无|不设置)上限|不限\s*\d*\s*(?:行|条)'
    rf'|\b{_ROW_VERBS}\s+(?:all|every|complete)\b|\b(?:no\s+(?:row\s+)?limit|unbounded)\b', re.I)


def requests_complete_result(question: str) -> bool:
    """An explicit language request opts into existing bounded artifacts.

    This changes presentation, never removes time/step/byte budgets or implies
    that a capped preview is the complete answer. Negated requests stay off.
    """
    text = re.sub(r'(?:不要|无需|不需要|不要求)(?:全部|完整)?(?:返回|输出|列出|列举|展示|显示)'
                  r'(?:所有|全部|完整)?[^，。；！？\n]*', '', question)
    text = re.sub(r'\b(?:do\s+not|don\x27t)\s+(?:return|show|list|output)\s+all\b', '', text, flags=re.I)
    return bool(_COMPLETE_REQUEST.search(text))


def configure_complete_scope(plan, original_question: str) -> dict:
    """Only original user language may define an explicit final row LIMIT.

    Model-provided limit=100 is a preview convention, not permission to change
    query semantics. Top-N is kept in its existing independent rank filter.
    Unknown or conflicting row-limit language fails closed.
    """
    # Remove only the locally negated numeric request, not its whole clause:
    # a later positive cap or an actual business filter must remain visible.
    negated = list(_NEGATED_ROW_REQUEST.finditer(original_question))
    if any(_unresolved_negative_prefix(original_question, match.start()) for match in negated):
        raise SqlSafetyError('complete_result_negative_scope_ambiguous_or_unsupported')
    rejected_caps = {cn_to_int(match['cn'] or match['en']) for match in negated
                     if not (match['cn_preview'] or match['en_preview'])}
    scope_question = _NEGATED_ROW_REQUEST.sub('', original_question)
    scope_question = re.sub(r'(?:不限|不限制|不设|不要限制|不设置上限)(?:为|到)?\s*\d*\s*(?:行|条)',
                            '', scope_question)
    scope_question = re.sub(r'\bno\s+(?:row\s+)?limit\s+\d+\b', '', scope_question, flags=re.I)
    previews = list(_PREVIEW_ROWS.finditer(scope_question))
    if any(_unresolved_negative_prefix(scope_question, match.start()) for match in previews):
        raise SqlSafetyError('complete_result_negative_scope_ambiguous_or_unsupported')
    preview_values = [cn_to_int(next(value for value in match.groupdict().values() if value is not None))
                      for match in previews]
    if len(set(preview_values)) > 1 or any(not value or not 1 <= value <= 100_000 for value in preview_values):
        raise SqlSafetyError('complete_result_preview_scope_ambiguous_or_unsupported')
    result_question = _PREVIEW_ROWS.sub('', scope_question)
    # A reference to a rejected display cap is not a requested LIMIT.
    result_question = re.sub(r'(?:不限|不限制|不设|不要限制为|不设置上限为)\s*\d*\s*(?:行|条)',
                             '', result_question)
    # Per-group selection is SQL ranking semantics, not a final result cap.
    # Keep the full original question for model review and ties checks.
    result_question = re.sub(r'每(?:个|一|组|位|家|类)[^，。；！？\n]{0,80}?(?:最新|最早)(?:的)?\s*[一1]\s*条',
                             '', result_question)
    matches = list(_EXPLICIT_ROWS.finditer(result_question))
    if any(_unresolved_negative_prefix(result_question, match.start()) for match in matches):
        raise SqlSafetyError('complete_result_negative_scope_ambiguous_or_unsupported')
    values = [cn_to_int(next(value for value in match.groupdict().values() if value is not None))
              for match in matches]
    if rejected_caps.intersection(values):
        raise SqlSafetyError('complete_result_positive_negative_cap_conflict')
    remaining = _EXPLICIT_ROWS.sub('', result_question)
    remaining = _ALL_ROWS.sub('', remaining)
    if (len(set(values)) > 1 or any(not value or not 1 <= value <= 100_000 for value in values)
            or _UNPARSED_ROWS.search(remaining) or _UNKNOWN_RETURN_ROWS.search(remaining)):
        raise SqlSafetyError('complete_result_scope_ambiguous_or_unsupported')
    ties_question = re.sub(rf'(?:不|不要|无需)(?:保留|包含|包括|含)\s*{_TIE_QUALIFIERS_CN}(?:并列|同分)',
                           '', original_question)
    if values and _WITH_TIES.search(ties_question):
        raise SqlSafetyError('complete_result_row_cap_conflicts_with_ties')
    plan.complete_results = True
    plan.semantic_row_limit = values[0] if values else None
    plan.preview_row_limit = min(100, preview_values[0]) if preview_values else None
    return {'mode': 'bounded_complete_result_with_preview',
            'semantic_row_limit': plan.semantic_row_limit, 'top_n': plan.top_n,
            'requested_preview_limit': preview_values[0] if preview_values else None,
            'effective_preview_limit': plan.preview_row_limit,
            'default_preview_limit_removed_from_sql': plan.semantic_row_limit is None,
            'verification': 'original_question_row_limit_and_existing_rank_filter'}


def append_result_limit(plan, sql: str, parameters: list) -> str:
    """One final SQL limit contract shared by all three typed compilers."""
    limit = plan.semantic_row_limit if plan.complete_results else plan.limit
    if limit is None:
        return sql
    if type(limit) is not int or not 1 <= limit <= 100_000:
        raise SqlSafetyError('result_semantic_row_limit_invalid')
    parameters.append(limit)
    return sql + ' LIMIT ?'


def enforce_relational_result_scope(plan, sql, parameters):
    """Model review cannot authorize an invented final row cap or offset.

    Apply an omitted explicit user cap deterministically; validate an existing
    cap without rewriting nested per-group/rank selection semantics.
    """
    from sqlglot import exp,parse_one
    root=parse_one(sql,read='sqlite')
    if root.args.get('offset') is not None:
        raise SqlSafetyError('relational_unverified_result_offset')
    limit=root.args.get('limit')
    expected=plan.semantic_row_limit
    if limit is not None:
        expression=limit.expression
        if (expected is None or not isinstance(expression,exp.Literal) or not expression.is_int
                or int(expression.this)!=expected):
            raise SqlSafetyError('relational_result_limit_not_user_scope')
    elif expected is not None:
        return sql+' LIMIT ?',tuple(parameters)+(expected,)
    return sql,parameters
