"""네이버 JSON API 클라이언트 테스트.

응답 형태는 실제 API 가 돌려준 것을 그대로 본떴다 (doctor 로 확인한 구조).
"""

from datetime import date

import pandas as pd
import pytest

from chartfinder.datasource import naver_api

#: m.stock.naver.com/api/stock/{code}/trend — 껍데기 없이 레코드 배열이 온다
TREND_RESPONSE = [
    {"itemCode": "005930", "bizdate": "20260923", "foreignerPureBuyQuant": "+4,513,767",
     "foreignerHoldRatio": "46.63%", "organPureBuyQuant": "-1,234,567",
     "individualPureBuyQuant": "-3,279,200", "closePrice": "71,900"},
    {"itemCode": "005930", "bizdate": "20260922", "foreignerPureBuyQuant": "-12,000",
     "foreignerHoldRatio": "46.60%", "organPureBuyQuant": "+5,000",
     "individualPureBuyQuant": "+7,000", "closePrice": "71,600"},
    {"itemCode": "005930", "bizdate": "20260921", "foreignerPureBuyQuant": "+1,000",
     "foreignerHoldRatio": "46.58%", "organPureBuyQuant": "+2,000",
     "individualPureBuyQuant": "-3,000", "closePrice": "71,800"},
]

#: .../finance/annual — 항목이 행, 결산기가 열
FINANCE_RESPONSE = {
    "itemCode": "005930",
    "financePeriodType": "annual",
    "financeInfo": [
        {"title": "매출액", "columns": {
            "202312": {"value": "2,589,355", "cx": None},
            "202412": {"value": "3,008,709", "cx": None},
            "202512": {"value": "3,336,059", "cx": None},
            "202612": {"value": "7,378,172", "cx": None}}},
        {"title": "영업이익", "columns": {
            "202312": {"value": "65,670"}, "202412": {"value": "328,726"},
            "202512": {"value": "500,000"}, "202612": {"value": "900,000"}}},
        {"title": "당기순이익", "columns": {
            "202312": {"value": "154,871"}, "202412": {"value": "340,000"}}},
        {"title": "ROE(%)", "columns": {
            "202312": {"value": "4.15"}, "202412": {"value": "9.44"}}},
        {"title": "부채비율(%)", "columns": {
            "202312": {"value": "25.36"}, "202412": {"value": "27.93"}}},
    ],
}


@pytest.fixture
def trend(monkeypatch):
    monkeypatch.setattr(
        naver_api, "get_json", lambda url, params=None, timeout=10.0: TREND_RESPONSE
    )


@pytest.fixture
def finance(monkeypatch):
    monkeypatch.setattr(
        naver_api, "get_json", lambda url, params=None, timeout=10.0: FINANCE_RESPONSE
    )


# --------------------------------------------------------------------------- 응답 탐색


def test_find_records_handles_bare_lists_and_wrappers():
    assert len(naver_api.find_records(TREND_RESPONSE)) == 3
    assert len(naver_api.find_records(FINANCE_RESPONSE)) == 5
    assert naver_api.find_records({"count": 3}) == []


def test_number_parses_signed_and_comma_values():
    assert naver_api._number("+4,513,767") == 4_513_767.0
    assert naver_api._number("-12,000") == -12_000.0
    assert naver_api._number("46.63%") == 46.63
    assert naver_api._number("-") is None


def test_parse_date_handles_both_formats():
    assert naver_api.parse_date("20260923") == pd.Timestamp("2026-09-23")
    assert naver_api.parse_date("2026-09-23") == pd.Timestamp("2026-09-23")


# --------------------------------------------------------------------------- 수급


def test_fetch_flows_parses_trend_response(trend):
    flows = naver_api.fetch_flows("005930", date(2026, 9, 21), date(2026, 9, 23))
    assert list(flows.columns) == ["foreign_net", "inst_net", "indi_net"]
    assert flows.loc["2026-09-23", "foreign_net"] == 4_513_767
    assert flows.loc["2026-09-22", "foreign_net"] == -12_000
    assert flows.loc["2026-09-23", "inst_net"] == -1_234_567
    assert flows.index.is_monotonic_increasing


def test_fetch_flows_ignores_hold_ratio_column(trend):
    """'외국인 보유율' 을 순매수로 착각하면 안 된다."""
    flows = naver_api.fetch_flows("005930", date(2026, 9, 21), date(2026, 9, 23))
    assert flows.loc["2026-09-23", "foreign_net"] != pytest.approx(46.63)


def test_fetch_flows_requests_a_safe_page_size(monkeypatch):
    """pageSize 를 크게 넣으면 네이버가 404 를 준다."""
    seen = []

    def fake(url, params=None, timeout=10.0):
        seen.append(params)
        return TREND_RESPONSE

    monkeypatch.setattr(naver_api, "get_json", fake)
    naver_api.fetch_flows("005930", date(2026, 9, 21), date(2026, 9, 23))
    assert all(p["pageSize"] <= 20 for p in seen)


def test_fetch_flows_pages_back_until_start_is_covered(monkeypatch):
    pages = {}
    for page in range(1, 6):
        day = pd.Timestamp("2026-09-23") - pd.Timedelta(days=(page - 1) * 3)
        pages[page] = [
            {"bizdate": (day - pd.Timedelta(days=offset)).strftime("%Y%m%d"),
             "foreignerPureBuyQuant": "1,000", "organPureBuyQuant": "2,000"}
            for offset in range(3)
        ]

    monkeypatch.setattr(
        naver_api, "get_json",
        lambda url, params=None, timeout=10.0: pages.get(params.get("page", 1), []),
    )
    flows = naver_api.fetch_flows("005930", date(2026, 9, 12), date(2026, 9, 23))
    assert len(flows) > 3  # 첫 페이지만으로는 기간을 못 덮는다
    assert flows.index.min().date() <= date(2026, 9, 14)


def test_fetch_flows_returns_empty_for_unknown_shape(monkeypatch):
    monkeypatch.setattr(naver_api, "get_json", lambda *a, **k: {"message": "not found"})
    assert naver_api.fetch_flows("005930", date(2026, 1, 1), date(2026, 9, 23)).empty


# --------------------------------------------------------------------------- 재무


def test_fetch_fundamentals_parses_item_rows(finance):
    df = naver_api.fetch_fundamentals("005930")
    assert df.loc[2024, "revenue"] == 3_008_709
    assert df.loc[2024, "operating_income"] == 328_726
    assert df.loc[2024, "roe"] == 9.44
    assert df.loc[2023, "debt_ratio"] == 25.36


def test_fetch_fundamentals_excludes_future_periods(finance):
    """202612 는 아직 끝나지 않은 결산기라 컨센서스다."""
    df = naver_api.fetch_fundamentals("005930")
    assert 2026 not in df.index
    assert 2025 in df.index  # 이미 끝난 결산기는 실적


def test_fetch_fundamentals_can_include_estimates(finance):
    df = naver_api.fetch_fundamentals("005930", include_estimates=True)
    assert 2026 in df.index


def test_fetch_fundamentals_derives_operating_margin(finance):
    df = naver_api.fetch_fundamentals("005930")
    assert df.loc[2024, "operating_margin"] == pytest.approx(328_726 / 3_008_709 * 100)


def test_fetch_fundamentals_feeds_conditions(finance):
    from chartfinder.conditions import Ctx, get
    from tests.conftest import make_df

    ctx = Ctx(make_df([100.0] * 40), naver_api.fetch_fundamentals("005930"))
    assert get("revenue_growth").score(ctx, {"years": 3}) == 1.0
    assert get("debt_ratio").score(ctx, {"max_ratio": 100.0, "years": 2}) == 1.0


def test_future_period_detection():
    today = date.today()
    assert naver_api._is_future_period(f"{today.year + 1}12")
    assert not naver_api._is_future_period(f"{today.year - 1}12")
    assert not naver_api._is_future_period("이상한값")


# --------------------------------------------------------------------------- 엔드포인트 후보


def test_try_paths_returns_the_first_success(monkeypatch):
    def fake(url, params=None, timeout=10.0):
        if url.endswith("/005930/trend"):
            return TREND_RESPONSE
        raise ConnectionError("404")

    monkeypatch.setattr(naver_api, "get_json", fake)
    url, data = naver_api.try_paths("005930", naver_api.TREND_PATHS)
    assert url.endswith("/005930/trend")


def test_try_paths_reports_every_attempt_when_all_fail(monkeypatch):
    monkeypatch.setattr(
        naver_api, "get_json",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("차단")),
    )
    with pytest.raises(RuntimeError) as err:
        naver_api.try_paths("005930", naver_api.TREND_PATHS)
    assert "ConnectionError" in str(err.value)


def test_probe_reports_keys_for_each_endpoint(monkeypatch):
    monkeypatch.setattr(naver_api, "get_json", lambda *a, **k: TREND_RESPONSE)
    report = naver_api.probe("005930")
    first = next(iter(report.values()))
    assert first["records"] == 3
    assert "bizdate" in first["record_keys"]
