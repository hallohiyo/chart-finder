"""스크리너 엔진: 조건 세트를 전 종목에 적용하고 근접도 순으로 정렬."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from . import cache
from .conditions import Ctx, get as get_condition
from .datasource import Ticker

ProgressFn = Callable[[int, int], None]

#: strict 모드에서 '충족'으로 인정하는 점수
STRICT_PASS = 0.999


@dataclass
class ConditionSpec:
    """조건 하나와 그 파라미터·가중치."""

    key: str
    params: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError(f"가중치는 0보다 커야 합니다: {self.key}={self.weight}")
        get_condition(self.key)  # 존재 확인 (없으면 KeyError)

    @property
    def label(self) -> str:
        return get_condition(self.key).label

    @classmethod
    def parse(cls, text: str) -> "ConditionSpec":
        """'rsi_oversold:period=14,threshold=35,weight=2' 형태를 파싱."""
        head, _, tail = text.partition(":")
        key = head.strip()
        params: dict[str, Any] = {}
        weight = 1.0
        for chunk in (c for c in tail.split(",") if c.strip()):
            name, sep, value = chunk.partition("=")
            if not sep:
                raise ValueError(f"파라미터 형식 오류: '{chunk}' (name=value 여야 함)")
            name, value = name.strip(), value.strip()
            if name == "weight":
                weight = float(value)
            else:
                params[name] = value
        return cls(key=key, params=params, weight=weight)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "params": dict(self.params), "weight": self.weight}


def score_one(df: pd.DataFrame, specs: Iterable[ConditionSpec]) -> dict[str, float]:
    """종목 하나에 대한 조건별 점수."""
    ctx = Ctx(df)
    return {spec.key: get_condition(spec.key).score(ctx, spec.params) for spec in specs}


def combine(scores: Mapping[str, float], specs: Iterable[ConditionSpec]) -> float:
    """가중 평균 (0~1)."""
    total = sum(spec.weight for spec in specs)
    if total <= 0:
        return 0.0
    return sum(scores.get(spec.key, 0.0) * spec.weight for spec in specs) / total


def screen(
    market: str,
    specs: list[ConditionSpec],
    tickers: list[Ticker] | None = None,
    universe: str = "all",
    strict: bool = False,
    min_score: float = 0.0,
    top: int | None = None,
    progress: ProgressFn | None = None,
) -> pd.DataFrame:
    """캐시된 일봉에 조건을 적용해 점수표를 만든다.

    strict=True면 모든 조건을 완전히 충족한 종목만 남긴다 (일반 스크리너와 동일).
    기본값(False)에서는 근접도 점수로 정렬해 '아깝게 놓친' 종목도 보여준다.
    """
    if not specs:
        raise ValueError("조건이 하나 이상 필요합니다.")

    if tickers is None:
        tickers = cache.get_tickers(market, universe)
    names = {t.symbol: t.name for t in tickers}

    rows: list[dict[str, Any]] = []
    total = len(tickers)
    for i, ticker in enumerate(tickers, start=1):
        if progress:
            progress(i, total)
        df = cache.load(market, ticker.symbol)
        if df is None or df.empty:
            continue

        scores = score_one(df, specs)
        total_score = combine(scores, specs)
        if strict and any(s < STRICT_PASS for s in scores.values()):
            continue
        if total_score < min_score:
            continue

        close = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2]) if len(df) > 1 else close
        row = {
            "symbol": ticker.symbol,
            "name": names.get(ticker.symbol, ticker.symbol),
            "score": round(total_score, 4),
            "matched": sum(1 for s in scores.values() if s >= STRICT_PASS),
            "close": close,
            "chg_pct": round((close / prev - 1.0) * 100.0, 2) if prev else 0.0,
            "date": df.index[-1].date(),
        }
        row.update({f"s_{k}": round(v, 3) for k, v in scores.items()})
        rows.append(row)

    columns = ["symbol", "name", "score", "matched", "close", "chg_pct", "date"] + [
        f"s_{spec.key}" for spec in specs
    ]
    if not rows:
        return pd.DataFrame(columns=columns)

    result = pd.DataFrame(rows)[columns]
    result = result.sort_values(
        ["score", "matched", "chg_pct"], ascending=[False, False, False]
    ).reset_index(drop=True)
    return result.head(top) if top else result
