"""과거 시점 채점 → 이후 수익률 확인.

기준일(as-of)까지의 데이터만 보고 점수를 매긴 뒤, 그 뒤 N일 수익률을 본다.
핵심은 **그 시점에 몰랐던 정보를 쓰지 않는 것**이다:

- 시세: 기준일까지만 잘라서 채점한다
- 수급: 일봉에 붙어 있으므로 같이 잘린다
- 재무: 결산기가 끝났다고 바로 공시되지 않는다. 사업보고서 제출 시한을
  감안해 기준일에 이미 공시됐을 연도만 남긴다

점수가 높을수록 이후 수익률이 좋은지를 보는 것이 목적이고, 매매 비용·슬리피지·
상장폐지 종목 누락(생존 편향)은 반영하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from . import cache
from .screener import ConditionSpec, combine, score_one

#: 사업보고서 제출 시한 (결산 후 90일). 이 전에는 그 해 재무를 몰랐다고 본다.
FILING_LAG_DAYS = 90
#: 채점에 필요한 최소 봉 수 (장기 이동평균·52주 고저 때문에)
MIN_BARS = 250

ProgressFn = Callable[[int, int], None]


@dataclass
class Backtest:
    """(기준일, 종목)별 점수와 이후 수익률."""

    rows: pd.DataFrame
    horizon: int
    top: int

    @property
    def dates(self) -> list[date]:
        return sorted(self.rows["asof"].unique()) if not self.rows.empty else []

    def by_date(self) -> pd.DataFrame:
        """기준일마다 상위 N종목과 전체의 이후 수익률을 비교한다."""
        if self.rows.empty:
            return pd.DataFrame()

        records = []
        for asof, group in self.rows.groupby("asof"):
            picks = group.nlargest(self.top, "score")
            records.append({
                "asof": asof,
                "종목수": len(group),
                "상위평균": picks["forward_return"].mean(),
                "상위중앙": picks["forward_return"].median(),
                "전체평균": group["forward_return"].mean(),
                "초과": picks["forward_return"].mean() - group["forward_return"].mean(),
                "상위승률": float((picks["forward_return"] > 0).mean() * 100),
                "평균점수": picks["score"].mean(),
            })
        return pd.DataFrame(records).sort_values("asof").reset_index(drop=True)

    def by_score_bucket(self, bins: int = 5) -> pd.DataFrame:
        """점수 구간별 이후 수익률. 점수가 의미 있으면 단조 증가해야 한다."""
        if self.rows.empty:
            return pd.DataFrame()

        rows = self.rows.copy()
        try:
            rows["구간"] = pd.qcut(rows["score"], bins, duplicates="drop")
        except ValueError:
            return pd.DataFrame()

        grouped = rows.groupby("구간", observed=True)["forward_return"]
        table = pd.DataFrame({
            "종목수": grouped.size(),
            "평균수익": grouped.mean(),
            "중앙수익": grouped.median(),
            "승률": grouped.apply(lambda s: float((s > 0).mean() * 100)),
        }).reset_index()
        table["구간"] = table["구간"].astype(str)
        return table

    def noise_level(self) -> float:
        """이 표본 크기에서 상관계수가 우연히 나올 수 있는 범위.

        이보다 작은 상관은 신호가 아니라 잡음으로 본다 (대략 2 표준오차).
        """
        n = len(self.rows)
        return 2.0 / np.sqrt(n) if n > 4 else 1.0

    def summary(self) -> dict[str, float]:
        """전체 요약. 점수-수익률 상관이 이 도구의 존재 이유다."""
        if self.rows.empty:
            return {}

        per_date = self.by_date()
        correlation = self.rows[["score", "forward_return"]].corr(method="spearman")
        return {
            "기준일수": len(per_date),
            "표본수": len(self.rows),
            "상위평균수익": per_date["상위평균"].mean(),
            "전체평균수익": per_date["전체평균"].mean(),
            "평균초과수익": per_date["초과"].mean(),
            "초과승률": float((per_date["초과"] > 0).mean() * 100),
            "점수-수익 상관": float(correlation.loc["score", "forward_return"]),
        }


# --------------------------------------------------------------------------- 시점 자르기


def fundamentals_as_of(fundamentals: pd.DataFrame | None, asof: date) -> pd.DataFrame | None:
    """기준일에 이미 공시됐을 재무만 남긴다.

    2024년 결산은 2025년 3월 말에야 공시되므로, 2025년 2월 시점의 백테스트가
    2024년 재무를 보면 미래를 훔쳐보는 셈이 된다.
    """
    if fundamentals is None or fundamentals.empty:
        return fundamentals
    cutoff = asof - timedelta(days=FILING_LAG_DAYS)
    published = [year for year in fundamentals.index if date(int(year), 12, 31) <= cutoff]
    return fundamentals.loc[published] if published else None


def forward_return(df: pd.DataFrame, position: int, horizon: int) -> float | None:
    """기준일 종가 대비 horizon 영업일 뒤 종가의 수익률 (%)."""
    if position + horizon >= len(df):
        return None
    start = float(df["close"].iloc[position])
    end = float(df["close"].iloc[position + horizon])
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _position(df: pd.DataFrame, asof: date) -> int | None:
    """기준일 이하의 마지막 거래일 위치."""
    positions = np.flatnonzero(df.index <= pd.Timestamp(asof))
    return int(positions[-1]) if positions.size else None


def trading_dates(df: pd.DataFrame, count: int, every: int, horizon: int) -> list[date]:
    """이후 수익률을 낼 수 있는 구간에서 기준일을 고른다 (최근부터 every일 간격)."""
    usable = df.index[MIN_BARS:-horizon] if len(df) > MIN_BARS + horizon else []
    if len(usable) == 0:
        return []
    picked = list(usable[::-1][:: max(every, 1)][:count])
    return sorted(stamp.date() for stamp in picked)


# --------------------------------------------------------------------------- 실행


def run(
    market: str,
    specs: list[ConditionSpec],
    tickers: Iterable,
    dates: list[date],
    horizon: int = 20,
    top: int = 30,
    progress: ProgressFn | None = None,
) -> Backtest:
    """각 기준일에 채점하고 이후 수익률을 붙인다.

    종목당 파일을 한 번만 읽고 기준일마다 잘라 쓴다.
    """
    tickers = list(tickers)
    records = []

    for index, ticker in enumerate(tickers, start=1):
        if progress:
            progress(index, len(tickers))

        df = cache.load(market, ticker.symbol)
        if df is None or len(df) < MIN_BARS + horizon:
            continue
        fundamentals = cache.load_fundamentals(market, ticker.symbol)

        for asof in dates:
            position = _position(df, asof)
            if position is None or position < MIN_BARS:
                continue
            gain = forward_return(df, position, horizon)
            if gain is None:
                continue

            # 기준일까지만 보고 채점한다
            window = df.iloc[: position + 1]
            scores = score_one(window, specs, fundamentals_as_of(fundamentals, asof))
            records.append({
                "asof": asof,
                "symbol": ticker.symbol,
                "name": ticker.name,
                "score": combine(scores, specs),
                "forward_return": gain,
            })

    return Backtest(rows=pd.DataFrame(records), horizon=horizon, top=top)
