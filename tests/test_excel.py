"""엑셀 저장 — 값은 숫자, 표시는 단위.

셀에 "1.2조" 같은 글자를 넣으면 읽기는 쉬워도 정렬·합계·필터가 깨진다.
그래서 값은 숫자로 두고 엑셀 표시 형식으로 단위를 붙인다.
"""

from __future__ import annotations

import pytest

from chartfinder import cache, excel
from chartfinder.datasource import get_source
from chartfinder.screener import ConditionSpec, screen

openpyxl = pytest.importorskip("openpyxl")


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()][:8]
    cache.update("demo", symbols=symbols, years=2, flows=True)
    cache.update_profiles("demo", symbols=symbols)

    result = screen(
        "demo",
        [ConditionSpec("turnover_value"), ConditionSpec("both_net_buy")],
        tickers=cache.get_tickers("demo", "all")[:8],
    )
    path = excel.save(result, tmp_path / "결과.xlsx")
    return openpyxl.load_workbook(path)["찾은종목"], result


def _cell(ws, label: str, row: int = 2):
    index = {str(c.value): c.column for c in ws[1]}[label]
    return ws.cell(row=row, column=index)


def test_values_stay_numeric_so_sorting_still_works(saved):
    ws, _ = saved
    for label in ("현재가", "거래대금 20일평균(억)", "시가총액(억)",
                  "외국인 순매수 5일(억)", "쌍끌이 일수 20일"):
        value = _cell(ws, label).value
        assert isinstance(value, (int, float)), f"{label} 이 글자로 저장됐다: {value!r}"


def test_units_come_from_the_display_format(saved):
    ws, _ = saved
    assert "억" in _cell(ws, "거래대금 20일평균(억)").number_format
    assert "억" in _cell(ws, "시가총액(억)").number_format
    assert "원" in _cell(ws, "현재가").number_format
    assert "일" in _cell(ws, "쌍끌이 일수 20일").number_format
    assert "주" in _cell(ws, "외국인 순매수 5일(주)").number_format
    assert _cell(ws, "적합도").number_format.endswith("%")


def test_net_buy_shows_its_sign(saved):
    """순매도를 순매수처럼 보이게 하면 안 된다."""
    ws, _ = saved
    fmt = _cell(ws, "외국인 순매수 5일(억)").number_format
    assert "-" in fmt and "+" in fmt


def test_summary_columns_switch_between_억_and_조():
    """정렬이 필요한 숫자 칸과 별도로, 읽기 쉬운 요약 칸을 둔다."""
    from chartfinder.display import _money

    assert _money(227_417) == "22.7조"
    assert _money(3_500) == "3,500억"
    assert _money(850) == "850억"


def test_summary_columns_are_in_the_output(saved):
    ws, result = saved
    assert "marcap_text" in result.columns
    assert "turnover_text" in result.columns
    # 요약 칸은 글자다 (정렬용 숫자 칸이 따로 있으므로 괜찮다)
    assert isinstance(_cell(ws, "시가총액").value, str)
    assert "조" in _cell(ws, "시가총액").value or "억" in _cell(ws, "시가총액").value


def test_header_is_frozen_and_filterable(saved):
    """종목이 많으면 머리글이 보여야 쓸 수 있다."""
    ws, _ = saved
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref is not None


def test_condition_scores_are_shown_as_percentages(saved):
    ws, _ = saved
    assert _cell(ws, "s_turnover_value").number_format == "0.0%"


def test_cli_writes_xlsx_when_the_extension_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from typer.testing import CliRunner

    from chartfinder.cli import app

    runner = CliRunner()
    assert runner.invoke(app, ["update", "-m", "demo", "--limit", "8"]).exit_code == 0

    out = tmp_path / "결과.xlsx"
    result = runner.invoke(
        app, ["scan", "-m", "demo", "-c", "above_ma", "--top", "3", "--csv", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    # 진짜 엑셀 파일인지 (zip 서명으로 확인)
    assert out.read_bytes()[:2] == b"PK"


def test_csv_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from typer.testing import CliRunner

    from chartfinder.cli import app

    runner = CliRunner()
    runner.invoke(app, ["update", "-m", "demo", "--limit", "8"])
    out = tmp_path / "결과.csv"
    runner.invoke(
        app, ["scan", "-m", "demo", "-c", "above_ma", "--top", "3", "--csv", str(out)]
    )
    assert out.exists()
    assert "종목코드" in out.read_text(encoding="utf-8-sig")
