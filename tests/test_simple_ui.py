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
    assert app.chosen.get()  # 기본 선택이 있어야 바로 누를 수 있다


def test_presets_are_listed_easiest_first(app):
    orders = [preset.order for preset in app.presets.values()]
    assert orders == sorted(orders)


def test_preset_names_avoid_jargon(app):
    """초보자 화면이므로 지표 이름이 그대로 노출되면 안 된다."""
    jargon = ("RSI", "MACD", "DMI", "볼린저", "스토캐스틱", "이동평균")
    for preset in app.presets.values():
        assert not any(word in preset.name for word in jargon), preset.name


def test_market_switch_updates_the_data_notice(app):
    app.market.set("demo")
    app._refresh_data_status()
    assert "종목" in app.data_status.cget("text") or "데이터가 없습니다" in app.data_status.cget("text")


def test_missing_data_is_detected_for_the_chosen_strategy(app, monkeypatch):
    from chartfinder import cache

    monkeypatch.setattr(cache, "stats", lambda market: {"symbols": 0, "latest": None, "size_mb": 0})
    preset = next(iter(app.presets.values()))
    assert "시세" in app._missing_data(preset)


def test_flow_and_fundamental_needs_are_reported(app, monkeypatch):
    from chartfinder import cache
    from chartfinder.presets import Preset

    monkeypatch.setattr(cache, "stats", lambda market: {"symbols": 10, "latest": None, "size_mb": 1})
    monkeypatch.setattr(cache, "has_flows", lambda market, sample=5: False)
    monkeypatch.setattr(cache, "has_fundamentals", lambda market: False)

    needs_both = Preset(name="x", requires=["flows", "fundamentals"])
    missing = app._missing_data(needs_both)
    assert "외국인·기관" in missing
    assert "실적" in missing


def test_result_table_has_no_condition_columns(app):
    """조건 점수를 열로 보여주면 초보자에게는 잡음이다."""
    assert app.tree["columns"] == ("순위", "종목명", "종목코드", "현재가", "등락", "적합도")
