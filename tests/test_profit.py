"""영업이익 성장 조건.

기존 `영업이익 증가` 는 '해마다 늘었는가' 만 본다 — 1% 늘어도 만점이다.
여기 조건들은 '얼마나' 늘었는지와 '수익성이 나아지는지' 를 본다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx
from chartfinder.fundamentals import normalize


def _company(**columns) -> Ctx:
    years = len(next(iter(columns.values())))
    index = list(range(2024 - years, 2024))
    frame = {"revenue": [1000.0] * years, "operating_margin": [10.0] * years}
    frame.update(columns)
    bars = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=5, freq="D"),
    )
    return Ctx(bars, normalize(pd.DataFrame(frame, index=index)))


def _score(key: str, ctx: Ctx, **params) -> float:
    condition = get_condition(key)
    return condition.score(ctx, {p.name: p.default for p in condition.params} | params)


# ------------------------------------------------------------- 성장률


def test_cagr_separates_fast_growth_from_barely_growing():
    """'해마다 늘었다' 로는 연 100% 와 연 1% 가 똑같이 만점이다."""
    fast = _company(operating_income=[100.0, 200.0, 400.0])    # 연 100%
    crawl = _company(operating_income=[100.0, 101.0, 102.0])   # 연 1%

    # 기존 조건은 둘을 구분하지 못한다
    assert _score("operating_income_growth", fast, years=3, min_growth=0.0) == 1.0
    assert _score("operating_income_growth", crawl, years=3, min_growth=0.0) == 1.0
    # 새 조건은 구분한다
    assert _score("growth_cagr", fast, years=3, min_cagr=20.0) == 1.0
    assert _score("growth_cagr", crawl, years=3, min_cagr=20.0) < 0.05


def test_cagr_refuses_to_measure_growth_from_a_loss():
    """적자에서 출발하면 복합 성장률이 정의되지 않는다.

    음수로 나누면 부호가 뒤집혀 적자 확대가 고성장처럼 보인다.
    """
    turnaround = _company(operating_income=[-50.0, 50.0, 200.0])
    worsening = _company(operating_income=[-50.0, -100.0, -200.0])

    assert _score("growth_cagr", turnaround, years=3) == 0.0
    assert _score("growth_cagr", worsening, years=3) == 0.0


def test_cagr_is_zero_when_the_latest_year_is_a_loss():
    fell_into_loss = _company(operating_income=[100.0, 50.0, -20.0])
    assert _score("growth_cagr", fell_into_loss, years=3) == 0.0


# ------------------------------------------------------------- 급증


def test_surge_catches_a_turnaround_from_loss_to_profit():
    """적자 탈출은 성장률로 표현하기 어렵다 — 분모에 절대값을 쓴다."""
    turnaround = _company(operating_income=[-100.0, 50.0])   # -100 → +50 = +150%
    assert _score("growth_surge", turnaround, min_growth=50.0) == 1.0


def test_surge_rejects_a_halving():
    halved = _company(operating_income=[100.0, 50.0])
    assert _score("growth_surge", halved, min_growth=50.0) < 0.01


def test_surge_looks_only_at_the_latest_year():
    """3년 전 급증은 지금의 가속이 아니다."""
    old_surge = _company(operating_income=[100.0, 300.0, 310.0])
    assert _score("growth_surge", old_surge, min_growth=50.0) < 0.05


# ------------------------------------------------------------- 마진 개선


def test_margin_improving_needs_an_actual_rise():
    """보합을 '개선' 으로 세면 이름과 맞지 않는다."""
    rising = _company(operating_margin=[5.0, 10.0, 15.0], operating_income=[100.0] * 3)
    flat = _company(operating_margin=[10.0, 10.0, 10.0], operating_income=[100.0] * 3)
    falling = _company(operating_margin=[15.0, 10.0, 5.0], operating_income=[100.0] * 3)

    assert _score("operating_margin_improving", rising, years=3) == 1.0
    assert _score("operating_margin_improving", flat, years=3) < 0.05
    assert _score("operating_margin_improving", falling, years=3) < 0.05


def test_partial_improvement_gives_a_partial_score():
    mixed = _company(operating_margin=[5.0, 12.0, 11.0], operating_income=[100.0] * 3)
    score = _score("operating_margin_improving", mixed, years=3, min_step=1.0)
    assert 0.0 < score < 1.0


# ------------------------------------------------------------- 데이터 없음


@pytest.mark.parametrize(
    "key", ["growth_cagr", "growth_surge", "operating_margin_improving"]
)
def test_no_fundamentals_means_zero(key):
    bars = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=5, freq="D"),
    )
    assert _score(key, Ctx(bars, None)) == 0.0


def test_a_single_year_is_not_enough_to_measure_growth(key="growth_cagr"):
    one_year = _company(operating_income=[100.0])
    assert _score(key, one_year) == 0.0
    assert _score("growth_surge", one_year) == 0.0


# ------------------------------------------------------------- 프리셋


def test_profit_growth_preset_is_complete():
    from chartfinder import presets as presets_mod

    preset = next(p for _, p in presets_mod.load_all() if "영업이익 급성장" in p.name)
    keys = {spec.key for spec in preset.conditions}
    assert {"growth_cagr", "growth_surge",
            "operating_margin", "operating_margin_improving"} <= keys
    # 비용만 줄여 만든 이익을 걸러내려면 매출 증가도 봐야 한다
    assert "revenue_growth" in keys
    # 장부상 이익이 현금으로 들어오는지
    assert "positive_cash_flow" in keys
    for spec in preset.conditions:
        get_condition(spec.key)
