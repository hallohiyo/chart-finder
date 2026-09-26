"""외국인·기관이 '많이 담는' 것을 재는 조건.

며칠 연속인지(기존)와 얼마나 많이인지(여기)는 다른 질문이다.
절대 주식 수는 종목 크기에 따라 의미가 달라지므로 비중·금액으로 본다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx


def _ctx(foreign: list[float], inst: list[float], close: float = 10_000.0,
         profile: dict | None = None) -> Ctx:
    rows = len(foreign)
    index = pd.date_range("2024-01-01", periods=rows, freq="D")
    return Ctx(
        pd.DataFrame(
            {"open": close, "high": close, "low": close, "close": close,
             "volume": 1_000_000.0, "foreign_net": foreign, "inst_net": inst},
            index=index,
        ),
        None,
        profile,
    )


def _score(key: str, ctx: Ctx, **params) -> float:
    condition = get_condition(key)
    return condition.score(ctx, {p.name: p.default for p in condition.params} | params)


# --------------------------------------------------------------- 매집 비중


def test_same_share_count_means_different_things_by_company_size():
    """10만주는 소형주엔 대량, 대형주엔 미미하다 — 그걸 구분해야 한다."""
    flows = [5_000.0] * 20                      # 20일간 10만주 순매수
    small = _ctx(flows, [0.0] * 20, profile={"shares": 2_000_000.0})    # 5%
    large = _ctx(flows, [0.0] * 20, profile={"shares": 500_000_000.0})  # 0.02%

    assert _score("net_buy_ratio", small, who="foreign", days=20, min_pct=1.0) == 1.0
    assert _score("net_buy_ratio", large, who="foreign", days=20, min_pct=1.0) < 0.05


def test_net_buy_ratio_counts_both_sides_together():
    both = _ctx([3_000.0] * 20, [3_000.0] * 20, profile={"shares": 2_000_000.0})
    # 외국인만 3%, 합치면 6%
    foreign_only = _score("net_buy_ratio", both, who="foreign", days=20, min_pct=5.0)
    combined = _score("net_buy_ratio", both, who="both", days=20, min_pct=5.0)
    assert foreign_only < combined
    assert combined == 1.0


def test_net_selling_scores_zero():
    dumping = _ctx([-5_000.0] * 20, [-5_000.0] * 20, profile={"shares": 2_000_000.0})
    assert _score("net_buy_ratio", dumping, days=20, min_pct=0.5) == 0.0


def test_net_buy_ratio_without_the_snapshot_is_zero():
    """상장주식수가 없으면 비중을 낼 수 없다."""
    ctx = _ctx([5_000.0] * 20, [5_000.0] * 20)
    assert _score("net_buy_ratio", ctx, days=20) == 0.0


# --------------------------------------------------------------- 순매수 금액


def test_net_buy_value_uses_money_not_shares():
    """같은 주식 수라도 주가가 다르면 들어온 돈이 다르다."""
    cheap = _ctx([10_000.0] * 20, [0.0] * 20, close=1_000.0)    # 2억
    pricey = _ctx([10_000.0] * 20, [0.0] * 20, close=200_000.0)  # 400억

    assert _score("net_buy_value", cheap, who="foreign", days=20, min_value=100.0) < 0.05
    assert _score("net_buy_value", pricey, who="foreign", days=20, min_value=100.0) == 1.0


# --------------------------------------------------------------- 쌍끌이


def test_both_sides_buying_together_beats_one_side_only():
    """한쪽이 사고 다른 쪽이 파는 것과 둘이 같이 담는 것은 다르다."""
    together = _ctx([100.0] * 10, [100.0] * 10)
    opposed = _ctx([100.0] * 10, [-100.0] * 10)   # 외국인 매수, 기관 매도

    assert _score("both_net_buy", together, days=5) == 1.0
    assert _score("both_net_buy", opposed, days=5) == 0.0


def test_partial_overlap_gives_a_partial_score():
    # 최근 4일 중 2일만 둘이 함께 순매수 (조건의 최소 봉 수를 채우려고 앞을 채운다)
    ctx = _ctx(
        [0.0] * 8 + [100.0, 100.0, 100.0, 100.0],
        [0.0] * 8 + [-100.0, -100.0, 100.0, 100.0],
    )
    assert _score("both_net_buy", ctx, days=4) == pytest.approx(0.5)


def test_both_net_buy_needs_both_columns():
    index = pd.date_range("2024-01-01", periods=10, freq="D")
    only_foreign = Ctx(
        pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
             "foreign_net": 100.0},
            index=index,
        )
    )
    assert _score("both_net_buy", only_foreign, days=5) == 0.0


# --------------------------------------------------------------- 매집 가속


def test_accelerating_accumulation_is_detected():
    # 앞은 하루 100주, 최근 5일은 하루 1,000주
    flows = [100.0] * 35 + [1_000.0] * 5
    ctx = _ctx(flows, [0.0] * 40)
    assert _score("net_buy_accelerating", ctx, who="foreign",
                  short=5, long=20, mult=2.0) == 1.0


def test_steady_accumulation_is_not_acceleration():
    steady = _ctx([500.0] * 40, [0.0] * 40)
    assert _score("net_buy_accelerating", steady, who="foreign",
                  short=5, long=20, mult=2.0) < 0.2


def test_acceleration_compares_daily_averages_not_totals():
    """기간이 다르므로 합계로 비교하면 항상 장기가 크게 나온다."""
    steady = _ctx([500.0] * 40, [0.0] * 40)
    # 하루평균이 같으므로 배수는 1.0 — 1.0 기준이면 만점이어야 한다
    assert _score("net_buy_accelerating", steady, who="foreign",
                  short=5, long=20, mult=1.0) == 1.0


def test_acceleration_needs_a_positive_baseline():
    """그동안 팔던 종목은 비교 기준이 없다."""
    was_selling = _ctx([-500.0] * 35 + [1_000.0] * 5, [0.0] * 40)
    assert _score("net_buy_accelerating", was_selling, who="foreign", short=5, long=20) == 0.0


def test_acceleration_rejects_a_short_window_longer_than_the_long_one():
    ctx = _ctx([500.0] * 40, [0.0] * 40)
    assert _score("net_buy_accelerating", ctx, short=20, long=20) == 0.0


# --------------------------------------------------------------- 프리셋


def test_accumulation_preset_is_complete():
    from chartfinder import presets as presets_mod

    preset = next(
        preset for _, preset in presets_mod.load_all() if "쌍끌이" in preset.name
    )
    assert preset.needs_flows and preset.needs_profiles
    keys = {spec.key for spec in preset.conditions}
    assert {"both_net_buy", "net_buy_ratio", "net_buy_value"} <= keys
    for spec in preset.conditions:
        get_condition(spec.key)
