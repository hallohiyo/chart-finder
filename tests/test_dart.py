"""DART 영업활동현금흐름 경로 테스트 (응답을 가짜로 주입)."""

from datetime import date

import pandas as pd
import pytest

from chartfinder.datasource import dart

#: fnlttSinglAcntAll.json 응답에서 필요한 부분만
CF_ROWS = [
    {"sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "500,000"},
    {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "300,000"},
    {"sj_div": "CF", "account_nm": "영업활동 현금흐름",
     "thstrm_amount": "58,000", "frmtrm_amount": "44,000", "bfefrmtrm_amount": "-2,000"},
    {"sj_div": "CF", "account_nm": "투자활동 현금흐름",
     "thstrm_amount": "-30,000", "frmtrm_amount": "-25,000", "bfefrmtrm_amount": "-20,000"},
]


def test_disabled_without_a_key(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    assert dart.api_key() is None
    assert not dart.enabled()


def test_enabled_with_a_key(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "abc123")
    assert dart.enabled()


def test_blank_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "   ")
    assert not dart.enabled()


def test_parse_cash_flow_picks_operating_rows_only():
    df = dart.parse_cash_flow(CF_ROWS, 2025)
    assert list(df.index) == [2023, 2024, 2025]
    assert df.loc[2025, "operating_cash_flow"] == 58_000
    assert df.loc[2023, "operating_cash_flow"] == -2_000  # 마이너스도 그대로


def test_parse_cash_flow_ignores_investing_and_other_statements():
    df = dart.parse_cash_flow(CF_ROWS, 2025)
    assert (df["operating_cash_flow"] != -30_000).all()


def test_parse_cash_flow_handles_spaced_account_names():
    rows = [{"sj_div": "CF", "account_nm": "영 업 활 동 으 로 인 한 현금흐름",
             "thstrm_amount": "10"}]
    assert not dart.parse_cash_flow(rows, 2025).empty


def test_parse_cash_flow_skips_empty_amounts():
    rows = [{"sj_div": "CF", "account_nm": "영업활동현금흐름",
             "thstrm_amount": "100", "frmtrm_amount": "-", "bfefrmtrm_amount": ""}]
    df = dart.parse_cash_flow(rows, 2025)
    assert list(df.index) == [2025]


def test_parse_cash_flow_returns_empty_when_absent():
    assert dart.parse_cash_flow([{"sj_div": "BS", "account_nm": "자산총계"}], 2025).empty


def test_fetch_cash_flow_falls_back_to_separate_statements(monkeypatch):
    """연결재무제표가 없으면 별도재무제표를 본다."""
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(path, **params):
        calls.append(params.get("fs_div"))
        if params.get("fs_div") == "CFS":
            return Response({"status": "013", "message": "데이터 없음"})
        return Response({"status": "000", "list": CF_ROWS})

    monkeypatch.setattr(dart, "_get", fake_get)
    monkeypatch.setattr(dart, "corp_codes", lambda refresh=False: {"005930": "00126380"})

    df = dart.fetch_cash_flow("005930", year=2025)
    assert calls == ["CFS", "OFS"]
    assert df.loc[2025, "operating_cash_flow"] == 58_000


def test_fetch_cash_flow_returns_empty_for_unknown_symbol(monkeypatch):
    monkeypatch.setattr(dart, "corp_codes", lambda refresh=False: {})
    assert dart.fetch_cash_flow("999999").empty


def test_corp_codes_keep_only_listed_companies(monkeypatch, tmp_path):
    import io
    import zipfile

    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    xml = """<?xml version="1.0" encoding="utf-8"?><result>
      <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>
            <stock_code>005930</stock_code></list>
      <list><corp_code>00999999</corp_code><corp_name>비상장</corp_name>
            <stock_code> </stock_code></list>
    </result>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml.encode("utf-8"))

    class Response:
        content = buffer.getvalue()

    monkeypatch.setattr(dart, "_get", lambda path, **params: Response())
    codes = dart.corp_codes(refresh=True)
    assert codes == {"005930": "00126380"}


def test_corp_codes_are_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    calls = []

    monkeypatch.setattr(dart, "_download_corp_codes",
                        lambda: calls.append(1) or {"005930": "00126380"})
    dart.corp_codes(refresh=True)
    dart.corp_codes()
    assert len(calls) == 1


def test_get_refuses_without_a_key(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as err:
        dart._get("corpCode.xml")
    assert "DART_API_KEY" in str(err.value)


def test_krx_attaches_cash_flow_when_dart_is_available(monkeypatch):
    """네이버 재무에 DART 현금흐름만 덧붙인다."""
    import sys
    import types

    fdr = types.ModuleType("FinanceDataReader")
    fdr.StockListing = lambda key: pd.DataFrame()
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fdr)

    from chartfinder.datasource import naver_api
    from chartfinder.datasource.krx import KrxSource

    naver_frame = pd.DataFrame(
        {"revenue": [100.0, 120.0], "operating_cash_flow": [None, None]},
        index=[2024, 2025],
    )
    monkeypatch.setattr(naver_api, "fetch_fundamentals", lambda symbol: naver_frame.copy())
    monkeypatch.setattr(dart, "enabled", lambda: True)
    monkeypatch.setattr(
        dart, "fetch_cash_flow",
        lambda symbol, year=None: pd.DataFrame(
            {"operating_cash_flow": [7.0, 9.0]}, index=[2024, 2025]
        ),
    )

    result = KrxSource().fetch_fundamentals("005930")
    assert result.loc[2025, "operating_cash_flow"] == 9.0
    assert result.loc[2025, "revenue"] == 120.0


def test_krx_keeps_fundamentals_when_dart_fails(monkeypatch):
    """현금흐름 하나 때문에 나머지 재무를 버리면 안 된다."""
    import sys
    import types

    fdr = types.ModuleType("FinanceDataReader")
    fdr.StockListing = lambda key: pd.DataFrame()
    monkeypatch.setitem(sys.modules, "FinanceDataReader", fdr)

    from chartfinder.datasource import naver_api
    from chartfinder.datasource.krx import KrxSource

    monkeypatch.setattr(
        naver_api, "fetch_fundamentals",
        lambda symbol: pd.DataFrame(
            {"revenue": [100.0], "operating_cash_flow": [None]}, index=[2025]
        ),
    )
    monkeypatch.setattr(dart, "enabled", lambda: True)
    monkeypatch.setattr(
        dart, "fetch_cash_flow",
        lambda symbol, year=None: (_ for _ in ()).throw(ConnectionError("키 오류")),
    )

    result = KrxSource().fetch_fundamentals("005930")
    assert result.loc[2025, "revenue"] == 100.0
