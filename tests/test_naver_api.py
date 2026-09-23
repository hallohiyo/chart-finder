"""네이버 JSON API 클라이언트 테스트 (응답을 가짜로 주입)."""

from datetime import date

import pandas as pd
import pytest

from chartfinder.datasource import naver_api

TREND_RESPONSE = {
    "totalCount": 3,
    "trends": [
        {"localTradedAt": "2026-09-23", "foreignerPureBuyQuant": "234,567",
         "organPureBuyQuant": "123,456", "individualPureBuyQuant": "-358,023"},
        {"localTradedAt": "2026-09-22", "foreignerPureBuyQuant": "-12,000",
         "organPureBuyQuant": "5,000", "individualPureBuyQuant": "7,000"},
        {"localTradedAt": "2026-09-21", "foreignerPureBuyQuant": "1,000",
         "organPureBuyQuant": "2,000", "individualPureBuyQuant": "-3,000"},
    ],
}

FINANCE_RESPONSE = {
    "financeInfo": [
        {"yearMonth": "2022.12", "salesAccount": "2,000,000", "operatingProfit": "200,000",
         "netIncome": "150,000", "operatingProfitRatio": "10.00", "debtRatio": "80.00",
         "roe": "8.00"},
        {"yearMonth": "2023.12", "salesAccount": "2,200,000", "operatingProfit": "250,000",
         "netIncome": "190,000", "operatingProfitRatio": "11.36", "debtRatio": "70.00",
         "roe": "11.00"},
        {"yearMonth": "2024.12", "salesAccount": "2,500,000", "operatingProfit": "300,000",
         "netIncome": "240,000", "operatingProfitRatio": "12.00", "debtRatio": "60.00",
         "roe": "13.00"},
    ]
}


@pytest.fixture
def trend(monkeypatch):
    monkeypatch.setattr(naver_api, "get_json", lambda url, params=None, timeout=10.0: TREND_RESPONSE)


@pytest.fixture
def finance(monkeypatch):
    monkeypatch.setattr(naver_api, "get_json", lambda url, params=None, timeout=10.0: FINANCE_RESPONSE)


# --------------------------------------------------------------------------- 응답 탐색


def test_find_records_digs_into_wrapped_responses():
    assert len(naver_api.find_records(TREND_RESPONSE)) == 3
    assert len(naver_api.find_records({"a": {"b": FINANCE_RESPONSE}})) == 3
    assert naver_api.find_records({"count": 3}) == []


def test_find_records_handles_bare_lists():
    assert len(naver_api.find_records([{"a": 1}, {"b": 2}])) == 2


def test_number_parses_korean_style_values():
    assert naver_api._number("1,234") == 1234.0
    assert naver_api._number("-12.5%") == -12.5
    assert naver_api._number("-") is None
    assert naver_api._number(None) is None


def test_pick_matches_keys_loosely():
    record = {"foreignerPureBuyQuant": 5, "somethingElse": 1}
    assert naver_api._pick(record, ("foreignerpurebuyquant",)) == 5
    assert naver_api._pick(record, ("foreigner",)) == 5
    assert naver_api._pick(record, ("없는키",)) is None


# --------------------------------------------------------------------------- 수급


def test_fetch_flows_parses_trend_response(trend):
    flows = naver_api.fetch_flows("005930", date(2026, 9, 21), date(2026, 9, 23))
    assert list(flows.columns) == ["foreign_net", "inst_net", "indi_net"]
    assert flows.loc["2026-09-23", "foreign_net"] == 234_567
    assert flows.loc["2026-09-22", "foreign_net"] == -12_000
    assert flows.index.is_monotonic_increasing


def test_fetch_flows_trims_to_the_requested_range(trend):
    flows = naver_api.fetch_flows("005930", date(2026, 9, 22), date(2026, 9, 23))
    assert len(flows) == 2


def test_fetch_flows_returns_empty_for_unknown_shape(monkeypatch):
    monkeypatch.setattr(naver_api, "get_json", lambda *a, **k: {"message": "not found"})
    assert naver_api.fetch_flows("005930", date(2026, 1, 1), date(2026, 9, 23)).empty


# --------------------------------------------------------------------------- 재무


def test_fetch_fundamentals_parses_finance_response(finance):
    df = naver_api.fetch_fundamentals("005930")
    assert df.index.tolist() == [2022, 2023, 2024]
    assert df.loc[2024, "revenue"] == 2_500_000
    assert df.loc[2024, "operating_margin"] == 12.0
    assert df.loc[2023, "roe"] == 11.0


def test_fetch_fundamentals_feeds_conditions(finance):
    from chartfinder.conditions import Ctx, get
    from tests.conftest import make_df

    ctx = Ctx(make_df([100.0] * 40), naver_api.fetch_fundamentals("005930"))
    assert get("revenue_growth").score(ctx, {"years": 3}) == 1.0
    assert get("debt_ratio").score(ctx, {"max_ratio": 100.0, "years": 3}) == 1.0


# --------------------------------------------------------------------------- 엔드포인트 후보


def test_try_paths_returns_the_first_success(monkeypatch):
    seen = []

    def fake(url, params=None, timeout=10.0):
        seen.append(url)
        if url.endswith("/005930/investor"):
            return {"ok": True}
        raise ConnectionError("404")

    monkeypatch.setattr(naver_api, "get_json", fake)
    url, data = naver_api.try_paths("005930", naver_api.TREND_PATHS)
    assert url.endswith("/005930/investor")
    assert data == {"ok": True}


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
    assert report
    first = next(iter(report.values()))
    assert first["records"] == 3
    assert "localTradedAt" in first["record_keys"]
