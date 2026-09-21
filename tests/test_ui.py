"""데스크톱 UI 스모크 테스트.

tkinter 나 디스플레이가 없는 환경(서버/CI)에서는 건너뛴다.
"""

import os
import sys

import pytest

tk = pytest.importorskip("tkinter")

if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    pytest.skip("디스플레이 없음", allow_module_level=True)

from chartfinder.ui import App  # noqa: E402


@pytest.fixture
def app():
    window = App()
    window.update()
    yield window
    window.destroy()


def test_window_lists_every_condition(app):
    from chartfinder.conditions import all_conditions

    assert len(app.rows) == len(all_conditions())


def test_unchecked_conditions_produce_no_specs(app):
    assert app.selected_specs() == []


def test_checked_condition_becomes_spec_with_edited_params(app):
    row = next(r for r in app.rows if r.cond.key == "rsi_oversold")
    row.checked.set(True)
    row._toggle()
    row.params["threshold"].set("25")
    row.weight.set("2")

    spec = row.to_spec()
    assert spec.key == "rsi_oversold"
    assert spec.weight == 2.0
    # 문자열 입력은 조건 쪽에서 타입이 맞춰진다
    from chartfinder.conditions import get

    assert get("rsi_oversold").resolve(spec.params)["threshold"] == 25.0


def test_market_switch_updates_universes(app):
    app._on_market_change("데모(오프라인)")
    assert app.market.get() == "demo"
    assert app.universe.get() == "all"


def test_preset_load_checks_matching_rows(app):
    from chartfinder.presets import list_presets

    paths = list_presets("presets")
    if not paths:
        pytest.skip("프리셋 없음")
    app.preset.set(paths[0].name)
    app.on_load_preset()
    assert app.selected_specs()
