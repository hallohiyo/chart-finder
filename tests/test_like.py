"""기준 종목 대비 검색.

"A 가 10점이면 8~9점짜리" 처럼 찾는다. 절대 점수로 자르면 조건 구성에
따라 기준이 달라지는데, 기준 종목을 잡으면 그 종목이 100인 눈금에서
상대적으로 고를 수 있다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder.screener import ReferenceNotFound, like_reference


def _result(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"symbol": s, "name": n, "score": v} for s, n, v in rows]
    )


def test_keeps_only_the_requested_band():
    result = _result([
        ("A", "가나다", 1.00),
        ("B", "나다라", 0.95),
        ("C", "다라마", 0.85),
        ("D", "라마바", 0.70),
        ("E", "마바사", 0.40),
    ])
    kept, base = like_reference(result, "A", low=80.0, high=100.0)

    assert base == 1.00
    assert list(kept["symbol"]) == ["A", "B", "C"]
    assert list(kept["ref_pct"]) == [100.0, 95.0, 85.0]


def test_the_reference_itself_is_a_hundred_percent():
    result = _result([("A", "가", 0.589), ("B", "나", 0.577)])
    kept, base = like_reference(result, "A", 80.0, 100.0)
    assert kept.loc[kept["symbol"] == "A", "ref_pct"].iloc[0] == 100.0
    assert base == pytest.approx(0.589)


def test_excludes_stocks_stronger_than_the_band_allows():
    """상한을 100 으로 두면 기준보다 센 종목은 빠진다."""
    result = _result([("A", "가", 0.50), ("B", "나", 0.90)])
    kept, _ = like_reference(result, "A", 80.0, 100.0)
    assert list(kept["symbol"]) == ["A"]

    # 상한을 올리면 들어온다
    kept, _ = like_reference(result, "A", 80.0, 200.0)
    assert set(kept["symbol"]) == {"A", "B"}


def test_the_reference_can_be_given_by_name():
    result = _result([("005930", "삼성전자", 0.8), ("000660", "SK하이닉스", 0.7)])
    kept, base = like_reference(result, "하이닉스", 50.0, 100.0)
    assert base == pytest.approx(0.7)


def test_a_missing_reference_is_an_error_not_a_silent_pass_through():
    """조용히 전체를 돌려주면 걸러진 줄 알게 된다."""
    result = _result([("A", "가", 0.8)])
    with pytest.raises(ReferenceNotFound) as caught:
        like_reference(result, "없는종목")
    assert "찾을 수 없습니다" in str(caught.value)


def test_error_message_has_no_escaped_newlines():
    """KeyError 를 쓰면 str() 이 인자를 repr 로 감싸 줄바꿈이 글자로 찍힌다."""
    result = _result([("A", "가", 0.8)])
    with pytest.raises(ReferenceNotFound) as caught:
        like_reference(result, "없는종목")
    assert "\\n" not in str(caught.value)
    assert "\n" in str(caught.value)


def test_a_zero_scoring_reference_is_rejected():
    """0 으로 나눌 수 없고, 0점짜리를 기준으로 삼는 것도 의미가 없다."""
    result = _result([("A", "가", 0.0), ("B", "나", 0.5)])
    with pytest.raises(ReferenceNotFound) as caught:
        like_reference(result, "A")
    assert "0" in str(caught.value)


def test_an_empty_result_is_rejected():
    with pytest.raises(ReferenceNotFound):
        like_reference(pd.DataFrame(columns=["symbol", "name", "score"]), "A")


def test_the_original_result_is_not_modified():
    """ref_pct 를 끼워 넣을 때 원본을 건드리면 다른 곳이 깨진다."""
    result = _result([("A", "가", 1.0), ("B", "나", 0.9)])
    before = list(result.columns)
    like_reference(result, "A", 80.0, 100.0)
    assert list(result.columns) == before


def test_cli_reports_the_reference_score(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from typer.testing import CliRunner

    from chartfinder.cli import app

    runner = CliRunner()
    runner.invoke(app, ["update", "-m", "demo", "--limit", "20"])
    tickers_out = runner.invoke(
        app, ["scan", "-m", "demo", "-c", "above_ma", "--top", "1"]
    )
    symbol = next(
        line.split("│")[2].strip()
        for line in tickers_out.output.splitlines()
        if "│ 1 │" in line
    )

    out = runner.invoke(
        app,
        ["scan", "-m", "demo", "-c", "above_ma", "-c", "volume_surge",
         "--like", symbol, "--band", "70,100", "--top", "5"],
    )
    assert out.exit_code == 0, out.output
    assert "기준 종목" in out.output
    assert "기준" in out.output


def test_cli_rejects_a_bad_band(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from typer.testing import CliRunner

    from chartfinder.cli import app

    runner = CliRunner()
    runner.invoke(app, ["update", "-m", "demo", "--limit", "10"])
    out = runner.invoke(
        app, ["scan", "-m", "demo", "-c", "above_ma", "--like", "DEMO000", "--band", "abc"]
    )
    assert out.exit_code == 1
    assert "--band" in out.output


def test_relative_strength_is_in_every_preset_but_the_delisting_check():
    """지수보다 강한지는 거의 모든 전략에서 봐야 한다.

    상장폐지 위험 회피는 탈락 요건 점검이라 상대강도가 의미 없다.
    """
    from chartfinder import presets as presets_mod

    for path, preset in presets_mod.load_all():
        keys = {spec.key for spec in preset.conditions}
        if path.name == "delisting_risk.yaml":
            continue
        assert "relative_strength" in keys, path.name
