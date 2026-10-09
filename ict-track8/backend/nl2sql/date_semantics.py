"""Shared conservative date-field semantics, independent of business schemas.

Names identify candidate date roles; actual storage is still checked before a
time filter executes. Arbitrary text values are not inferred to be date roles.
"""
from __future__ import annotations

import re
import unicodedata

from .t2s import to_simplified

GENERIC_TIME_ALIASES = frozenset({'日期', '时间', '月份', '月度', '按月', '年份', '按年', '年度', '每月', '每年'})


def is_date_column(column: str, data_type: str = '') -> bool:
    name = to_simplified(re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(column or '')))).lower()
    declared = str(data_type or '').upper().split('(', 1)[0].strip()
    return (declared in {'DATE', 'DATETIME', 'TIMESTAMP', 'TIME'}
            or name.endswith(('date', 'time', 'timestamp', '_at', '_on', '_month', 'month', '日期', '时间', '月份')))
