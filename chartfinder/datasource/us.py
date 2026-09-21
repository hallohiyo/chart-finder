"""미국 시장 (NYSE / NASDAQ) — 종목목록은 FinanceDataReader, 시세는 yfinance."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..fundamentals import normalize
from .base import EXCHANGE_COL, DataSource, Ticker, normalize_ohlcv

_LISTING_KEY = {
    "sp500": "S&P500",
    "nasdaq": "NASDAQ",
    "nyse": "NYSE",
    "amex": "AMEX",
}


class UsSource(DataSource):
    market = "us"
    universes = ("sp500", "nasdaq", "nyse", "amex", "all")

    supports_fundamentals = True

    #: yfinance 배치 다운로드 한 묶음 크기
    batch_size = 100

    def __init__(self) -> None:
        import FinanceDataReader as fdr
        import yfinance as yf

        self._fdr = fdr
        self._yf = yf

    def list_tickers(self, universe: str = "sp500") -> list[Ticker]:
        universe = universe.lower()
        if universe not in self.universes:
            raise ValueError(f"지원하지 않는 유니버스: {universe} (가능: {self.universes})")

        keys = ["NASDAQ", "NYSE", "AMEX"] if universe == "all" else [_LISTING_KEY[universe]]
        frames = []
        for key in keys:
            df = self._fdr.StockListing(key)
            df[EXCHANGE_COL] = key
            frames.append(df)
        listing = pd.concat(frames, ignore_index=True)

        sym_col = "Symbol" if "Symbol" in listing.columns else "Code"
        name_col = "Name" if "Name" in listing.columns else sym_col

        seen: set[str] = set()
        tickers: list[Ticker] = []
        for row in listing.to_dict("records"):
            sym = str(row.get(sym_col, "")).strip().upper()
            if not sym or sym in seen or not _looks_like_symbol(sym):
                continue
            seen.add(sym)
            tickers.append(
                Ticker(
                    symbol=sym,
                    name=str(row.get(name_col, sym)).strip() or sym,
                    market=self.market,
                    exchange=str(row.get(EXCHANGE_COL, "")),
                )
            )
        return tickers

    def fetch_fundamentals(self, symbol: str) -> pd.DataFrame:
        """연간 재무 지표. yfinance 재무제표에서 뽑고 비율은 계산한다."""
        ticker = self._yf.Ticker(symbol)
        income, balance, cash = ticker.income_stmt, ticker.balance_sheet, ticker.cashflow

        revenue = _row(income, "Total Revenue", "Operating Revenue")
        operating = _row(income, "Operating Income", "EBIT")
        net = _row(income, "Net Income", "Net Income Common Stockholders")
        equity = _row(balance, "Stockholders Equity", "Total Equity Gross Minority Interest")
        liabilities = _row(balance, "Total Liabilities Net Minority Interest", "Total Debt")
        operating_cash = _row(cash, "Operating Cash Flow", "Total Cash From Operating Activities")

        columns = {
            "revenue": revenue, "operating_income": operating, "net_income": net,
            "operating_cash_flow": operating_cash,
        }
        frame = pd.DataFrame({k: v for k, v in columns.items() if v is not None})
        if frame.empty:
            return pd.DataFrame()

        # 비율 항목은 yfinance 가 주지 않으므로 직접 계산한다
        if equity is not None:
            safe_equity = equity.reindex(frame.index).replace(0, pd.NA)
            if net is not None:
                frame["roe"] = net.reindex(frame.index) / safe_equity * 100.0
            if liabilities is not None:
                frame["debt_ratio"] = liabilities.reindex(frame.index) / safe_equity * 100.0
        return normalize(frame)

    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = self._yf.download(
            symbol,
            start=str(start),
            end=str(end),
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        return normalize_ohlcv(df)

    def fetch_many(
        self, symbols: list[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), self.batch_size):
            chunk = symbols[i : i + self.batch_size]
            raw = self._yf.download(
                chunk,
                start=str(start),
                end=str(end),
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
            if raw is None or raw.empty:
                continue
            for sym in chunk:
                try:
                    sub = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
                    df = normalize_ohlcv(sub.dropna(how="all"))
                except (KeyError, ValueError):
                    continue
                if not df.empty:
                    out[sym] = df
        return out


def _row(df, *names):
    """재무제표 프레임(행=항목)에서 항목 하나를 뽑는다."""
    if df is None or df.empty:
        return None
    lookup = {str(i).strip().lower(): i for i in df.index}
    for name in names:
        key = name.strip().lower()
        if key in lookup:
            return df.loc[lookup[key]]
    return None


def _looks_like_symbol(sym: str) -> bool:
    """워런트/우선주(BRK.A 류 제외 대상 아님)·테스트 티커 정도만 걸러낸다."""
    if len(sym) > 6 or not sym.replace(".", "").replace("-", "").isalnum():
        return False
    return sym.replace(".", "").replace("-", "").isalpha()
