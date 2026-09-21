import numpy as np
import pandas as pd

from chartfinder import indicators as ind
from tests.conftest import make_df


def test_sma_matches_manual_mean():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert ind.sma(s, 3).iloc[-1] == 4.0
    assert pd.isna(ind.sma(s, 3).iloc[1])


def test_rsi_is_100_for_monotonic_rise():
    s = pd.Series(np.arange(1, 60), dtype=float)
    assert ind.rsi(s, 14).iloc[-1] == 100.0


def test_rsi_is_low_for_monotonic_fall():
    s = pd.Series(np.arange(60, 1, -1), dtype=float)
    assert ind.rsi(s, 14).iloc[-1] < 1.0


def test_rsi_stays_in_bounds(uptrend):
    values = ind.rsi(uptrend["close"]).dropna()
    assert values.between(0, 100).all()


def test_bollinger_bands_are_ordered(flat):
    lower, mid, upper = ind.bollinger(flat["close"])
    assert (lower.dropna() <= mid.dropna()).all()
    assert (mid.dropna() <= upper.dropna()).all()


def test_atr_is_positive(uptrend):
    assert ind.atr(uptrend).dropna().gt(0).all()


def test_volume_ratio_detects_spike():
    volumes = [1_000_000.0] * 40 + [5_000_000.0]
    df = make_df([100.0] * 41, volumes)
    assert ind.volume_ratio(df["volume"], 20).iloc[-1] == 5.0


def test_drawdown_from_high_is_zero_at_new_high(uptrend):
    assert ind.drawdown_from_high(uptrend["close"], 252).iloc[-1] == 0.0


def test_trading_value_falls_back_to_close_times_volume():
    df = make_df([10.0] * 30, [100.0] * 30)
    assert ind.trading_value(df, 20).iloc[-1] == 1000.0
