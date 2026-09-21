"""데이터 소스 공통 인터페이스.

모든 소스는 동일한 OHLCV 스키마를 돌려준다:
  index : DatetimeIndex (오름차순, 중복/결측 없음)
  cols  : open, high, low, close, volume [, value]
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import date

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class Ticker:
    symbol: str
    name: str
    market: str  # "kr" | "us"
    exchange: str = ""
    marcap: float | None = None  # 시가총액 (원/달러)

    @property
    def uid(self) -> str:
        return f"{self.market}:{self.symbol}"


class DataSource(abc.ABC):
    """시장 한 곳을 담당하는 어댑터."""

    market: str
    universes: tuple[str, ...] = ("all",)

    @abc.abstractmethod
    def list_tickers(self, universe: str = "all") -> list[Ticker]:
        """대상 종목 목록."""

    @abc.abstractmethod
    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """종목 하나의 일봉."""

    def fetch_many(
        self, symbols: list[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        """여러 종목을 한 번에. 기본 구현은 순차 호출, 소스가 배치를 지원하면 재정의."""
        out: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                df = self.fetch_ohlcv(sym, start, end)
            except Exception:
                continue
            if df is not None and not df.empty:
                out[sym] = df
        return out


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """소스별 컬럼명을 표준 스키마로 정리."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    alias = {
        "adj_close": "close",
        "종가": "close", "시가": "open", "고가": "high", "저가": "low",
        "거래량": "volume", "거래대금": "value",
    }
    df = df.rename(columns={k: v for k, v in alias.items() if k in df.columns})
    df = df.loc[:, ~df.columns.duplicated()]

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV 컬럼 누락: {missing} (받은 컬럼: {list(df.columns)})")

    keep = OHLCV_COLUMNS + (["value"] if "value" in df.columns else [])
    df = df[keep]

    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # 거래정지 등으로 종가가 0/결측인 행은 버린다
    df = df[df["close"].notna() & (df["close"] > 0)]
    return df
