"""매물대 — 가격대별 거래량.

'52주 신고가 위에 매물 없음' 과 '바닥에서 받쳐줌' 은 둘 다 매물대를
봐야 한다. 봉마다 거래량을 고가~저가 구간에 나눠 담아 계산한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from chartfinder import indicators as ind
from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx


def _bars(highs, lows, volumes=None) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(highs), freq="D")
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes,
         "volume": volumes if volumes is not None else [1000.0] * len(highs)},
        index=index,
    )


def _score(key: str, ctx: Ctx, **params) -> float:
    condition = get_condition(key)
    return condition.score(ctx, {p.name: p.default for p in condition.params} | params)


# ------------------------------------------------------------- 매물대 자체


def test_profile_conserves_the_total_volume():
    """물량이 새거나 중복되면 비중 계산이 전부 틀어진다."""
    df = _bars([100 + i for i in range(60)], [99 + i for i in range(60)])
    _, profile = ind.volume_profile(df, 60, 40)
    assert profile.sum() == pytest.approx(float(df["volume"].sum()))


def test_a_wide_bar_spreads_its_volume_in_proportion_to_the_overlap():
    """종가에만 몰아 담으면 하루에 크게 움직인 봉의 물량이 한 점에 쌓인다.

    100~200 을 걸친 봉(거래량 1,000)은 20칸(칸당 5원)에 50씩 들어가야 한다.
    """
    df = _bars([200.0, 101.0], [100.0, 100.0], [1000.0, 1000.0])
    edges, profile = ind.volume_profile(df, 2, 20)

    assert edges[0] == pytest.approx(100.0)
    assert edges[-1] == pytest.approx(200.0)
    # 넓은 봉은 모든 칸에 50씩. 좁은 봉(100~101)은 첫 칸에 1,000 전부
    assert profile[5] == pytest.approx(50.0)
    assert profile[-1] == pytest.approx(50.0)
    assert profile[0] == pytest.approx(1050.0)
    assert profile.sum() == pytest.approx(2000.0)


def test_profile_handles_a_limit_up_bar_where_high_equals_low():
    """상한가 봉은 고가=저가라 구간 폭이 0이다 — 0으로 나누면 안 된다."""
    df = _bars([100.0] * 10, [100.0] * 10)
    _, profile = ind.volume_profile(df, 10, 20)
    assert profile.sum() == pytest.approx(float(df["volume"].sum()))


def test_profile_is_empty_without_volume():
    df = _bars([100.0] * 10, [99.0] * 10, volumes=[0.0] * 10)
    edges, profile = ind.volume_profile(df, 10, 20)
    assert edges.size == 0 and profile.size == 0


# ------------------------------------------------------------- 위쪽 매물


def test_a_new_high_has_nothing_above_it():
    """신고가를 돌파하면 위에서 거래된 적이 없다."""
    rising = _bars([100 + i for i in range(60)], [99 + i for i in range(60)])
    assert ind.supply_above(rising, 60, 40) == pytest.approx(0.0, abs=2.0)


def test_a_stock_well_below_its_high_has_supply_above():
    highs = [100 + i for i in range(40)] + [120.0] * 20
    lows = [99 + i for i in range(40)] + [119.0] * 20
    fell = _bars(highs, lows)
    fell.iloc[-1, fell.columns.get_loc("close")] = 105.0

    above = ind.supply_above(fell, 60, 40)
    assert above > 50.0


def test_no_overhead_supply_condition_separates_the_two():
    rising = Ctx(_bars([100 + i for i in range(80)], [99 + i for i in range(80)]))
    highs = [100 + i for i in range(60)] + [140.0] * 20
    lows = [99 + i for i in range(60)] + [139.0] * 20
    fell_frame = _bars(highs, lows)
    fell_frame.iloc[-1, fell_frame.columns.get_loc("close")] = 110.0

    assert _score("no_overhead_supply", rising, period=80, max_pct=3.0) == 1.0
    assert _score("no_overhead_supply", Ctx(fell_frame), period=80, max_pct=3.0) < 0.05


# ------------------------------------------------------------- 아래쪽 받침


def test_support_below_finds_volume_stacked_under_the_price():
    """아래 구간에 거래가 몰려 있으면 받쳐주는 힘이 된다."""
    # 100 근처에서 오래 머문 뒤 110 으로 올라온 종목
    highs = [101.0] * 50 + [110.0] * 5
    lows = [99.0] * 50 + [108.0] * 5
    volumes = [5000.0] * 50 + [1000.0] * 5
    df = _bars(highs, lows, volumes)

    below = ind.support_below(df, 55, 40, depth=15.0)
    assert below > 70.0, f"아래 받침 {below}"


def test_support_below_is_small_when_the_stock_ran_far_from_its_base():
    # 100 에서 오래 머물다 300 까지 치솟은 종목 — 아래 15% 구간은 비어 있다
    highs = [101.0] * 50 + [300.0] * 5
    lows = [99.0] * 50 + [295.0] * 5
    df = _bars(highs, lows, [5000.0] * 50 + [1000.0] * 5)

    below = ind.support_below(df, 55, 40, depth=15.0)
    assert below < 20.0, f"아래 받침 {below}"


# ------------------------------------------------------------- 저점 높아짐


def test_higher_lows_detects_a_rising_floor():
    rising = Ctx(_bars([110 + i for i in range(90)], [100 + i for i in range(90)]))
    assert _score("higher_lows", rising, period=90, segments=3) == 1.0


def test_higher_lows_rejects_a_falling_floor():
    falling = Ctx(_bars([200 - i for i in range(90)], [190 - i for i in range(90)]))
    assert _score("higher_lows", falling, period=90, segments=3) == 0.0


def test_partially_rising_lows_give_a_partial_score():
    # 1구간 저점 100, 2구간 110(상승), 3구간 105(하락) → 2단계 중 1단계
    lows = [100.0] * 30 + [110.0] * 30 + [105.0] * 30
    highs = [v + 5 for v in lows]
    assert _score("higher_lows", Ctx(_bars(highs, lows)), period=90, segments=3) == pytest.approx(0.5)


def test_higher_lows_needs_enough_bars_per_segment():
    short = Ctx(_bars([100.0] * 45, [99.0] * 45))
    assert _score("higher_lows", short, period=120, segments=10) == 0.0


# ------------------------------------------------------------- 프리셋


@pytest.mark.parametrize("name, keys", [
    ("신고가 돌파 + 매물", {"no_overhead_supply", "breakout_high", "turnover_surge"}),
    ("바닥 받침", {"supply_support", "higher_lows", "above_ma"}),
])
def test_presets_load_and_target_the_korean_market(name, keys):
    from chartfinder import presets as presets_mod

    preset = next(p for _, p in presets_mod.load_all() if name in p.name)
    assert preset.market == "kr", "한국 데이터만 받는 사용자에게 빈 결과가 나간다"
    assert keys <= {spec.key for spec in preset.conditions}
    for spec in preset.conditions:
        get_condition(spec.key)


def test_the_two_presets_are_opposites_and_stay_separate():
    """매물 없는 신고가와 바닥 받침은 동시에 성립할 수 없다.

    한 전략에 섞으면 어느 종목이든 절반이 0점이 되어 순위가 죽는다.
    """
    from chartfinder import presets as presets_mod

    for _, preset in presets_mod.load_all():
        keys = {spec.key for spec in preset.conditions}
        assert not ({"no_overhead_supply"} <= keys and {"supply_support"} <= keys), preset.name
