"""조건 레지스트리 (import 시 내장 조건이 등록된다)."""

from __future__ import annotations

from .base import Condition, Ctx, Param, all_conditions, by_category, condition, get
from . import builtin as _builtin  # noqa: F401  (등록 부수효과)

__all__ = [
    "Condition", "Ctx", "Param", "condition", "get", "all_conditions", "by_category",
]
