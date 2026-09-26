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


# --------------------------------------------------------------- 지분·잠재 물량


class _Payload:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_major_holder_prefers_the_total_row():
    rows = [
        {"nm": "이재용", "trmend_posesn_stock_qota_rt": "1.63"},
        {"nm": "삼성물산", "trmend_posesn_stock_qota_rt": "5.01"},
        {"nm": "계", "trmend_posesn_stock_qota_rt": "21.15"},
    ]
    assert dart._major_pct_from_rows(rows) == 21.15


def test_major_holder_sums_when_there_is_no_total_row():
    rows = [
        {"nm": "김대표", "trmend_posesn_stock_qota_rt": "30.0"},
        {"nm": "배우자", "trmend_posesn_stock_qota_rt": "4.5"},
        {"nm": "미기재", "trmend_posesn_stock_qota_rt": "-"},
    ]
    assert dart._major_pct_from_rows(rows) == 34.5


def test_major_holder_is_none_when_nothing_is_readable():
    assert dart._major_pct_from_rows([{"nm": "계", "trmend_posesn_stock_qota_rt": "-"}]) is None


def test_dilution_counts_bonds_and_rights_offerings(monkeypatch):
    """전환사채·신주인수권부사채·유상증자를 모두 합친다."""
    payloads = {
        # 전환사채 100억, 전환가액 10,000원 → 100만주
        "cvbdIsDecsn.json": {
            "status": "000",
            "list": [{"bd_fta": "10,000,000,000", "cv_prc": "10,000"}],
        },
        # 신주인수권부사채 50억, 행사가액 5,000원 → 100만주
        "bdwtIsDecsn.json": {
            "status": "000",
            "list": [{"bd_fta": "5,000,000,000", "ex_prc": "5,000"}],
        },
        # 유상증자는 신주 수가 그대로 있다 → 보통주 200만주 + 기타 50만주
        "piicDecsn.json": {
            "status": "000",
            "list": [{"nstk_ostk_cnt": "2,000,000", "nstk_estk_cnt": "500,000"}],
        },
    }
    monkeypatch.setattr(dart, "_get", lambda path, **params: _Payload(payloads[path]))
    monkeypatch.setattr(dart, "corp_codes", lambda refresh=False: {"005930": "00126380"})

    assert dart.fetch_dilution_shares("005930") == 4_500_000


def test_dilution_is_zero_when_there_are_no_filings(monkeypatch):
    """공시가 없는 것과 못 받은 것은 다르다 — 없으면 0.0, 못 받으면 None."""
    monkeypatch.setattr(
        dart, "_get", lambda path, **params: _Payload({"status": "013", "list": []})
    )
    monkeypatch.setattr(dart, "corp_codes", lambda refresh=False: {"005930": "00126380"})

    assert dart.fetch_dilution_shares("005930") == 0.0


def test_dilution_is_none_when_every_call_fails(monkeypatch):
    def boom(path, **params):
        raise RuntimeError("차단")

    monkeypatch.setattr(dart, "_get", boom)
    monkeypatch.setattr(dart, "corp_codes", lambda refresh=False: {"005930": "00126380"})

    assert dart.fetch_dilution_shares("005930") is None


def test_dilution_skips_rows_without_a_conversion_price(monkeypatch):
    """전환가액 미정 공시는 주식 수를 낼 수 없으므로 세지 않는다."""
    payloads = {
        "cvbdIsDecsn.json": {
            "status": "000",
            "list": [
                {"bd_fta": "10,000,000,000", "cv_prc": "-"},
                {"bd_fta": "2,000,000,000", "cv_prc": "2,000"},
            ],
        },
        "bdwtIsDecsn.json": {"status": "013", "list": []},
        "piicDecsn.json": {"status": "013", "list": []},
    }
    monkeypatch.setattr(dart, "_get", lambda path, **params: _Payload(payloads[path]))
    monkeypatch.setattr(dart, "corp_codes", lambda refresh=False: {"005930": "00126380"})

    assert dart.fetch_dilution_shares("005930") == 1_000_000
