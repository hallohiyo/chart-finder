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
#: 종목 목록에 거래소 이름을 담아 두는 임시 컬럼.
#: 앞에 밑줄을 쓰면 안 된다 — 파이썬 이름 맹글링/식별자 제약에 걸린다.
EXCHANGE_COL = "cf_exchange"
#: 투자자별 순매수 (주식 수). 지원하는 소스만 채운다.
FLOW_COLUMNS = ["foreign_net", "inst_net", "indi_net"]


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

    #: 투자자별 순매수(외국인·기관 수급)를 받을 수 있는 소스인지
    supports_flows: bool = False
    #: 재무 데이터를 받을 수 있는 소스인지
    supports_fundamentals: bool = False

    def fetch_fundamentals(self, symbol: str) -> pd.DataFrame:
        """연간 재무 지표. 지원하지 않는 소스는 빈 프레임."""
        return pd.DataFrame()

    def fetch_flows(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """투자자별 일별 순매수. 지원하지 않는 소스는 빈 프레임."""
        return pd.DataFrame(columns=FLOW_COLUMNS)

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

    extra = [c for c in ["value", *FLOW_COLUMNS] if c in df.columns]
    df = df[OHLCV_COLUMNS + extra]

    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # 거래정지 등으로 종가가 0/결측인 행은 버린다
    df = df[df["close"].notna() & (df["close"] > 0)]
    return df


def normalize_flows(df: pd.DataFrame) -> pd.DataFrame:
    """투자자별 순매수 프레임을 표준 컬럼명으로 정리.

    소스마다 컬럼명이 '외국인합계', '외국인', ('순매수','외국인') 등으로 달라서
    이름에 포함된 키워드로 찾는다. 단위는 주식 수.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=FLOW_COLUMNS)

    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        # ('순매수', '외국인합계') 같은 형태면 순매수 레벨만 고른다
        levels = [lvl for lvl in range(df.columns.nlevels)
                  if any("순매수" in str(v) for v in df.columns.get_level_values(lvl))]
        if levels:
            df = df.xs("순매수", axis=1, level=levels[0])
        else:
            df.columns = df.columns.get_level_values(-1)

    keywords = {"foreign_net": "외국인", "inst_net": "기관", "indi_net": "개인"}
    out = pd.DataFrame(index=pd.to_datetime(df.index).tz_localize(None).normalize())
    for target, keyword in keywords.items():
        matches = [c for c in df.columns if keyword in str(c)]
        if matches:
            # '외국인합계'가 있으면 그걸, 없으면 첫 번째 매칭 컬럼
            col = next((c for c in matches if "합계" in str(c)), matches[0])
            out[target] = pd.to_numeric(df[col], errors="coerce").to_numpy()

    if out.empty or not len(out.columns):
        return pd.DataFrame(columns=FLOW_COLUMNS)
    out.index.name = "date"
    return out[~out.index.duplicated(keep="last")].sort_index()
