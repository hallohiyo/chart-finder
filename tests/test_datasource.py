"""데이터 소스 어댑터 테스트.

외부 라이브러리(FinanceDataReader / yfinance / pykrx)를 가짜 모듈로 갈아끼워
네트워크 없이 종목 목록·시세·수급 변환 경로를 검증한다.
"""

import sys
import types
from datetime import date

import pandas as pd
import pytest

from chartfinder.datasource.base import EXCHANGE_COL, normalize_flows, normalize_ohlcv

KOSPI_LISTING = pd.DataFrame(
    {
        "Code": ["005930", "000660", "00104K", "900110"],
        "Name": ["삼성전자", "SK하이닉스", "우선주", "외국기업"],
        "Market": ["KOSPI"] * 4,
        "Marcap": [4.5e14, 9.0e13, 1.0e12, None],
    }
)

US_LISTING = pd.DataFrame(
    {
        "Symbol": ["AAPL", "MSFT", "AAPL", "TEST1"],
        "Name": ["Apple", "Microsoft", "Apple dup", "테스트"],
    }
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """네이버 JSON API 를 빈 응답으로 고정한다.

    KRX 소스가 이 경로를 먼저 시도하므로, 막지 않으면 테스트가 실제 네트워크를
    두드리고 타임아웃만큼 느려진다. 이 경로를 검증하는 테스트는 직접 덮어쓴다.
    """
    from chartfinder.datasource import naver_api

    monkeypatch.setattr(naver_api, "fetch_flows", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(naver_api, "fetch_fundamentals", lambda *a, **k: pd.DataFrame())


def _ohlcv_frame(rows: int = 30) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=rows)
    return pd.DataFrame(
        {
            "Open": [100.0] * rows, "High": [101.0] * rows, "Low": [99.0] * rows,
            "Close": [100.0] * rows, "Volume": [1_000.0] * rows,
        },
        index=index,
    )


@pytest.fixture
def krx(monkeypatch):
    fdr = types.ModuleType("FinanceDataReader")
    fdr.StockListing = lambda key: KOSPI_LISTING.copy()
    fdr.DataReader = lambda symbol, start, end: _ohlcv_frame()
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fdr)

    from chartfinder.datasource.krx import KrxSource

    return KrxSource()


@pytest.fixture
def us(monkeypatch):
    fdr = types.ModuleType("FinanceDataReader")
    fdr.StockListing = lambda key: US_LISTING.copy()
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fdr)

    yf = types.ModuleType("yfinance")
    yf.download = lambda *a, **k: _ohlcv_frame()
    monkeypatch.setitem(sys.modules, "yfinance", yf)

    from chartfinder.datasource.us import UsSource

    return UsSource()


def test_krx_listing_fills_symbol_name_and_exchange(krx):
    """거래소 컬럼 접근이 깨지지 않아야 한다 (이름 맹글링 회귀 방지)."""
    tickers = krx.list_tickers("kospi")
    assert tickers
    first = tickers[0]
    assert first.symbol == "005930"
    assert first.name == "삼성전자"
    assert first.exchange == "KOSPI"
    assert first.market == "kr"


def test_krx_listing_drops_non_numeric_codes(krx):
    codes = [t.symbol for t in krx.list_tickers("kospi")]
    assert codes == ["005930", "000660", "900110"]  # 00104K 는 제외


def test_krx_listing_keeps_missing_marcap_as_none(krx):
    tickers = {t.symbol: t for t in krx.list_tickers("kospi")}
    assert tickers["005930"].marcap == pytest.approx(4.5e14)
    assert tickers["900110"].marcap is None


def test_krx_rejects_unknown_universe(krx):
    with pytest.raises(ValueError):
        krx.list_tickers("sp500")


def test_krx_fetch_ohlcv_is_normalized(krx):
    df = krx.fetch_ohlcv("005930", date(2024, 1, 1), date(2024, 2, 1))
    assert list(df.columns)[:5] == ["open", "high", "low", "close", "volume"]
    assert df.index.is_monotonic_increasing


def test_us_listing_deduplicates_symbols(us):
    symbols = [t.symbol for t in us.list_tickers("sp500")]
    assert symbols.count("AAPL") == 1
    assert "TEST1" not in symbols  # 숫자가 섞인 티커는 제외
    assert all(t.exchange for t in us.list_tickers("sp500"))


def test_krx_fetch_flows_normalizes_pykrx_columns(krx, monkeypatch):
    index = pd.bdate_range("2024-01-01", periods=3)
    raw = pd.DataFrame(
        {
            "기관합계": [100, -50, 20],
            "기타법인": [1, 2, 3],
            "개인": [-200, 60, -30],
            "외국인합계": [99, -12, 7],
            "전체": [0, 0, 0],
        },
        index=index,
    )
    stock = types.ModuleType("stock")
    stock.get_market_trading_volume_by_date = lambda *a, **k: raw.copy()
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)

    flows = krx.fetch_flows("005930", date(2024, 1, 1), date(2024, 1, 3))
    assert list(flows.columns) == ["foreign_net", "inst_net", "indi_net"]
    assert flows["foreign_net"].tolist() == [99, -12, 7]
    assert flows["inst_net"].tolist() == [100, -50, 20]


def test_flows_normalizer_handles_multiindex_columns():
    index = pd.bdate_range("2024-01-01", periods=2)
    raw = pd.DataFrame(
        [[1, 2, 3, 4], [5, 6, 7, 8]],
        index=index,
        columns=pd.MultiIndex.from_product([["순매수", "매수"], ["외국인합계", "기관합계"]]),
    )
    flows = normalize_flows(raw)
    assert flows["foreign_net"].tolist() == [1, 5]


def test_flows_normalizer_survives_unexpected_shape():
    assert normalize_flows(pd.DataFrame()).empty
    assert list(normalize_flows(pd.DataFrame({"뭔가": [1, 2]})).columns) == [
        "foreign_net", "inst_net", "indi_net"
    ]


def test_ohlcv_normalizer_rejects_missing_columns():
    with pytest.raises(ValueError):
        normalize_ohlcv(pd.DataFrame({"Open": [1.0]}))


def test_exchange_column_name_is_attribute_safe():
    """itertuples/getattr 로 접근해도 안전한 이름이어야 한다."""
    assert EXCHANGE_COL.isidentifier()
    assert not EXCHANGE_COL.startswith("_")


def test_krx_flows_never_request_future_dates(krx, monkeypatch):
    """미래 날짜를 넣으면 KRX 가 빈 응답을 준다. 종료일을 오늘로 잘라야 한다."""
    calls = []
    stock = types.ModuleType("stock")

    def record(fromdate, todate, ticker, **kwargs):
        calls.append((fromdate, todate))
        index = pd.bdate_range(
            pd.Timestamp(fromdate), min(pd.Timestamp(todate), pd.Timestamp(date.today()))
        )
        return pd.DataFrame(
            {"외국인합계": [1] * len(index), "기관합계": [2] * len(index)}, index=index
        )

    stock.get_market_trading_volume_by_date = record
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)

    krx.fetch_flows("005930", date.today() - pd.Timedelta(days=10).to_pytimedelta(),
                    date.today() + pd.Timedelta(days=5).to_pytimedelta())
    assert calls
    for _, todate in calls:
        assert pd.Timestamp(todate).date() <= date.today()


def test_krx_flows_are_requested_in_chunks(krx, monkeypatch):
    """긴 기간을 한 번에 요청하면 빈 응답이 오므로 나눠서 받아야 한다."""
    from chartfinder.datasource.krx import FLOW_CHUNK_DAYS

    calls = []
    stock = types.ModuleType("stock")

    def record(fromdate, todate, ticker, **kwargs):
        calls.append((pd.Timestamp(fromdate).date(), pd.Timestamp(todate).date()))
        index = pd.bdate_range(fromdate, todate)
        return pd.DataFrame({"외국인합계": [1] * len(index)}, index=index)

    stock.get_market_trading_volume_by_date = record
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)

    start = date.today() - pd.Timedelta(days=700).to_pytimedelta()
    flows = krx.fetch_flows("005930", start, date.today())

    assert len(calls) >= 4  # 700일이면 180일 단위로 최소 4회
    for chunk_start, chunk_end in calls:
        assert (chunk_end - chunk_start).days < FLOW_CHUNK_DAYS
    assert not flows.empty
    assert not flows.index.duplicated().any()
    assert flows.index.is_monotonic_increasing


def test_krx_flows_return_empty_when_range_is_invalid(krx, monkeypatch):
    stock = types.ModuleType("stock")
    stock.get_market_trading_volume_by_date = lambda *a, **k: pd.DataFrame()
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)

    future = date.today() + pd.Timedelta(days=30).to_pytimedelta()
    assert krx.fetch_flows("005930", future, future).empty


def test_krx_falls_back_to_naver_when_pykrx_is_empty(krx, monkeypatch):
    """KRX 가 빈 응답을 주면 네이버로 넘어가야 한다."""
    stock = types.ModuleType("stock")
    stock.get_market_trading_volume_by_date = lambda *a, **k: pd.DataFrame()
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)

    index = pd.bdate_range("2026-09-14", periods=5)
    naver_result = pd.DataFrame(
        {"foreign_net": [1.0] * 5, "inst_net": [2.0] * 5}, index=index
    )
    from chartfinder.datasource import naver_flows

    monkeypatch.setattr(naver_flows, "fetch", lambda *a, **k: naver_result)

    flows = krx.fetch_flows("005930", date(2026, 9, 14), date.today())
    assert not flows.empty
    assert flows["foreign_net"].iloc[0] == 1.0


def test_krx_reports_both_providers_when_both_fail(krx, monkeypatch):
    stock = types.ModuleType("stock")
    stock.get_market_trading_volume_by_date = lambda *a, **k: pd.DataFrame()
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)

    from chartfinder.datasource import naver_flows

    monkeypatch.setattr(naver_flows, "fetch", lambda *a, **k: pd.DataFrame())

    with pytest.raises(RuntimeError) as err:
        krx.fetch_flows("005930", date(2026, 9, 14), date.today())
    assert "pykrx" in str(err.value) and "naver" in str(err.value)


def test_krx_prefers_the_json_api_over_scraping(krx, monkeypatch):
    """새 네이버는 화면을 JS 로 그려 HTML 에 표가 없다. JSON API 가 먼저다."""
    from chartfinder.datasource import naver_api, naver_flows

    index = pd.bdate_range("2026-09-14", periods=3)
    monkeypatch.setattr(
        naver_api, "fetch_flows",
        lambda *a, **k: pd.DataFrame({"foreign_net": [1.0] * 3}, index=index),
    )
    called = []
    monkeypatch.setattr(
        naver_flows, "fetch",
        lambda *a, **k: called.append(1) or pd.DataFrame(),
    )

    flows = krx.fetch_flows("005930", date(2026, 9, 14), date.today())
    assert not flows.empty
    assert not called  # HTML 경로까지 가지 않는다


def test_us_fundamentals_computed_from_statements(us, monkeypatch):
    """yfinance 는 비율을 주지 않으므로 ROE·부채비율을 직접 계산해야 한다."""
    periods = [pd.Timestamp("2023-12-31"), pd.Timestamp("2024-12-31")]

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        income_stmt = pd.DataFrame(
            [[1000.0, 1200.0], [100.0, 150.0], [80.0, 120.0]],
            index=["Total Revenue", "Operating Income", "Net Income"], columns=periods,
        )
        balance_sheet = pd.DataFrame(
            [[800.0, 1000.0], [400.0, 500.0]],
            index=["Stockholders Equity", "Total Liabilities Net Minority Interest"],
            columns=periods,
        )
        cashflow = pd.DataFrame(
            [[90.0, 140.0]], index=["Operating Cash Flow"], columns=periods
        )

    monkeypatch.setattr(us._yf, "Ticker", FakeTicker, raising=False)
    df = us.fetch_fundamentals("AAPL")

    assert df.loc[2024, "revenue"] == 1200.0
    assert df.loc[2024, "operating_margin"] == pytest.approx(12.5)
    assert df.loc[2024, "roe"] == pytest.approx(12.0)        # 120 / 1000
    assert df.loc[2024, "debt_ratio"] == pytest.approx(50.0)  # 500 / 1000
    assert df.loc[2024, "operating_cash_flow"] == 140.0


def test_us_fundamentals_empty_when_statements_missing(us, monkeypatch):
    class Empty:
        def __init__(self, symbol):
            pass

        income_stmt = pd.DataFrame()
        balance_sheet = pd.DataFrame()
        cashflow = pd.DataFrame()

    monkeypatch.setattr(us._yf, "Ticker", Empty, raising=False)
    assert us.fetch_fundamentals("AAPL").empty


def test_krx_remembers_the_working_flow_provider(krx, monkeypatch):
    """한 종목에서 통한 경로를 다음 종목에서 먼저 쓴다."""
    from chartfinder.datasource import naver_api

    index = pd.bdate_range("2026-09-14", periods=3)
    attempts = []

    def api(symbol, start, end):
        attempts.append("naver-api")
        return pd.DataFrame({"foreign_net": [1.0] * 3}, index=index)

    monkeypatch.setattr(naver_api, "fetch_flows", api)
    monkeypatch.setattr(
        krx, "_flows_pykrx",
        lambda *a: attempts.append("pykrx") or pd.DataFrame(), raising=False,
    )

    krx.fetch_flows("005930", date(2026, 9, 14), date.today())
    attempts.clear()
    krx.fetch_flows("000660", date(2026, 9, 14), date.today())
    assert attempts == ["naver-api"]


def test_krx_forgets_the_provider_when_it_stops_working(krx, monkeypatch):
    from chartfinder.datasource import naver_api

    monkeypatch.setattr(naver_api, "fetch_flows", lambda *a, **k: pd.DataFrame())
    stock = types.ModuleType("stock")
    stock.get_market_trading_volume_by_date = lambda *a, **k: pd.DataFrame()
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)
    from chartfinder.datasource import naver_flows

    monkeypatch.setattr(naver_flows, "fetch", lambda *a, **k: pd.DataFrame())

    with pytest.raises(RuntimeError):
        krx.fetch_flows("005930", date(2026, 9, 14), date.today())
    assert krx._flow_provider is None


def test_pykrx_chatter_does_not_reach_the_console(krx, monkeypatch, capsys):
    """pykrx 는 실패를 예외 대신 표준출력으로 흘린다. 진행 표시를 덮으면 안 된다."""
    stock = types.ModuleType("stock")

    def noisy(*args, **kwargs):
        print("Error occurred in get_market_trading_value_and_volume_on_ticker_by_date")
        return pd.DataFrame()

    stock.get_market_trading_volume_by_date = noisy
    pykrx = types.ModuleType("pykrx")
    pykrx.stock = stock
    monkeypatch.setitem(sys.modules, "pykrx", pykrx)

    krx._flows_pykrx("005930", date(2026, 9, 1), date(2026, 9, 20))
    assert "Error occurred" not in capsys.readouterr().out
