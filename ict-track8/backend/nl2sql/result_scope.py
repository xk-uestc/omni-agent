"""Separate result semantics from presentation caps; never infer missing rows."""
from __future__ import annotations

import re

from .lexicon import cn_to_int
from .security import SqlSafetyError


_EXPLICIT_ROWS = re.compile(
    r'(?:返回|展示|显示|只取|仅取|最多|只要|只看|仅看|取)\s*'
    r'(?:(?:最多|仅|只要|前)\s*)*(?P<cn>[0-9零一二三四五六七八九十百两]+)\s*(?:条|行)'
    r'|\b(?:return|show)\s+(?:(?:only|at\s+most)\s+)*(?P<en>\d+)\s+(?:rows?|records?)\b'
    r'|\blimit\s+(?P<limit>\d+)\b', re.I)
_UNPARSED_ROWS = re.compile(
    r'[0-9零一二三四五六七八九十百两]+\s*(?:条|行|rows?\b|records?\b)|\blimit\b|限制(?:返回|条数|行数)', re.I)


def configure_complete_scope(plan, original_question: str) -> dict:
    """Only original user language may define an explicit final row LIMIT.

    Model-provided limit=100 is a preview convention, not permission to change
    query semantics. Top-N is kept in its existing independent rank filter.
    Unknown or conflicting row-limit language fails closed.
    """
    matches = list(_EXPLICIT_ROWS.finditer(original_question))
    values = [cn_to_int(next(value for value in match.groupdict().values() if value is not None))
              for match in matches]
    remaining = _EXPLICIT_ROWS.sub('', original_question)
    if (len(set(values)) > 1 or any(not value or not 1 <= value <= 100_000 for value in values)
            or _UNPARSED_ROWS.search(remaining)):
        raise SqlSafetyError('complete_result_scope_ambiguous_or_unsupported')
    plan.complete_results = True
    plan.semantic_row_limit = values[0] if values else None
    return {'mode': 'bounded_complete_result_with_preview',
            'semantic_row_limit': plan.semantic_row_limit, 'top_n': plan.top_n,
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
