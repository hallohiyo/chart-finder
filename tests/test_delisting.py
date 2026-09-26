"""상장폐지·관리종목 요건 조건.

이 조건들은 '좋은 종목 고르기' 가 아니라 '탈락 위험 종목 빼기' 다.
부실기업을 만들어 실제로 걸러지는지 확인한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx
from chartfinder.fundamentals import FUNDAMENTAL_COLUMNS, normalize


def _company(**columns) -> Ctx:
    """연도별 재무를 가진 가상 회사. 안 준 항목은 넉넉한 값으로 채운다."""
    years = len(next(iter(columns.values())))
    index = list(range(2023 - years + 1, 2024))
    defaults = {
        "revenue": [5000.0] * years,
        "operating_income": [500.0] * years,
        "net_income": [400.0] * years,
        "debt_ratio": [50.0] * years,
        "roe": [15.0] * years,
        "reserve_ratio": [800.0] * years,
        "operating_cash_flow": [450.0] * years,
    }
    defaults.update(columns)
    bars = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=5, freq="D"),
    )
    return Ctx(bars, normalize(pd.DataFrame(defaults, index=index)))


def _score(key: str, ctx: Ctx, **params) -> float:
    condition = get_condition(key)
    merged = {p.name: p.default for p in condition.params} | params
    return condition.score(ctx, merged)


# ------------------------------------------------------------------ 유보율 스키마


def test_reserve_ratio_is_part_of_the_schema():
    assert "reserve_ratio" in FUNDAMENTAL_COLUMNS


def test_reserve_ratio_is_read_from_the_korean_label():
    """네이버는 '유보율' 이라는 이름으로 준다."""
    frame = normalize(pd.DataFrame({"유보율": [-45.0, 120.0]}, index=[2022, 2023]))
    assert list(frame["reserve_ratio"]) == [-45.0, 120.0]


# ------------------------------------------------------------------ 매출액 미달


def test_revenue_floor_rejects_a_shell_company():
    """코스피 50억·코스닥 30억 미만은 관리종목 사유다."""
    shell = _company(revenue=[12.0])       # 12억
    healthy = _company(revenue=[5000.0])   # 5,000억
    assert _score("revenue_floor", shell, min_revenue=100.0) < 0.05
    assert _score("revenue_floor", healthy, min_revenue=100.0) == 1.0


def test_revenue_floor_uses_the_worst_year_not_the_average():
    """한 해라도 미달이면 그 해에 지정된다. 평균으로 가려지면 안 된다."""
    dipped = _company(revenue=[5000.0, 5000.0, 20.0])
    assert _score("revenue_floor", dipped, min_revenue=100.0, years=3) < 0.1


# ------------------------------------------------------------------ 자본잠식


def test_capital_impairment_is_caught_by_a_negative_reserve_ratio():
    """유보율이 음수면 결손금이 자본금을 깎아먹은 상태다."""
    impaired = _company(reserve_ratio=[-60.0])
    # 잠식은 '기준에 가까운' 상태가 아니라 법적으로 구분되는 상태다 → 부분점수 없음
    assert _score("no_capital_impairment", impaired, min_reserve=100.0) == 0.0


def test_a_thin_reserve_scores_between_the_two():
    """잠식은 아니지만 얇은 회사는 중간 점수여야 한다 (0이나 1이 아니라)."""
    thin = _company(reserve_ratio=[60.0])
    score = _score("no_capital_impairment", thin, min_reserve=100.0)
    assert 0.0 < score < 1.0


def test_healthy_reserve_is_full_marks():
    assert _score("no_capital_impairment", _company(reserve_ratio=[900.0])) == 1.0


def test_capital_impairment_checks_every_year_asked():
    """최근 연도만 좋아도 직전에 잠식이 있었으면 걸러야 한다."""
    recovered = _company(reserve_ratio=[-30.0, 50.0, 400.0])
    assert _score("no_capital_impairment", recovered, min_reserve=100.0, years=3) == 0.0


# ------------------------------------------------------------------ 영업손실 연속


def test_consecutive_operating_losses_score_zero():
    """코스닥 4년 연속 영업손실은 관리종목이다."""
    losing = _company(operating_income=[-100.0, -80.0, -120.0])
    assert _score("no_operating_loss_streak", losing, years=3) == 0.0


def test_partial_losses_give_a_partial_score():
    mixed = _company(operating_income=[-100.0, 50.0, 80.0])
    assert _score("no_operating_loss_streak", mixed, years=3) == pytest.approx(2 / 3)


def test_all_profitable_years_are_full_marks():
    assert _score("no_operating_loss_streak", _company(operating_income=[10.0, 20.0, 30.0])) == 1.0


# ------------------------------------------------------------------ 데이터 없음


@pytest.mark.parametrize(
    "key", ["revenue_floor", "no_capital_impairment", "no_operating_loss_streak"]
)
def test_missing_fundamentals_score_zero(key):
    """재무를 안 받았으면 0점이어야 한다.

    '위험 없음' 으로 처리하면 데이터가 없는 종목이 안전한 종목으로 올라온다.
    """
    bars = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=5, freq="D"),
    )
    assert _score(key, Ctx(bars, None)) == 0.0


# ------------------------------------------------------------------ 프리셋


def test_delisting_preset_is_loadable_and_complete():
    from chartfinder import presets as presets_mod

    preset = next(
        preset for _, preset in presets_mod.load_all() if "상장폐지" in preset.name
    )
    assert preset.needs_fundamentals and preset.needs_profiles
    keys = {spec.key for spec in preset.conditions}
    # 숫자로 확인 가능한 요건 세 가지가 빠지면 안 된다
    assert {"revenue_floor", "no_capital_impairment", "no_operating_loss_streak"} <= keys
    for spec in preset.conditions:
        get_condition(spec.key)


def test_a_failing_company_ranks_below_a_healthy_one():
    """필터가 실제로 순위를 가르는지 — 부실기업이 아래로 가야 한다."""
    from chartfinder import presets as presets_mod
    from chartfinder.screener import combine

    preset = next(
        preset for _, preset in presets_mod.load_all() if "상장폐지" in preset.name
    )
    specs = [s for s in preset.conditions if get_condition(s.key).category == "재무"]

    bad = _company(revenue=[15.0, 15.0, 15.0], reserve_ratio=[-70.0] * 3,
                   operating_income=[-50.0] * 3, operating_cash_flow=[-40.0] * 3)
    good = _company(revenue=[5000.0] * 3, reserve_ratio=[900.0] * 3,
                    operating_income=[600.0] * 3, operating_cash_flow=[550.0] * 3)

    def total(ctx):
        scores = {s.key: get_condition(s.key).score(ctx, s.params) for s in specs}
        return combine(scores, specs)

    assert total(bad) < 0.2
    assert total(good) > 0.9
