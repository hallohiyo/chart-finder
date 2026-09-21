"""근접도 점수화.

조건을 참/거짓으로 자르지 않고 0~1 점수로 만든다. 기준을 만족하면 1.0,
살짝 못 미치면 완만히 감소 → "아깝게 놓친" 종목도 순위에 남는다.
"""

from __future__ import annotations

import math

EPS = 1e-9


def _clean(x: float | None) -> float | None:
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _decay(distance: float, tol: float) -> float:
    """기준선에서 tol만큼 벗어나면 약 0.37점, 2*tol이면 약 0.02점."""
    tol = max(abs(tol), EPS)
    return math.exp(-((distance / tol) ** 2))


def tolerance(threshold: float, rel: float = 0.1, floor: float = 0.5) -> float:
    """기준값 크기에 비례한 기본 허용폭."""
    return max(abs(threshold) * rel, floor)


def soft_lt(x: float | None, threshold: float, tol: float | None = None) -> float:
    """x <= threshold 이면 1.0, 초과하면 감쇠."""
    x = _clean(x)
    if x is None:
        return 0.0
    if x <= threshold:
        return 1.0
    return _decay(x - threshold, tol if tol is not None else tolerance(threshold))


def soft_gt(x: float | None, threshold: float, tol: float | None = None) -> float:
    """x >= threshold 이면 1.0, 미달이면 감쇠."""
    x = _clean(x)
    if x is None:
        return 0.0
    if x >= threshold:
        return 1.0
    return _decay(threshold - x, tol if tol is not None else tolerance(threshold))


def soft_between(
    x: float | None, low: float, high: float, tol: float | None = None
) -> float:
    """구간 안이면 1.0, 밖이면 가까운 경계로부터 감쇠."""
    x = _clean(x)
    if x is None:
        return 0.0
    if low <= x <= high:
        return 1.0
    span = max(abs(high - low), EPS)
    tol = tol if tol is not None else max(span * 0.15, EPS)
    return _decay(low - x if x < low else x - high, tol)


def soft_near(x: float | None, target: float, tol: float | None = None) -> float:
    """target에 가까울수록 1.0."""
    x = _clean(x)
    if x is None:
        return 0.0
    return _decay(x - target, tol if tol is not None else tolerance(target))


def ordered(values: list[float | None], tol_pct: float = 2.0) -> float:
    """values가 내림차순이면 1.0 (예: 이동평균 정배열).

    역전된 구간은 그 크기에 비례해 감점하고, 전체 평균을 점수로 쓴다.
    tol_pct는 앞 값 대비 몇 % 역전까지 부분점수를 줄지.
    """
    cleaned = [_clean(v) for v in values]
    if any(v is None for v in cleaned) or len(cleaned) < 2:
        return 0.0
    scores = []
    for a, b in zip(cleaned, cleaned[1:]):
        if a >= b:
            scores.append(1.0)
        else:
            gap_pct = (b - a) / max(abs(b), EPS) * 100.0
            scores.append(_decay(gap_pct, tol_pct))
    return sum(scores) / len(scores)


def binary(flag: bool | None) -> float:
    return 1.0 if flag else 0.0
