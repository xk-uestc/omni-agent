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


def configure_complete_scope(plan, original_question: str) -> dict:
    """Only original user language may define an explicit final row LIMIT.

    Model-provided limit=100 is a preview convention, not permission to change
    query semantics. Top-N is kept in its existing independent rank filter.
    Unknown or conflicting row-limit language fails closed.
    """
    previews = list(_PREVIEW_ROWS.finditer(original_question))
    preview_values = [cn_to_int(next(value for value in match.groupdict().values() if value is not None))
                      for match in previews]
    if len(set(preview_values)) > 1 or any(not value or not 1 <= value <= 100_000 for value in preview_values):
        raise SqlSafetyError('complete_result_preview_scope_ambiguous_or_unsupported')
    result_question = _PREVIEW_ROWS.sub('', original_question)
    matches = list(_EXPLICIT_ROWS.finditer(result_question))
    values = [cn_to_int(next(value for value in match.groupdict().values() if value is not None))
              for match in matches]
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
