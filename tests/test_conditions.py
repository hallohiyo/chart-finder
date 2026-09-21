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


def test_rsi_cross_up_fires_after_crossing_threshold():
    """과매도에서 빠져나오며 30선을 넘은 직후여야 점수가 높다."""
    down = list(100 * np.exp(np.linspace(0, -0.35, 120)))
    rebound = list(down[-1] * np.exp(np.linspace(0, 0.12, 12)))
    cond = get("rsi_cross_up")
    assert cond.score(Ctx(make_df(down + rebound))) > 0.5
    assert cond.score(Ctx(make_df(down))) == 0.0  # 아직 안 넘음


def test_bb_lower_recovery_needs_both_break_and_return():
    """하단을 이탈한 채로 있으면 '복귀'가 아니고, 밴드 안으로 돌아와야 점수가 난다."""
    cond = get("bb_lower_recovery")
    # 조용히 횡보하다 급락해야 밴드를 실제로 이탈한다 (완만한 하락은 밴드를 못 뚫는다)
    calm = [100.0] * 60
    crash = [95.0, 88.0, 82.0]
    still_out = make_df(calm + crash)
    assert cond.score(Ctx(still_out)) == 0.0

    recovered = make_df(calm + crash + [86.0, 90.0])
    assert cond.score(Ctx(recovered)) > 0.0


def test_dmi_cross_up_on_trend_reversal():
    turn = list(100 * np.exp(np.linspace(0, -0.3, 120))) + list(
        100 * np.exp(-0.3) * np.exp(np.linspace(0, 0.25, 25))
    )
    assert get("dmi_cross_up").score(Ctx(make_df(turn)), {"within": 20}) > 0.0


def test_stoch_oversold_at_bottom_of_range():
    cond = get("stoch_oversold")
    at_bottom = make_df(list(np.linspace(130, 100, 60)))
    at_top = make_df(list(np.linspace(100, 130, 60)))
    assert cond.score(Ctx(at_bottom)) > 0.9
    assert cond.score(Ctx(at_top)) < 0.1


def test_ma_turn_up_detects_direction_change():
    turn = list(np.linspace(100, 80, 60)) + list(np.linspace(80, 95, 15))
    assert get("ma_turn_up").score(Ctx(make_df(turn)), {"within": 15}) > 0.0
    falling = list(np.linspace(100, 80, 80))
    assert get("ma_turn_up").score(Ctx(make_df(falling))) == 0.0


def test_ma_converging_scores_close_moving_averages(flat, uptrend):
    assert get("ma_converging").score(Ctx(flat)) == 1.0
    steep = make_df(100 * np.exp(np.linspace(0, 1.0, 150)))
    assert get("ma_converging").score(Ctx(steep)) < 1.0


def test_up_candle_volume_requires_both_price_and_volume():
    cond = get("up_candle_volume")
    closes = [100.0] * 59 + [103.0]
    quiet = [1_000_000.0] * 60
    loud = [1_000_000.0] * 59 + [3_000_000.0]
    assert cond.score(Ctx(make_df(closes, loud))) == 1.0
    assert cond.score(Ctx(make_df(closes, quiet))) < 0.5      # 거래량이 안 붙음
    down_closes = [100.0] * 59 + [97.0]
    assert cond.score(Ctx(make_df(down_closes, loud))) < 0.5  # 하락 캔들


def test_flow_conditions_score_zero_without_flow_data(uptrend):
    """수급을 받지 않은 캐시에서는 0점이어야 한다 (예외가 아니라)."""
    ctx = Ctx(uptrend)
    for key in ("foreign_net_buy", "inst_net_buy", "net_buy_volume"):
        assert get(key).score(ctx) == 0.0


def test_foreign_net_buy_counts_consecutive_days():
    df = make_df([100.0] * 40)
    df["foreign_net"] = [1000.0] * 37 + [500.0, 700.0, 900.0]
    assert get("foreign_net_buy").score(Ctx(df), {"days": 3}) == 1.0

    df.loc[df.index[-2], "foreign_net"] = -100.0  # 3일 중 하루만 매도
    assert get("foreign_net_buy").score(Ctx(df), {"days": 3}) == pytest.approx(2 / 3)


def test_net_buy_volume_sums_selected_investors():
    df = make_df([100.0] * 40)
    df["foreign_net"] = [10_000.0] * 40
    df["inst_net"] = [10_000.0] * 40
    cond = get("net_buy_volume")
    assert cond.score(Ctx(df), {"days": 5, "min_shares": 100_000, "who": "both"}) == 1.0
    assert cond.score(Ctx(df), {"days": 5, "min_shares": 100_000, "who": "foreign"}) < 1.0
