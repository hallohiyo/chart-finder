"""한국거래소 (KOSPI / KOSDAQ) — FinanceDataReader 기반."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import EXCHANGE_COL, DataSource, Ticker, normalize_flows, normalize_ohlcv


#: KRX 일별추이를 한 번에 조회할 최대 일수. 길면 빈 응답이 돌아온다.
FLOW_CHUNK_DAYS = 180


class KrxSource(DataSource):
    market = "kr"
    universes = ("all", "kospi", "kosdaq")
    supports_flows = True
    supports_fundamentals = True

    def __init__(self) -> None:
        import FinanceDataReader as fdr  # 지연 import: 네트워크 의존 모듈

        self._fdr = fdr
        self._pykrx = None  # 수급을 쓸 때만 로드

    def list_tickers(self, universe: str = "all") -> list[Ticker]:
        universe = universe.lower()
        if universe not in self.universes:
            raise ValueError(f"지원하지 않는 유니버스: {universe} (가능: {self.universes})")

        frames = []
        for mkt in (["KOSPI", "KOSDAQ"] if universe == "all" else [universe.upper()]):
            df = self._fdr.StockListing(mkt)
            df[EXCHANGE_COL] = mkt
            frames.append(df)
        listing = pd.concat(frames, ignore_index=True)

        code_col = _first_col(listing, ["Code", "Symbol"])
        name_col = _first_col(listing, ["Name"])
        marcap_col = _first_col(listing, ["Marcap", "MarketCap"], required=False)

        tickers: list[Ticker] = []
        for row in listing.to_dict("records"):
            code = str(row.get(code_col, "")).strip()
            name = str(row.get(name_col, "")).strip()
            if not code or not name:
                continue
            # 스팩/우선주/리츠 등 6자리 숫자가 아닌 코드는 제외
            if not (len(code) == 6 and code.isdigit()):
                continue
            marcap = row.get(marcap_col) if marcap_col else None
            tickers.append(
                Ticker(
                    symbol=code,
                    name=name,
                    market=self.market,
                    exchange=str(row.get(EXCHANGE_COL, "")),
                    marcap=float(marcap) if pd.notna(marcap) else None,
                )
            )
        return tickers

    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = self._fdr.DataReader(symbol, str(start), str(end))
        return normalize_ohlcv(df)

    def fetch_flows(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """외국인·기관 일별 순매수 (주식 수).

        KRX 일별추이(pykrx)를 먼저 시도하고, 빈 응답이면 네이버 금융으로 넘어간다.
        KRX 쪽은 차단·엔드포인트 변경으로 빈 응답을 주는 일이 잦다.
        """
        end = min(end, date.today())
        if start > end:
            return pd.DataFrame()

        errors: list[str] = []
        for name, fetcher in (
            ("naver-api", self._flows_naver_api),
            ("pykrx", self._flows_pykrx),
            ("naver-html", self._flows_naver),
        ):
            try:
                flows = fetcher(symbol, start, end)
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if flows is not None and not flows.empty:
                return flows
            errors.append(f"{name}: 빈 응답")

        if errors:
            raise RuntimeError(" / ".join(errors))
        return pd.DataFrame()

    def fetch_fundamentals(self, symbol: str) -> pd.DataFrame:
        """연간 재무 지표. 네이버 JSON API 를 먼저, 안 되면 옛 HTML 표를 읽는다."""
        from . import naver_api, naver_fundamentals

        errors = []
        for name, fetcher in (("naver-api", naver_api.fetch_fundamentals),
                              ("naver-html", naver_fundamentals.fetch)):
            try:
                df = fetcher(symbol)
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if df is not None and not df.empty:
                return df
            errors.append(f"{name}: 빈 응답")
        raise RuntimeError(" / ".join(errors))

    def _flows_naver_api(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from . import naver_api

        return naver_api.fetch_flows(symbol, start, end)

    def _flows_naver(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """옛 HTML 표. 새 네이버는 화면을 JS 로 그려 표가 없지만, 남겨둔다."""
        from . import naver_flows

        return naver_flows.fetch(symbol, start, end)

    def _flows_pykrx(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """KRX 일별추이. 긴 기간은 빈 응답을 주므로 FLOW_CHUNK_DAYS 단위로 나눈다."""
        if self._pykrx is None:
            try:
                from pykrx import stock
            except ImportError as exc:  # 의존성 누락을 조용히 넘기지 않는다
                raise RuntimeError(
                    "수급 데이터에는 pykrx 가 필요합니다. `pip install pykrx` 로 설치하세요."
                ) from exc

            self._pykrx = stock

        frames = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=FLOW_CHUNK_DAYS - 1), end)
            raw = self._pykrx.get_market_trading_volume_by_date(
                cursor.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"), symbol
            )
            chunk = normalize_flows(raw)
            if not chunk.empty:
                frames.append(chunk)
            cursor = chunk_end + timedelta(days=1)

        if not frames:
            return pd.DataFrame()
        merged = pd.concat(frames)
        return merged[~merged.index.duplicated(keep="last")].sort_index()


def _first_col(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    if required:
        raise ValueError(f"컬럼을 찾을 수 없음: {candidates} (보유: {list(df.columns)})")
    return None
