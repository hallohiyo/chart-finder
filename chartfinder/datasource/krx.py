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
    #: 종목당 1회 요청이라 순차로 받으면 2,600종목에 한 시간이 걸린다.
    #: 8이면 대체로 8배 가까이 빨라지고, 그 이상은 상대 서버가 막기 시작한다.
    max_workers = 8
    #: 이 단위로 받고 진행률을 갱신한다
    batch_size = 24
    supports_flows = True
    supports_fundamentals = True
    supports_profiles = True

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

    # ------------------------------------------------------------- 종목 정보
    def fetch_profiles(self, symbols: list[str], universe: str = "all") -> pd.DataFrame:
        """시가총액·주식수·지분율 스냅샷.

        항목마다 출처가 다르고, 어느 하나가 막혀도 나머지는 채워야 한다.
        어디서 뭘 받았는지는 `self.profile_notes` 에 남긴다.
        """
        self.profile_notes: dict[str, str] = {}
        wanted = [s for s in symbols if len(s) == 6 and s.isdigit()]
        frame = pd.DataFrame(index=pd.Index(wanted, name="symbol"), dtype="float64")

        for name, filler in (
            ("상장/시총 (FinanceDataReader)", self._profiles_from_listing),
            ("외국인 지분율 (pykrx)", self._profiles_foreign),
            ("공매도 (pykrx)", self._profiles_short),
            ("대주주·CB/BW (DART)", self._profiles_dart),
        ):
            try:
                filler(frame, universe)
            except Exception as exc:
                self.profile_notes[name] = f"실패: {type(exc).__name__}: {exc}"
                continue
            self.profile_notes.setdefault(name, "받음")

        # 유통주식수는 어디서도 안 준다. 잠긴 물량(대주주)을 뺀 값으로 센다.
        if "shares" in frame and "major_pct" in frame:
            frame["float_shares"] = frame["shares"] * (1.0 - frame["major_pct"] / 100.0)
        if "shares" in frame and "dilution_shares" in frame:
            frame["dilution_pct"] = frame["dilution_shares"] / frame["shares"] * 100.0
            frame = frame.drop(columns=["dilution_shares"])
        return frame

    def _profiles_from_listing(self, frame: pd.DataFrame, universe: str) -> None:
        frames = []
        for mkt in (["KOSPI", "KOSDAQ"] if universe == "all" else [universe.upper()]):
            frames.append(self._fdr.StockListing(mkt))
        listing = pd.concat(frames, ignore_index=True)
        code_col = _first_col(listing, ["Code", "Symbol"])
        listing[code_col] = listing[code_col].astype(str).str.zfill(6)
        listing = listing.set_index(code_col)
        for field, candidates in (
            ("marcap", ["Marcap", "MarketCap"]),
            ("shares", ["Stocks", "Shares", "ListedShares"]),
        ):
            col = _first_col(listing, candidates, required=False)
            if col:
                frame[field] = pd.to_numeric(listing[col], errors="coerce").reindex(frame.index)

    def _profiles_foreign(self, frame: pd.DataFrame, universe: str) -> None:
        """외국인 지분율. 종목별이 아니라 시장 단위 1회 요청이라 싸다."""
        for table in self._pykrx_by_ticker("get_exhaustion_rates_of_foreign_investment", universe):
            if "지분율" in table:
                frame["foreign_pct"] = _merge_column(frame, table["지분율"])
            if "상장주식수" in table and "shares" not in frame:
                frame["shares"] = _merge_column(frame, table["상장주식수"])

    def _profiles_short(self, frame: pd.DataFrame, universe: str) -> None:
        for table in self._pykrx_by_ticker("get_shorting_volume_by_ticker", universe):
            if "비중" in table:
                frame["short_ratio"] = _merge_column(frame, table["비중"])
        for table in self._pykrx_by_ticker("get_shorting_balance_by_ticker", universe):
            if "비중" in table:
                frame["loan_ratio"] = _merge_column(frame, table["비중"])

    def _pykrx_by_ticker(self, func_name: str, universe: str):
        """시장별 표를 순서대로 돌려준다. 최근 영업일을 며칠 거슬러 시도한다."""
        if self._pykrx is None:
            from pykrx import stock

            self._pykrx = stock
        func = getattr(self._pykrx, func_name, None)
        if func is None:
            raise RuntimeError(f"pykrx 에 {func_name} 이 없습니다 (버전 확인).")

        markets = ["KOSPI", "KOSDAQ"] if universe == "all" else [universe.upper()]
        for back in range(0, 8):  # 휴일·집계 지연을 감안해 며칠 물러난다
            day = (date.today() - timedelta(days=back)).strftime("%Y%m%d")
            tables = []
            for mkt in markets:
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    try:
                        table = func(day, market=mkt)
                    except Exception:
                        table = pd.DataFrame()
                if table is not None and not table.empty:
                    table.index = table.index.astype(str).str.zfill(6)
                    tables.append(table)
            if tables:
                return tables
        raise RuntimeError(f"{func_name}: 최근 8일 안에 응답이 없습니다.")

    def _profiles_dart(self, frame: pd.DataFrame, universe: str) -> None:
        """대주주 지분율과 CB/BW 물량. 종목당 요청이라 DART 키가 있을 때만 한다."""
        from . import dart

        if not dart.enabled():
            raise RuntimeError("DART_API_KEY 가 없어 대주주 지분율·CB/BW 는 건너뜁니다.")

        major: dict[str, float] = {}
        dilution: dict[str, float] = {}
        for symbol in frame.index:
            pct = dart.fetch_major_holder_pct(symbol)
            if pct is not None:
                major[symbol] = pct
            shares = dart.fetch_dilution_shares(symbol)
            if shares is not None:
                dilution[symbol] = shares
        if major:
            frame["major_pct"] = pd.Series(major).reindex(frame.index)
        if dilution:
            frame["dilution_shares"] = pd.Series(dilution).reindex(frame.index)

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


def _merge_column(frame: pd.DataFrame, series: pd.Series) -> pd.Series:
    """스냅샷 표의 한 컬럼을 대상 종목 순서에 맞춘다."""
    return pd.to_numeric(series, errors="coerce").reindex(frame.index)


def _first_col(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    if required:
        raise ValueError(f"컬럼을 찾을 수 없음: {candidates} (보유: {list(df.columns)})")
    return None
