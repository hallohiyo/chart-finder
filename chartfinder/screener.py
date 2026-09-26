"""스크리너 엔진: 조건 세트를 전 종목에 적용하고 근접도 순으로 정렬."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from . import cache
from .datasource import get_source
from .conditions import Ctx, get as get_condition
from .conditions.builtin import FUNDAMENTAL as FUNDAMENTAL_CATEGORY
from .conditions.builtin import PROFILE as PROFILE_CATEGORY

#: 비교 지수를 필요로 하는 조건. 카테고리로는 구분되지 않아 이름으로 둔다.
_BENCHMARK_CONDITIONS = frozenset({"relative_strength"})
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


def score_one(
    df: pd.DataFrame,
    specs: Iterable[ConditionSpec],
    fundamentals: pd.DataFrame | None = None,
    profile: dict[str, float] | None = None,
    benchmark: pd.DataFrame | None = None,
) -> dict[str, float]:
    """종목 하나에 대한 조건별 점수."""
    ctx = Ctx(df, fundamentals, profile, benchmark)
    return {spec.key: get_condition(spec.key).score(ctx, spec.params) for spec in specs}


def _benchmarks_for(market: str, specs: Iterable[ConditionSpec]) -> dict[str, pd.DataFrame]:
    """상대강도 조건이 있을 때만 지수를 읽는다. {지수이름: 일봉}."""
    if not any(spec.key in _BENCHMARK_CONDITIONS for spec in specs):
        return {}
    source = get_source(market)
    frames: dict[str, pd.DataFrame] = {}
    for name in set((getattr(source, "benchmarks", None) or {}).values()):
        frame = cache.load_index(market, name)
        if frame is not None:
            frames[name] = frame
    return frames


def _benchmark_of(
    market: str, frames: dict[str, pd.DataFrame], exchange: str
) -> pd.DataFrame | None:
    """그 종목이 속한 거래소의 지수를 골라 준다 (코스피 종목 → 코스피 지수)."""
    if not frames:
        return None
    name = get_source(market).benchmark_for(exchange)
    return frames.get(name) if name else None


def _profiles_for(
    market: str, specs: Iterable[ConditionSpec]
) -> pd.DataFrame | None:
    """종목정보 조건이 있을 때만 스냅샷을 읽는다."""
    if not any(get_condition(spec.key).category == PROFILE_CATEGORY for spec in specs):
        return None
    return cache.load_profiles(market)


def _profile_of(profiles: pd.DataFrame | None, symbol: str) -> dict[str, float] | None:
    if profiles is None or symbol not in profiles.index:
        return None
    return profiles.loc[symbol].to_dict()


def unscored_conditions(result: pd.DataFrame, specs: Iterable[ConditionSpec]) -> list[str]:
    """모든 종목에서 0점이 나온 조건.

    대개 그 조건이 보는 데이터를 받지 않았다는 뜻이다 (수급·재무 등).
    조건이 잘못된 것과 데이터가 없는 것을 구분하려면 이걸 봐야 한다.
    """
    if result is None or result.empty:
        return []
    return [
        spec.key for spec in specs
        if f"s_{spec.key}" in result.columns and float(result[f"s_{spec.key}"].max()) <= 0
    ]


def combine(scores: Mapping[str, float], specs: Iterable[ConditionSpec]) -> float:
    """가중 평균 (0~1)."""
    total = sum(spec.weight for spec in specs)
    if total <= 0:
        return 0.0
    return sum(scores.get(spec.key, 0.0) * spec.weight for spec in specs) / total


def screen_multi(
    market: str,
    strategies: Mapping[str, list[ConditionSpec]],
    tickers: list[Ticker] | None = None,
    universe: str = "all",
    top: int | None = None,
    min_score: float = 0.0,
    progress: ProgressFn | None = None,
) -> pd.DataFrame:
    """전략마다 따로 채점하고, 가장 잘 맞는 전략의 점수로 순위를 매긴다.

    여러 전략을 하나로 평균내면 서로 반대인 조건(신고가 근접 ↔ 신저가 근접)이
    섞여 어느 쪽도 만족하지 못하는 종목이 상위에 온다. 전략별로 따로 채점하면
    "이 종목은 눌림목 92%" 처럼 왜 뽑혔는지도 분명해진다.
    """
    if not strategies:
        raise ValueError("전략이 하나 이상 필요합니다.")

    if tickers is None:
        tickers = cache.get_tickers(market, universe)
    names = {t.symbol: t.name for t in tickers}

    all_specs = [spec for specs in strategies.values() for spec in specs]
    needs_fundamentals = any(
        get_condition(spec.key).category == FUNDAMENTAL_CATEGORY for spec in all_specs
    )
    profiles = _profiles_for(market, all_specs)
    benchmarks = _benchmarks_for(market, all_specs)

    rows: list[dict[str, Any]] = []
    for i, ticker in enumerate(tickers, start=1):
        if progress:
            progress(i, len(tickers))
        df = cache.load(market, ticker.symbol)
        if df is None or df.empty:
            continue

        fundamentals = (
            cache.load_fundamentals(market, ticker.symbol) if needs_fundamentals else None
        )
        ctx = Ctx(
            df,
            fundamentals,
            _profile_of(profiles, ticker.symbol),
            _benchmark_of(market, benchmarks, ticker.exchange),
        )
        # 조건 점수는 전략끼리 공유한다 (같은 조건을 두 번 계산하지 않도록)
        cache_by_key: dict[tuple, float] = {}

        per_strategy: dict[str, float] = {}
        for label, specs in strategies.items():
            scores = {}
            for spec in specs:
                key = (spec.key, tuple(sorted(spec.params.items())))
                if key not in cache_by_key:
                    cache_by_key[key] = get_condition(spec.key).score(ctx, spec.params)
                scores[spec.key] = cache_by_key[key]
            per_strategy[label] = combine(scores, specs)

        # 점수가 같으면 조건이 많은(까다로운) 전략 이름을 붙인다. 더 많은 정보다.
        best_label = max(
            per_strategy, key=lambda label: (per_strategy[label], len(strategies[label]))
        )
        best = per_strategy[best_label]
        if best < min_score:
            continue

        # 조건이 적은 전략은 만점이 쉬워 동점이 쏟아진다. 동점일 때는 다른
        # 전략에도 두루 맞는 종목을 앞세운다.
        overall = sum(per_strategy.values()) / len(per_strategy)

        close = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2]) if len(df) > 1 else close
        row = {
            "symbol": ticker.symbol,
            "name": names.get(ticker.symbol, ticker.symbol),
            "score": round(best, 4),
            "strategy": best_label,
            "close": close,
            "chg_pct": round((close / prev - 1.0) * 100.0, 2) if prev else 0.0,
            "date": df.index[-1].date(),
            "overall": round(overall, 4),
        }
        row.update({f"p_{label}": round(value, 3) for label, value in per_strategy.items()})
        rows.append(row)

    columns = [
        "symbol", "name", "score", "strategy", "close", "chg_pct", "date", "overall"
    ] + [f"p_{label}" for label in strategies]
    if not rows:
        return pd.DataFrame(columns=columns)

    result = pd.DataFrame(rows)[columns]
    result = result.sort_values(
        ["score", "overall", "chg_pct"], ascending=[False, False, False]
    ).reset_index(drop=True)
    return result.head(top) if top else result


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

    # 재무 조건이 하나도 없으면 재무 캐시를 읽지 않는다 (불필요한 디스크 접근)
    needs_fundamentals = any(
        get_condition(spec.key).category == FUNDAMENTAL_CATEGORY for spec in specs
    )
    profiles = _profiles_for(market, specs)
    benchmarks = _benchmarks_for(market, specs)

    rows: list[dict[str, Any]] = []
    total = len(tickers)
    for i, ticker in enumerate(tickers, start=1):
        if progress:
            progress(i, total)
        df = cache.load(market, ticker.symbol)
        if df is None or df.empty:
            continue

        fundamentals = cache.load_fundamentals(market, ticker.symbol) if needs_fundamentals else None
        scores = score_one(
            df, specs, fundamentals,
            _profile_of(profiles, ticker.symbol),
            _benchmark_of(market, benchmarks, ticker.exchange),
        )
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
