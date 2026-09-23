"""한국거래소 (KOSPI / KOSDAQ) — FinanceDataReader 기반."""

from __future__ import annotations

import contextlib
import io
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
        self._flow_provider: str | None = None  # 마지막으로 통한 수급 경로
        self._fundamental_provider: str | None = None

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

        providers = {
            "naver-api": self._flows_naver_api,
            "pykrx": self._flows_pykrx,
            "naver-html": self._flows_naver,
        }
        # 한 번 통한 경로를 먼저 쓴다. 종목마다 죽은 경로를 다시 두드리면
        # 전 종목 수집에서 헛된 요청이 수천 번 쌓인다.
        order = [self._flow_provider] if self._flow_provider else []
        order += [name for name in providers if name != self._flow_provider]

        errors: list[str] = []
        for name in order:
            try:
                flows = providers[name](symbol, start, end)
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if flows is not None and not flows.empty:
                self._flow_provider = name
                return flows
            errors.append(f"{name}: 빈 응답")

        self._flow_provider = None  # 다음 종목에서는 처음부터 다시 찾는다
        if errors:
            raise RuntimeError(" / ".join(errors))
        return pd.DataFrame()

    def fetch_fundamentals(self, symbol: str) -> pd.DataFrame:
        """연간 재무 지표.

        네이버 JSON API 를 먼저, 안 되면 옛 HTML 표를 읽는다. 네이버는 현금흐름
        항목을 주지 않으므로, DART 키가 설정돼 있으면 영업활동현금흐름만 덧붙인다.
        """
        from . import naver_api, naver_fundamentals

        providers = {
            "naver-api": naver_api.fetch_fundamentals,
            "naver-html": naver_fundamentals.fetch,
        }
        order = [self._fundamental_provider] if self._fundamental_provider else []
        order += [name for name in providers if name != self._fundamental_provider]

        errors = []
        for name in order:
            try:
                df = providers[name](symbol)
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if df is not None and not df.empty:
                self._fundamental_provider = name
                return self._with_cash_flow(symbol, df)
            errors.append(f"{name}: 빈 응답")

        self._fundamental_provider = None
        raise RuntimeError(" / ".join(errors))

    def _with_cash_flow(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        """DART 에서 영업활동현금흐름을 받아 채운다 (키가 있을 때만)."""
        from . import dart

        if not dart.enabled() or df["operating_cash_flow"].notna().any():
            return df
        try:
            flows = dart.fetch_cash_flow(symbol)
        except Exception:
            return df  # 현금흐름 하나 때문에 나머지 재무를 버리지는 않는다
        if flows is None or flows.empty:
            return df

        df = df.copy()
        df["operating_cash_flow"] = flows["operating_cash_flow"].reindex(df.index)
        return df

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
            # pykrx 는 실패를 예외 대신 표준출력으로 흘린다. 종목마다 찍히면
            # 진행 상황을 덮어버리므로 삼키고, 결과가 비었는지로 판단한다.
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
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
