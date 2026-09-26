"""스크리너 엔진: 조건 세트를 전 종목에 적용하고 근접도 순으로 정렬."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from . import cache
from .datasource import get_source
from .conditions import Ctx, get as get_condition
from .conditions.builtin import FUNDAMENTAL as FUNDAMENTAL_CATEGORY

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
    """종목 하나에 대한 조건별 점수.

    같은 조건을 다른 파라미터로 두 번 쓰면 이름을 구분해서 돌려준다
    (`gap_over_ma`, `gap_over_ma#2`). 키만 쓰면 뒤쪽이 앞쪽을 덮어쓴다.
    """
    specs = list(specs)
    ctx = Ctx(df, fundamentals, profile, benchmark)
    cached: dict[tuple, float] = {}
    out = {}
    for label, spec in zip(score_labels(specs), specs):
        key = _cache_key(spec)
        if key not in cached:
            cached[key] = get_condition(spec.key).score(ctx, spec.params)
        out[label] = cached[key]
    return out


#: 저장 파일에 쓸 한글 머리글. 엑셀에서 바로 읽히도록.
EXPORT_LABELS = {
    "symbol": "종목코드",
    "name": "종목명",
    "score": "적합도",
    "strategy": "맞는 전략",
    "matched": "충족 조건수",
    "of": "전체 조건수",
    "gap": "2등 전략과 차이",
    "matched_ratio": "충족 비율",
    "close": "현재가",
    "chg_pct": "등락률(%)",
    "date": "기준일",
    "overall": "전략 평균(참고용)",
    "turnover_20d": "거래대금 20일평균(억)",
    "marcap": "시가총액(억)",
    "foreign_net_5d": "외국인 순매수 5일(주)",
    "foreign_net_20d": "외국인 순매수 20일(주)",
    "inst_net_5d": "기관 순매수 5일(주)",
    "inst_net_20d": "기관 순매수 20일(주)",
    "both_buy_days_20d": "쌍끌이 일수 20일",
}


def export_frame(result: pd.DataFrame) -> pd.DataFrame:
    """저장용으로 머리글을 한글로 바꾼다.

    내부 컬럼명은 영문으로 두고 (코드가 참조한다) 파일만 읽기 쉽게 만든다.
    전략별 점수(p_...)와 조건별 점수(s_...)는 이름을 그대로 쓴다.
    """
    out = result.copy()
    # 596612.8414485113 처럼 찍히면 엑셀에서 읽기 어렵다
    if "close" in out.columns:
        out["close"] = out["close"].round(2)
    renamed = {column: EXPORT_LABELS.get(column, column) for column in out.columns}
    return out.rename(columns=renamed)


#: 점수가 아닌 실제 숫자 컬럼의 표시 순서
FACT_COLUMNS = [
    "turnover_20d", "marcap",
    "foreign_net_5d", "foreign_net_20d",
    "inst_net_5d", "inst_net_20d",
    "both_buy_days_20d",
]


def _cache_key(spec: "ConditionSpec") -> tuple:
    """조건 점수 캐시 키. 같은 조건·같은 파라미터면 한 번만 계산한다."""
    return (spec.key, tuple(sorted(spec.params.items())))


def score_labels(specs: Iterable[ConditionSpec]) -> list[str]:
    """조건별 점수 컬럼 이름. 같은 조건이 두 번 나오면 구분해서 붙인다.

    `gap_over_ma` 를 20일선·60일선으로 두 번 쓰는 것은 정당한 사용인데,
    이름을 키만으로 지으면 두 점수가 한 칸에 겹쳐 하나가 사라진다.
    """
    specs = list(specs)
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec.key] = counts.get(spec.key, 0) + 1
    out, used = [], {}
    for spec in specs:
        if counts[spec.key] == 1:
            out.append(spec.key)
            continue
        used[spec.key] = used.get(spec.key, 0) + 1
        out.append(f"{spec.key}#{used[spec.key]}")
    return out


def facts(df: pd.DataFrame, profile: dict[str, float] | None = None) -> dict[str, Any]:
    """점수가 아닌 '실제 숫자' 컬럼.

    점수만 저장하면 왜 뽑혔는지 확인할 수 없다. 외국인·기관이 며칠 동안 몇 주를
    담았는지, 거래대금이 얼마인지 같은 원 숫자를 결과에 같이 싣는다.
    수급을 받지 않은 캐시에서는 그 컬럼이 아예 생기지 않는다.
    """
    out: dict[str, Any] = {}

    turnover = (df["close"] * df["volume"]).tail(20).mean()
    if pd.notna(turnover):
        out["turnover_20d"] = round(float(turnover) / 1e8, 1)  # 억원

    if profile:
        marcap = profile.get("marcap")
        if marcap is not None and pd.notna(marcap):
            out["marcap"] = round(float(marcap) / 1e8, 0)  # 억원

    for column, label in (("foreign_net", "foreign"), ("inst_net", "inst")):
        if column not in df.columns:
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        if series.empty:
            continue
        for days in (5, 20):
            out[f"{label}_net_{days}d"] = int(series.tail(days).sum())

    # 외국인·기관이 같은 날 함께 담은 날수 (최근 20일)
    if "foreign_net" in df.columns and "inst_net" in df.columns:
        pair = df[["foreign_net", "inst_net"]].apply(
            pd.to_numeric, errors="coerce"
        ).dropna().tail(20)
        if not pair.empty:
            out["both_buy_days_20d"] = int(
                ((pair["foreign_net"] > 0) & (pair["inst_net"] > 0)).sum()
            )
    return out


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
    """종목 정보 스냅샷. 시장당 파일 하나라 조건 유무와 무관하게 읽는다.

    종목정보 조건이 없어도 시가총액 같은 숫자를 결과에 실어야 하고, 읽기
    비용은 파일 한 번이라 무시할 수 있다.
    """
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
    specs = list(specs)
    return [
        spec.key
        for label, spec in zip(score_labels(specs), specs)
        if f"s_{label}" in result.columns and float(result[f"s_{label}"].max()) <= 0
    ]


def combine(scores: Mapping[str, float], specs: Iterable[ConditionSpec]) -> float:
    """가중 평균 (0~1).

    scores 는 score_labels() 가 만든 이름으로 조회한다. 키만으로 조회하면
    같은 조건이 두 번 나올 때 두 조건이 같은 점수를 받아 합이 부풀려진다.
    """
    specs = list(specs)
    total = sum(spec.weight for spec in specs)
    if total <= 0:
        return 0.0
    labels = score_labels(specs)
    return sum(
        scores.get(label, scores.get(spec.key, 0.0)) * spec.weight
        for label, spec in zip(labels, specs)
    ) / total


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
            # 같은 조건을 다른 파라미터로 두 번 쓰면 이름을 구분해야 한다.
            # 키만 쓰면 뒤쪽이 앞쪽을 덮어써 두 조건이 같은 점수를 받는다.
            for name, spec in zip(score_labels(specs), specs):
                key = _cache_key(spec)
                if key not in cache_by_key:
                    cache_by_key[key] = get_condition(spec.key).score(ctx, spec.params)
                scores[name] = cache_by_key[key]
            per_strategy[label] = combine(scores, specs)

        # 점수가 같으면 조건이 많은(까다로운) 전략 이름을 붙인다. 더 많은 정보다.
        best_label = max(
            per_strategy, key=lambda label: (per_strategy[label], len(strategies[label]))
        )
        best = per_strategy[best_label]
        if best < min_score:
            continue

        # 이긴 전략에서 실제로 충족한 조건 수 / 전체. 조건이 적은 전략은 만점이
        # 쉬워 동점이 쏟아지므로, 동점일 때는 '몇 개를 진짜로 맞췄는지' 로 가른다.
        best_specs = strategies[best_label]
        met = sum(
            1 for spec in best_specs
            if cache_by_key.get(_cache_key(spec), 0.0) >= STRICT_PASS
        )
        matched_ratio = met / len(best_specs) if best_specs else 0.0

        # 2등 전략과의 차이. 크면 '이 전략 하나에 확실히 맞는' 종목이고,
        # 작으면 방향이 반대인 전략에도 비슷한 점수가 난 애매한 종목이다.
        others = sorted(per_strategy.values(), reverse=True)
        gap = round(best - others[1], 4) if len(others) > 1 else round(best, 4)

        # 전략 평균. 방향이 반대인 전략끼리는 평균이 의미가 없다 (공유하는
        # 필터 때문에 올라간다). 그래서 순위에는 쓰지 않고 참고로만 남긴다.
        overall = sum(per_strategy.values()) / len(per_strategy)

        close = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2]) if len(df) > 1 else close
        row = {
            "symbol": ticker.symbol,
            "name": names.get(ticker.symbol, ticker.symbol),
            "score": round(best, 4),
            "strategy": best_label,
            "matched": met,
            "of": len(best_specs),
            "gap": gap,
            "close": close,
            "chg_pct": round((close / prev - 1.0) * 100.0, 2) if prev else 0.0,
            "date": df.index[-1].date(),
            "matched_ratio": round(matched_ratio, 4),
            "overall": round(overall, 4),
        }
        row.update({f"p_{label}": round(value, 3) for label, value in per_strategy.items()})
        row.update(facts(df, _profile_of(profiles, ticker.symbol)))
        rows.append(row)

    # 엑셀에서 바로 보이는 순서로 짠다. 전략 점수(p_...)는 전략마다 한 칸씩
    # 늘어나므로, 실제 숫자를 그 뒤에 두면 열 30번대로 밀려 안 보인다.
    head = ["symbol", "name", "score", "strategy", "matched", "of",
            "close", "chg_pct"]
    tail = ["date", "gap", "matched_ratio", "overall"]
    if not rows:
        return pd.DataFrame(
            columns=head + FACT_COLUMNS + tail + [f"p_{label}" for label in strategies]
        )

    # 실제 숫자 컬럼은 데이터가 있을 때만 생긴다 (수급을 안 받으면 없다)
    frame = pd.DataFrame(rows)
    columns = (
        head
        + [c for c in FACT_COLUMNS if c in frame.columns]
        + tail
        + [f"p_{label}" for label in strategies]
    )
    result = frame[columns]
    # overall(전략 평균) 로 동점을 가르면, 반대 방향 전략에도 어중간하게
    # 맞는 종목이 한 전략에 확실히 맞는 종목을 이긴다. 공유하는 필터 때문에
    # 올라간 점수이므로 근거가 못 된다. 실제로 충족한 조건 비율로 가른다.
    result = result.sort_values(
        ["score", "matched_ratio", "gap", "chg_pct"],
        ascending=[False, False, False, False],
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
        row.update(facts(df, _profile_of(profiles, ticker.symbol)))
        rows.append(row)

    head = ["symbol", "name", "score", "matched", "close", "chg_pct"]
    scores_cols = [f"s_{label}" for label in score_labels(specs)]
    if not rows:
        return pd.DataFrame(columns=head + FACT_COLUMNS + ["date"] + scores_cols)

    frame = pd.DataFrame(rows)
    columns = (
        head
        + [c for c in FACT_COLUMNS if c in frame.columns]
        + ["date"]
        + scores_cols
    )
    result = frame[columns]
    result = result.sort_values(
        ["score", "matched", "chg_pct"], ascending=[False, False, False]
    ).reset_index(drop=True)
    return result.head(top) if top else result
