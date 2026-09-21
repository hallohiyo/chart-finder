import numpy as np
import pytest

from chartfinder.conditions import Ctx, all_conditions, get
from tests.conftest import make_df


def test_every_condition_runs_on_normal_data_and_returns_unit_range(uptrend):
    ctx = Ctx(uptrend)
    for cond in all_conditions():
        score = cond.score(ctx)
        assert 0.0 <= score <= 1.0, f"{cond.key} -> {score}"


def test_every_condition_survives_short_history():
    """봉이 3개뿐이어도 예외 없이 0점을 돌려줘야 한다."""
    ctx = Ctx(make_df([100.0, 101.0, 102.0]))
    for cond in all_conditions():
        assert 0.0 <= cond.score(ctx) <= 1.0


def test_ma_alignment_high_for_uptrend_low_for_downtrend(uptrend, downtrend):
    assert get("ma_alignment").score(Ctx(uptrend)) == 1.0
    assert get("ma_alignment").score(Ctx(downtrend)) < 0.3


def test_rsi_oversold_scores_downtrend(uptrend, downtrend):
    assert get("rsi_oversold").score(Ctx(downtrend)) > 0.9
    assert get("rsi_oversold").score(Ctx(uptrend)) < 0.01


def test_rsi_oversold_gives_partial_credit_near_threshold(flat):
    """RSI가 기준을 살짝 넘긴 종목도 점수가 남아야 한다 (이 도구의 핵심)."""
    from chartfinder import indicators as ind

    cond = get("rsi_oversold")
    actual = float(ind.rsi(flat["close"]).iloc[-1])
    # 기준을 조금 넘긴 경우 부분점수, 크게 넘긴 경우 사실상 0점
    near = cond.score(Ctx(flat), {"threshold": actual - 3})
    far = cond.score(Ctx(flat), {"threshold": actual - 40})
    assert 0.0 < near < 1.0
    assert far < near
    assert cond.score(Ctx(flat), {"threshold": actual + 1}) == 1.0


def test_volume_surge_detects_spike():
    closes = [100.0] * 60
    volumes = [1_000_000.0] * 59 + [4_000_000.0]
    assert get("volume_surge").score(Ctx(make_df(closes, volumes)), {"ratio": 3}) == 1.0


def test_near_high_full_score_at_new_high(uptrend, downtrend):
    assert get("near_high").score(Ctx(uptrend)) == 1.0
    assert get("near_high").score(Ctx(downtrend)) < 0.5


def test_box_range_prefers_flat_chart(flat):
    steep = make_df(100 * np.exp(np.linspace(0, 1.2, 120)))  # 60일 동안 크게 상승
    assert get("box_range").score(Ctx(flat)) == 1.0
    assert get("box_range").score(Ctx(steep)) < 0.5


def test_unknown_param_is_rejected(uptrend):
    with pytest.raises(ValueError):
        get("rsi_oversold").resolve({"nope": 1})


def test_params_are_cast_from_strings():
    resolved = get("rsi_oversold").resolve({"period": "9", "threshold": "35"})
    assert resolved == {"period": 9, "threshold": 35.0}


def test_condition_keys_are_unique():
    keys = [c.key for c in all_conditions()]
    assert len(keys) == len(set(keys))
