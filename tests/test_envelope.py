"""엔벨로프·이격도·갭 이평선 돌파 (차트박사 세팅).

엔벨로프는 볼린저와 달리 표준편차가 아니라 고정 비율이라, 분할 매수
가격대를 미리 정해 둘 수 있다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder import indicators as ind
from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx


def _bars(closes: list[float], opens: list[float] | None = None) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    opens = opens if opens is not None else closes
    return pd.DataFrame(
        {"open": opens, "high": [max(o, c) for o, c in zip(opens, closes)],
         "low": [min(o, c) for o, c in zip(opens, closes)],
         "close": closes, "volume": 1_000_000.0},
        index=index,
    )


def _score(key: str, ctx: Ctx, **params) -> float:
    condition = get_condition(key)
    return condition.score(ctx, {p.name: p.default for p in condition.params} | params)


# ------------------------------------------------------------------ 지표


def test_envelope_bands_are_a_fixed_percentage_of_the_average():
    """볼린저와 달리 변동성에 따라 폭이 변하지 않는다."""
    close = pd.Series([10_000.0] * 30)
    center, upper, lower = ind.envelope(close, period=20, pct=20.0)

    assert center.iloc[-1] == pytest.approx(10_000.0)
    assert upper.iloc[-1] == pytest.approx(12_000.0)
    assert lower.iloc[-1] == pytest.approx(8_000.0)


def test_envelope_position_maps_the_band_to_zero_to_hundred():
    # 마지막 봉이 들어오면 20일선 자체가 내려가므로 정확히 0 은 아니다
    close = pd.Series([10_000.0] * 29 + [8_000.0])
    assert ind.envelope_position(close, 20, 20.0).iloc[-1] == pytest.approx(0.0, abs=4.0)

    close = pd.Series([10_000.0] * 29 + [10_000.0])  # 중심선
    assert ind.envelope_position(close, 20, 20.0).iloc[-1] == pytest.approx(50.0, abs=1.0)


def test_envelope_position_goes_below_zero_when_the_band_breaks():
    """하단 이탈은 하단 터치보다 더 내려간 자리다 — 잘라내면 구분이 없어진다."""
    close = pd.Series([10_000.0] * 29 + [6_000.0])
    assert ind.envelope_position(close, 20, 20.0).iloc[-1] < 0


def test_disparity_is_a_hundred_at_the_moving_average():
    close = pd.Series([10_000.0] * 30)
    assert ind.disparity(close, 20).iloc[-1] == pytest.approx(100.0)

    close = pd.Series([10_000.0] * 29 + [8_500.0])
    assert ind.disparity(close, 20).iloc[-1] < 90.0


# ------------------------------------------------------------------ 조건


def test_envelope_lower_fires_near_the_bottom_band():
    at_bottom = Ctx(_bars([10_000.0] * 39 + [8_200.0]))
    at_middle = Ctx(_bars([10_000.0] * 40))

    assert _score("envelope_lower", at_bottom, period=20, pct=20.0, max_position=15.0) == 1.0
    assert _score("envelope_lower", at_middle, period=20, pct=20.0, max_position=15.0) < 0.05


def test_envelope_zone_can_express_buy_and_sell_areas_with_one_condition():
    low = Ctx(_bars([10_000.0] * 39 + [8_300.0]))
    high = Ctx(_bars([10_000.0] * 39 + [11_700.0]))

    # 분할 매수 자리
    assert _score("envelope_zone", low, period=20, pct=20.0, low=0.0, high=25.0) == 1.0
    assert _score("envelope_zone", high, period=20, pct=20.0, low=0.0, high=25.0) < 0.05
    # 같은 조건, 구간만 반대 — 분할 매도 자리
    assert _score("envelope_zone", high, period=20, pct=20.0, low=75.0, high=100.0) == 1.0


def test_disparity_range_rejects_both_collapse_and_overheating():
    collapsed = Ctx(_bars([10_000.0] * 39 + [6_000.0]))
    overheated = Ctx(_bars([10_000.0] * 39 + [14_000.0]))
    mild = Ctx(_bars([10_000.0] * 39 + [9_300.0]))

    assert _score("disparity_range", mild, period=20, low=85.0, high=97.0) == 1.0
    assert _score("disparity_range", collapsed, period=20, low=85.0, high=97.0) < 0.05
    assert _score("disparity_range", overheated, period=20, low=85.0, high=97.0) < 0.05


# ------------------------------------------------------------------ 갭 돌파


def test_gap_over_ma_needs_a_gap_not_a_gradual_climb():
    """슬금슬금 올라 이평선을 넘은 것과 갭으로 뛰어넘은 것은 다르다."""
    # 이평선 아래에서 횡보하다가 시가를 크게 띄워 이평선 위에서 시작
    closes = [10_000.0] * 30 + [9_300.0] * 9 + [10_400.0]
    opens = closes[:-1] + [10_350.0]   # 전일 종가 9,300 → 시가 10,350 (갭 +11%)
    gapped = Ctx(_bars(closes, opens))

    # 같은 종착점이지만 갭 없이 서서히 오른 경우.
    # 실제 봉처럼 시가를 전일 종가로 둔다 — 시가=종가로 두면 오르는 날마다
    # 갭으로 잡혀 시험이 무의미해진다.
    climb = [10_000.0] * 30 + [9_300.0] * 6 + [9_600.0, 9_900.0, 10_200.0, 10_400.0]
    gradual = Ctx(_bars(climb, [climb[0]] + climb[:-1]))

    assert _score("gap_over_ma", gapped, period=20, min_gap=1.0, within=3) == 1.0
    assert _score("gap_over_ma", gradual, period=20, min_gap=1.0, within=3) == 0.0


# ------------------------------------------------------------------ 중복 조건


def test_the_same_condition_twice_keeps_two_separate_scores():
    """20일선과 60일선을 같은 조건으로 두 번 쓰는 것은 정당하다.

    이름을 키만으로 지으면 뒤쪽이 앞쪽을 덮어써 두 조건이 같은 점수를
    받고 가중합이 부풀려진다.
    """
    from chartfinder.screener import ConditionSpec, combine, score_labels, score_one

    specs = [
        ConditionSpec("gap_over_ma", {"period": 20, "min_gap": 1.0, "within": 3}),
        ConditionSpec("gap_over_ma", {"period": 60, "min_gap": 1.0, "within": 5}),
    ]
    assert score_labels(specs) == ["gap_over_ma#1", "gap_over_ma#2"]

    closes = [10_000.0] * 70 + [9_300.0] * 9 + [10_400.0]
    opens = [closes[0]] + closes[:-2] + [10_350.0]
    scores = score_one(_bars(closes, opens), specs)
    assert len(scores) == 2

    # 두 점수가 다르면 덮어쓰기가 일어나지 않은 것이다
    weighted = combine(scores, specs)
    assert 0.0 <= weighted <= 1.0


def test_labels_stay_plain_when_there_is_no_duplicate():
    from chartfinder.screener import ConditionSpec, score_labels

    specs = [ConditionSpec("above_ma"), ConditionSpec("volume_surge")]
    assert score_labels(specs) == ["above_ma", "volume_surge"]


# ------------------------------------------------------------------ 프리셋


@pytest.mark.parametrize("name, keys", [
    ("엔벨로프", {"envelope_lower", "disparity_range", "above_ma"}),
    ("갭 이평선", {"gap_over_ma", "turnover_surge", "disparity_range"}),
])
def test_chartdoctor_presets_load(name, keys):
    from chartfinder import presets as presets_mod

    preset = next(p for _, p in presets_mod.load_all() if name in p.name)
    assert keys <= {spec.key for spec in preset.conditions}
    for spec in preset.conditions:
        get_condition(spec.key)


def test_envelope_preset_does_not_use_the_unreachable_twenty_percent():
    """20% 엔벨로프는 실측에서 60종목 중 하나도 하단에 닿지 않았다."""
    from chartfinder import presets as presets_mod

    preset = next(p for _, p in presets_mod.load_all() if "엔벨로프" in p.name)
    spec = next(s for s in preset.conditions if s.key == "envelope_lower")
    assert spec.params["pct"] <= 15.0
