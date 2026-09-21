"""기술적 지표 모음. 모두 순수 pandas/numpy 벡터 연산."""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "sma", "ema", "rsi", "macd", "bollinger", "true_range", "atr",
    "rolling_high", "rolling_low", "pct_change_n", "volume_ratio",
    "band_width", "slope_pct", "drawdown_from_high", "trading_value",
    "dmi", "stochastic_slow",
]


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # 손실이 전혀 없는 구간은 RSI 100
    return out.where(avg_loss != 0, 100.0).where(avg_gain.notna())


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(macd, signal, histogram)"""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def bollinger(
    close: pd.Series, period: int = 20, mult: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(하단, 중심선, 상단)"""
    mid = sma(close, period)
    sd = close.rolling(period, min_periods=period).std(ddof=0)
    return mid - mult * sd, mid, mid + mult * sd


def band_width(close: pd.Series, period: int = 20, mult: float = 2.0) -> pd.Series:
    """볼린저 밴드 폭을 중심선 대비 %로."""
    lower, mid, upper = bollinger(close, period, mult)
    return (upper - lower) / mid.replace(0.0, np.nan) * 100.0


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rolling_high(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=max(2, period // 2)).max()


def rolling_low(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=max(2, period // 2)).min()


def pct_change_n(s: pd.Series, period: int) -> pd.Series:
    """N일 수익률 (%)."""
    return s.pct_change(period) * 100.0


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """당일 거래량 / 직전 N일 평균 거래량 (당일 제외)."""
    base = volume.shift(1).rolling(period, min_periods=period).mean()
    return volume / base.replace(0.0, np.nan)


def slope_pct(s: pd.Series, period: int = 5) -> pd.Series:
    """N일 전 대비 기울기를 %로. 이동평균의 방향 판단용."""
    return (s / s.shift(period) - 1.0) * 100.0


def drawdown_from_high(close: pd.Series, period: int = 252) -> pd.Series:
    """기간 최고가 대비 현재가 낙폭 (%, 음수)."""
    high = rolling_high(close, period)
    return (close / high - 1.0) * 100.0


def trading_value(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """평균 거래대금. 데이터 소스가 value를 주면 그대로, 없으면 종가*거래량."""
    value = df["value"] if "value" in df.columns else df["close"] * df["volume"]
    return value.rolling(period, min_periods=1).mean()


def dmi(
    df: pd.DataFrame, period: int = 14, adx_period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """DMI. (+DI, -DI, ADX) — Wilder 평활.

    +DM: 당일 고가가 전일 고가보다 더 많이 오른 폭 (하락폭보다 클 때만)
    -DM: 당일 저가가 전일 저가보다 더 많이 내린 폭 (상승폭보다 클 때만)
    """
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    def wilder(s: pd.Series) -> pd.Series:
        return s.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    atr_value = wilder(true_range(df)).replace(0.0, np.nan)
    plus_di = 100.0 * wilder(plus_dm) / atr_value
    minus_di = 100.0 * wilder(minus_dm) / atr_value

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=1 / adx_period, adjust=False, min_periods=adx_period).mean()
    return plus_di, minus_di, adx


def stochastic_slow(
    df: pd.DataFrame, period: int = 14, smooth_k: int = 3, smooth_d: int = 3
) -> tuple[pd.Series, pd.Series]:
    """Slow Stochastic. (%K, %D)

    Fast %K를 smooth_k로 평활한 것이 Slow %K, 그걸 다시 평활한 것이 %D.
    """
    low = df["low"].rolling(period, min_periods=period).min()
    high = df["high"].rolling(period, min_periods=period).max()
    span = (high - low).replace(0.0, np.nan)
    fast_k = 100.0 * (df["close"] - low) / span
    slow_k = fast_k.rolling(smooth_k, min_periods=smooth_k).mean()
    slow_d = slow_k.rolling(smooth_d, min_periods=smooth_d).mean()
    return slow_k, slow_d
