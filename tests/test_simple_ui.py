"""초보자용 화면 스모크 테스트 (tkinter·디스플레이 없으면 건너뛴다)."""

import os
import sys

import pytest

tk = pytest.importorskip("tkinter")

if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    pytest.skip("디스플레이 없음", allow_module_level=True)

from chartfinder.simple_ui import App  # noqa: E402


@pytest.fixture
def app():
    window = App()
    window.update()
    yield window
    window.destroy()


def test_every_preset_is_offered_as_a_choice(app):
    from chartfinder.presets import list_presets

    assert len(app.presets) == len(list_presets("presets"))
    assert app.selected  # 기본으로 하나는 체크돼 있어야 바로 누를 수 있다


def test_presets_are_listed_easiest_first(app):
    orders = [preset.order for preset in app.presets.values()]
    assert orders == sorted(orders)


def test_every_preset_explains_itself(app):
    """이름만으로 안 되면 설명과 지표 갈래로 보완한다."""
    from chartfinder.presets import indicator_tags

    for preset in app.presets.values():
        assert preset.description.strip(), preset.name
        assert indicator_tags(preset), preset.name


def test_strategies_can_be_combined(app):
    """여러 전략을 고르면 조건이 합쳐진다."""
    names = list(app.checked)
    for name in names:
        app.checked[name].set(False)

    app.checked[names[0]].set(True)
    single = len(app.conditions)

    app.checked[names[1]].set(True)
    combined = len(app.conditions)

    assert len(app.selected) == 2
    assert combined > single


def test_overlapping_conditions_are_merged_once_with_added_weight(app):
    """같은 조건이 두 전략에 있으면 하나로 합치고 가중치를 더한다."""
    from chartfinder.presets import Preset, merge
    from chartfinder.screener import ConditionSpec

    first = Preset(name="a", conditions=[ConditionSpec("rsi_oversold", {}, 1.0)])
    second = Preset(name="b", conditions=[ConditionSpec("rsi_oversold", {}, 1.5),
                                          ConditionSpec("above_ma", {}, 1.0)])
    merged = merge([first, second])
    assert len(merged) == 2
    assert {spec.key: spec.weight for spec in merged}["rsi_oversold"] == 2.5


def test_choosing_nothing_is_reported(app):
    for var in app.checked.values():
        var.set(False)
    app._refresh_choice()
    assert "하나 이상" in app.choice_summary.cget("text")
    assert app.conditions == []


def test_market_switch_updates_the_data_notice(app):
    app.market.set("demo")
    app._refresh_data_status()
    assert "종목" in app.data_status.cget("text") or "데이터가 없습니다" in app.data_status.cget("text")


def test_missing_data_is_detected_for_the_chosen_strategy(app, monkeypatch):
    from chartfinder import cache

    monkeypatch.setattr(cache, "stats", lambda market: {"symbols": 0, "latest": None, "size_mb": 0})
    assert "시세" in app._missing_data(app.selected)


def test_flow_and_fundamental_needs_are_reported(app, monkeypatch):
    from chartfinder import cache
    from chartfinder.presets import Preset

    monkeypatch.setattr(cache, "stats", lambda market: {"symbols": 10, "latest": None, "size_mb": 1})
    monkeypatch.setattr(cache, "has_flows", lambda market, sample=5: False)
    monkeypatch.setattr(cache, "has_fundamentals", lambda market: False)

    needs_flows = Preset(name="x", requires=["flows"])
    needs_fundamentals = Preset(name="y", requires=["fundamentals"])
    missing = app._missing_data([needs_flows, needs_fundamentals])
    assert "외국인·기관" in missing
    assert "실적" in missing


def test_result_table_has_no_condition_columns(app):
    """조건 점수를 열로 보여주면 초보자에게는 잡음이다."""
    assert app.tree["columns"] == ("순위", "종목명", "종목코드", "현재가", "등락", "적합도")


# --------------------------------------------------------------------------- 진행 표시


def test_duration_is_human_readable():
    from chartfinder.simple_ui import _duration

    assert _duration(9) == "9초"
    assert _duration(75) == "1분 15초"
    assert _duration(3700) == "1시간 1분"
    assert _duration(-5) == "0초"


def test_progress_shows_count_and_percent(app):
    app.started_at = __import__("time").monotonic()
    app._show_progress(879, 2678, "시세 받는 중")

    assert "879 / 2,678" in app.status.get()
    assert "(33%)" in app.status.get()
    assert app.progress["value"] == 879
    assert app.progress["maximum"] == 2678


def test_remaining_time_appears_only_after_a_few_items(app):
    import time as _time

    app.started_at = _time.monotonic() - 10
    assert "남은 시간" not in app._timing_text(2, 1000)   # 표본이 적으면 추정하지 않는다
    assert "남은 시간" in app._timing_text(100, 1000)
    assert "남은 시간" not in app._timing_text(1000, 1000)  # 다 끝났으면 필요 없다


def test_cancel_stops_the_next_progress_report(app):
    """취소는 다음 진행 보고 시점에 수집 루프를 빠져나오게 한다."""
    from chartfinder.simple_ui import Cancelled

    app.busy = True
    app.on_cancel()
    assert app.cancelled

    with pytest.raises(Cancelled):
        app.report(10, 100, "시세 받는 중")


def test_cancel_button_is_hidden_while_idle(app):
    assert not app.cancel_button.winfo_ismapped()
