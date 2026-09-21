"""한국거래소 (KOSPI / KOSDAQ) — FinanceDataReader 기반."""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import DataSource, Ticker, normalize_ohlcv


class KrxSource(DataSource):
    market = "kr"
    universes = ("all", "kospi", "kosdaq")

    def __init__(self) -> None:
        import FinanceDataReader as fdr  # 지연 import: 네트워크 의존 모듈

        self._fdr = fdr

    def list_tickers(self, universe: str = "all") -> list[Ticker]:
        universe = universe.lower()
        if universe not in self.universes:
            raise ValueError(f"지원하지 않는 유니버스: {universe} (가능: {self.universes})")

        frames = []
        for mkt in (["KOSPI", "KOSDAQ"] if universe == "all" else [universe.upper()]):
            df = self._fdr.StockListing(mkt)
            df["__exchange"] = mkt
            frames.append(df)
        listing = pd.concat(frames, ignore_index=True)

        code_col = _first_col(listing, ["Code", "Symbol"])
        name_col = _first_col(listing, ["Name"])
        marcap_col = _first_col(listing, ["Marcap", "MarketCap"], required=False)

        tickers: list[Ticker] = []
        for row in listing.itertuples(index=False):
            code = str(getattr(row, code_col, "")).strip()
            name = str(getattr(row, name_col, "")).strip()
            if not code or not name:
                continue
            # 스팩/우선주/리츠 등 6자리 숫자가 아닌 코드는 제외
            if not (len(code) == 6 and code.isdigit()):
                continue
            marcap = getattr(row, marcap_col, None) if marcap_col else None
            tickers.append(
                Ticker(
                    symbol=code,
                    name=name,
                    market=self.market,
                    exchange=str(row.__exchange),
                    marcap=float(marcap) if pd.notna(marcap) else None,
                )
            )
        return tickers

    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = self._fdr.DataReader(symbol, str(start), str(end))
        return normalize_ohlcv(df)


def _first_col(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    if required:
        raise ValueError(f"컬럼을 찾을 수 없음: {candidates} (보유: {list(df.columns)})")
    return None
