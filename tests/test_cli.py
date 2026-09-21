"""CLI 스모크 테스트 — 출력이 깨지거나 예외가 나지 않는지."""

import pytest
from typer.testing import CliRunner

from chartfinder.cli import app

runner = CliRunner()


@pytest.fixture
def demo_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    result = runner.invoke(app, ["update", "-m", "demo", "--limit", "8", "-y", "1"])
    assert result.exit_code == 0, result.output
    return tmp_path


def test_conditions_lists_every_category():
    result = runner.invoke(app, ["conditions"])
    assert result.exit_code == 0
    for category in ("추세", "모멘텀", "수급", "재무"):
        assert category in result.output


def test_condition_detail_shows_parameters():
    result = runner.invoke(app, ["conditions", "-k", "rsi_oversold"])
    assert result.exit_code == 0
    assert "threshold" in result.output


def test_unknown_condition_fails_cleanly():
    result = runner.invoke(app, ["conditions", "-k", "없는조건"])
    assert result.exit_code != 0


def _header_columns(output: str) -> int:
    """결과 표 머리글의 열 개수. 라벨은 폭에 따라 잘리므로 구조로 센다."""
    # "60종목 · 조건 ..." 같은 요약 줄이 아니라 표 머리글 줄을 찾는다
    header = next(line for line in output.splitlines() if "종목" in line and "┃" in line)
    return header.count("┃") - 1


def test_scan_renders_inline_scores_for_few_conditions(demo_home):
    result = runner.invoke(
        app, ["scan", "-m", "demo", "-c", "above_ma", "-c", "rsi_oversold",
              "--top", "3", "--detail"]
    )
    assert result.exit_code == 0, result.output
    # 기본 7열 + 조건 2열
    assert _header_columns(result.output) == 9


def test_scan_switches_to_breakdown_for_many_conditions(demo_home):
    """조건이 많으면 표에 욱여넣지 말고 종목별 내역으로 보여준다."""
    result = runner.invoke(
        app, ["scan", "-p", "presets/bottom_reversal_noflow.yaml",
              "-m", "demo", "-u", "all", "--top", "2", "--detail"]
    )
    assert result.exit_code == 0, result.output
    assert "충족" in result.output
    assert "근접" in result.output or "미달" in result.output
    # 표에는 기본 7열만 남아야 한다 (17개를 밀어넣으면 전부 뭉개진다)
    assert _header_columns(result.output) == 7


def test_scan_without_conditions_fails(demo_home):
    result = runner.invoke(app, ["scan", "-m", "demo"])
    assert result.exit_code == 1
    assert "조건이 없습니다" in result.output


def test_scan_rejects_unknown_universe(demo_home):
    result = runner.invoke(app, ["scan", "-m", "demo", "-u", "sp500", "-c", "above_ma"])
    assert result.exit_code == 1
    assert "없는 유니버스" in result.output


def test_scan_writes_csv(demo_home, tmp_path):
    out = tmp_path / "out" / "result.csv"
    result = runner.invoke(
        app, ["scan", "-m", "demo", "-c", "above_ma", "--top", "3", "--csv", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "symbol" in out.read_text(encoding="utf-8-sig")


def test_status_reports_cache(demo_home):
    result = runner.invoke(app, ["status", "-m", "demo"])
    assert result.exit_code == 0
    assert "종목 수" in result.output


def test_presets_are_listed():
    result = runner.invoke(app, ["presets"])
    assert result.exit_code == 0
    assert "바닥권 반등" in result.output


def test_doctor_runs_on_demo_market(demo_home):
    result = runner.invoke(app, ["doctor", "-m", "demo"])
    assert result.exit_code == 0, result.output
    assert "종목 목록" in result.output
    assert "시세 조회" in result.output
