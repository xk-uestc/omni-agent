"""运行时环境变量的有界解析工具。

服务启动阶段不能因为一个拼写错误的数字环境变量直接崩溃；同时也不能把
无界的超时或重试次数带入生产请求。解析失败和越界都会回退到明确的安全
默认值，并留下不含敏感值的配置告警供 ``/health`` 暴露。
"""

from __future__ import annotations

import math
import os
from typing import MutableSequence


def env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
    warnings: MutableSequence[str] | None = None,
) -> float:
    raw = os.getenv(name, "")
    try:
        value = float(raw) if raw.strip() else float(default)
    except (TypeError, ValueError):
        _warn(warnings, f"{name}: invalid value; fallback={default}")
        return float(default)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        _warn(warnings, f"{name}: outside [{minimum}, {maximum}]; fallback={default}")
        return float(default)
    return value


def env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
    warnings: MutableSequence[str] | None = None,
) -> int:
    raw = os.getenv(name, "")
    try:
        value = int(raw) if raw.strip() else int(default)
    except (TypeError, ValueError):
        _warn(warnings, f"{name}: invalid value; fallback={default}")
        return int(default)
    if not minimum <= value <= maximum:
        _warn(warnings, f"{name}: outside [{minimum}, {maximum}]; fallback={default}")
        return int(default)
    return value


def _warn(warnings: MutableSequence[str] | None, message: str) -> None:
    if warnings is not None:
        warnings.append(message)
