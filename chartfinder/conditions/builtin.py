"""내장 조건 모음.

새 조건을 추가하려면 @condition 데코레이터를 붙인 함수 하나만 쓰면 된다.
CLI 옵션과 웹 UI 위젯은 params 선언에서 자동으로 만들어진다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..scoring import binary, ordered, soft_between, soft_gt, soft_lt, soft_near
from .base import Ctx, Param, condition

TREND = "추세"
MOMENTUM = "모멘텀"
VOLATILITY = "변동성"
VOLUME = "거래량"
POSITION = "가격위치"
PATTERN = "패턴"
FILTER = "필터"


def _p(name, label, type="int", default=0, min=None, max=None, step=None, help=""):
    return Param(name, label, type, default, min, max, step, help=help)


def _recency_score(flags: pd.Series, within: int) -> float:
    """최근 within일 안에 True가 있으면 1.0, 그보다 오래됐으면 감쇠."""
    window = flags.tail(max(within * 3, within + 1))
    hits = np.flatnonzero(window.to_numpy())
    if hits.size == 0:
        return 0.0
    days_ago = len(window) - 1 - int(hits[-1])
    return soft_lt(days_ago, within, tol=max(within, 1))


# --------------------------------------------------------------------------- 추세


@condition(
    "ma_alignment", "이동평균 정배열", TREND,
    params=(
        _p("p1", "단기", default=5, min=2, max=60),
        _p("p2", "중기", default=20, min=3, max=120),
        _p("p3", "장기", default=60, min=5, max=240),
        _p("p4", "최장기", default=120, min=10, max=480),
    ),
    description="단기 > 중기 > 장기 > 최장기 이동평균 순서. 역전 폭에 따라 부분점수.",
    min_bars=130,
)
def ma_alignment(ctx: Ctx, p1: int, p2: int, p3: int, p4: int) -> float:
    return ordered([ctx.last(ctx.ma(p)) for p in (p1, p2, p3, p4)])


@condition(
    "golden_cross", "골든크로스", TREND,
    params=(
        _p("short", "단기 이평", default=5, min=2, max=60),
        _p("long", "장기 이평", default=20, min=5, max=240),
        _p("within", "최근 N일 이내", default=5, min=1, max=60),
    ),
    description="단기 이평이 장기 이평을 최근 N일 안에 상향 돌파.",
    min_bars=60,
)
def golden_cross(ctx: Ctx, short: int, long: int, within: int) -> float:
    fast, slow = ctx.ma(short), ctx.ma(long)
    crossed = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    return _recency_score(crossed.fillna(False), within)


@condition(
    "above_ma", "이동평균 위", TREND,
    params=(
        _p("period", "이평 기간", default=20, min=2, max=240),
        _p("margin", "최소 이격 (%)", "float", default=0.0, min=-20.0, max=50.0, step=0.5),
    ),
    description="종가가 이동평균보다 margin% 이상 위에 있음.",
)
def above_ma(ctx: Ctx, period: int, margin: float) -> float:
    ma = ctx.last(ctx.ma(period))
    close = ctx.last(ctx.close)
    if not ma or close is None:
        return 0.0
    gap = (close / ma - 1.0) * 100.0
    return soft_gt(gap, margin, tol=2.0)


@condition(
    "ma_slope_up", "이동평균 상승 추세", TREND,
    params=(
        _p("period", "이평 기간", default=20, min=3, max=240),
        _p("lookback", "기울기 측정 일수", default=5, min=1, max=60),
        _p("min_slope", "최소 상승률 (%)", "float", default=0.0, min=-10.0, max=30.0, step=0.5),
    ),
    description="이동평균이 lookback일 전보다 min_slope% 이상 상승.",
)
def ma_slope_up(ctx: Ctx, period: int, lookback: int, min_slope: float) -> float:
    slope = ctx.last(ind.slope_pct(ctx.ma(period), lookback))
    return soft_gt(slope, min_slope, tol=1.5)


@condition(
    "ma_pullback", "이평 눌림목", TREND,
    params=(
        _p("period", "이평 기간", default=20, min=3, max=240),
        _p("max_gap", "최대 이격 (%)", "float", default=3.0, min=0.5, max=20.0, step=0.5),
    ),
    description="상승 추세(이평 우상향) 중 종가가 이동평균에 근접.",
)
def ma_pullback(ctx: Ctx, period: int, max_gap: float) -> float:
    ma_series = ctx.ma(period)
    ma, close = ctx.last(ma_series), ctx.last(ctx.close)
    if not ma or close is None:
        return 0.0
    gap = abs(close / ma - 1.0) * 100.0
    rising = soft_gt(ctx.last(ind.slope_pct(ma_series, 5)), 0.0, tol=1.0)
    return soft_lt(gap, max_gap, tol=max_gap) * rising


# --------------------------------------------------------------------------- 모멘텀


@condition(
    "rsi_oversold", "RSI 과매도", MOMENTUM,
    params=(
        _p("period", "RSI 기간", default=14, min=2, max=60),
        _p("threshold", "기준값 이하", "float", default=30.0, min=5.0, max=60.0, step=1.0),
    ),
    description="RSI가 기준값 이하. 살짝 넘으면 부분점수.",
)
def rsi_oversold(ctx: Ctx, period: int, threshold: float) -> float:
    return soft_lt(ctx.last(ctx.rsi(period)), threshold, tol=5.0)


@condition(
    "rsi_overbought", "RSI 과매수", MOMENTUM,
    params=(
        _p("period", "RSI 기간", default=14, min=2, max=60),
        _p("threshold", "기준값 이상", "float", default=70.0, min=40.0, max=95.0, step=1.0),
    ),
    description="RSI가 기준값 이상.",
)
def rsi_overbought(ctx: Ctx, period: int, threshold: float) -> float:
    return soft_gt(ctx.last(ctx.rsi(period)), threshold, tol=5.0)


@condition(
    "rsi_range", "RSI 구간", MOMENTUM,
    params=(
        _p("period", "RSI 기간", default=14, min=2, max=60),
        _p("low", "하한", "float", default=40.0, min=0.0, max=100.0, step=1.0),
        _p("high", "상한", "float", default=60.0, min=0.0, max=100.0, step=1.0),
    ),
    description="RSI가 지정 구간 안.",
)
def rsi_range(ctx: Ctx, period: int, low: float, high: float) -> float:
    return soft_between(ctx.last(ctx.rsi(period)), low, high, tol=5.0)


@condition(
    "macd_cross_up", "MACD 골든크로스", MOMENTUM,
    params=(
        _p("fast", "단기", default=12, min=2, max=60),
        _p("slow", "장기", default=26, min=5, max=120),
        _p("signal", "시그널", default=9, min=2, max=60),
        _p("within", "최근 N일 이내", default=5, min=1, max=60),
    ),
    description="MACD가 시그널선을 최근 N일 안에 상향 돌파.",
    min_bars=60,
)
def macd_cross_up(ctx: Ctx, fast: int, slow: int, signal: int, within: int) -> float:
    macd_line, signal_line, _ = ctx.macd(fast, slow, signal)
    crossed = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    return _recency_score(crossed.fillna(False), within)


@condition(
    "macd_above_zero", "MACD 0선 위", MOMENTUM,
    params=(
        _p("fast", "단기", default=12, min=2, max=60),
        _p("slow", "장기", default=26, min=5, max=120),
        _p("signal", "시그널", default=9, min=2, max=60),
    ),
    description="MACD가 0선 위 (중기 상승 국면).",
    min_bars=60,
)
def macd_above_zero(ctx: Ctx, fast: int, slow: int, signal: int) -> float:
    macd_line, _, _ = ctx.macd(fast, slow, signal)
    value = ctx.last(macd_line)
    close = ctx.last(ctx.close)
    if value is None or not close:
        return 0.0
    return soft_gt(value / close * 100.0, 0.0, tol=0.3)  # 주가 대비 정규화


@condition(
    "return_range", "기간 수익률 구간", MOMENTUM,
    params=(
        _p("period", "기간 (일)", default=20, min=1, max=250),
        _p("low", "하한 (%)", "float", default=5.0, min=-90.0, max=300.0, step=1.0),
        _p("high", "상한 (%)", "float", default=50.0, min=-90.0, max=1000.0, step=1.0),
    ),
    description="N일 수익률이 지정 구간 안.",
)
def return_range(ctx: Ctx, period: int, low: float, high: float) -> float:
    return soft_between(ctx.last(ind.pct_change_n(ctx.close, period)), low, high)


@condition(
    "consecutive_up", "연속 상승", MOMENTUM,
    params=(_p("days", "연속 일수", default=3, min=2, max=15),),
    description="최근 N일 연속 종가 상승.",
)
def consecutive_up(ctx: Ctx, days: int) -> float:
    ups = (ctx.close.diff() > 0).tail(days)
    if len(ups) < days:
        return 0.0
    return float(ups.sum()) / days  # 3일 중 2일이면 0.67


# --------------------------------------------------------------------------- 변동성


@condition(
    "bb_upper_break", "볼린저 상단 돌파", VOLATILITY,
    params=(
        _p("period", "기간", default=20, min=5, max=120),
        _p("mult", "표준편차 배수", "float", default=2.0, min=0.5, max=4.0, step=0.1),
    ),
    description="종가가 볼린저 상단 위.",
)
def bb_upper_break(ctx: Ctx, period: int, mult: float) -> float:
    _, _, upper = ctx.bollinger(period, mult)
    up, close = ctx.last(upper), ctx.last(ctx.close)
    if not up or close is None:
        return 0.0
    return soft_gt((close / up - 1.0) * 100.0, 0.0, tol=1.5)


@condition(
    "bb_lower_touch", "볼린저 하단 이탈", VOLATILITY,
    params=(
        _p("period", "기간", default=20, min=5, max=120),
        _p("mult", "표준편차 배수", "float", default=2.0, min=0.5, max=4.0, step=0.1),
    ),
    description="종가가 볼린저 하단 아래 (과매도 반등 후보).",
)
def bb_lower_touch(ctx: Ctx, period: int, mult: float) -> float:
    lower, _, _ = ctx.bollinger(period, mult)
    low, close = ctx.last(lower), ctx.last(ctx.close)
    if not low or close is None:
        return 0.0
    return soft_lt((close / low - 1.0) * 100.0, 0.0, tol=1.5)


@condition(
    "bb_squeeze", "볼린저 스퀴즈", VOLATILITY,
    params=(
        _p("period", "기간", default=20, min=5, max=120),
        _p("max_width", "최대 밴드폭 (%)", "float", default=10.0, min=1.0, max=60.0, step=0.5),
    ),
    description="밴드 폭이 좁아진 변동성 수축 구간 (분출 직전 후보).",
)
def bb_squeeze(ctx: Ctx, period: int, max_width: float) -> float:
    return soft_lt(ctx.last(ctx.band_width(period)), max_width, tol=max_width * 0.3)


@condition(
    "atr_range", "변동성(ATR) 구간", VOLATILITY,
    params=(
        _p("period", "ATR 기간", default=14, min=2, max=60),
        _p("low", "하한 (%)", "float", default=1.0, min=0.0, max=30.0, step=0.1),
        _p("high", "상한 (%)", "float", default=5.0, min=0.1, max=50.0, step=0.1),
    ),
    description="주가 대비 ATR 비율이 지정 구간 안.",
)
def atr_range(ctx: Ctx, period: int, low: float, high: float) -> float:
    atr_value, close = ctx.last(ctx.atr(period)), ctx.last(ctx.close)
    if atr_value is None or not close:
        return 0.0
    return soft_between(atr_value / close * 100.0, low, high)


# --------------------------------------------------------------------------- 거래량


@condition(
    "volume_surge", "거래량 급증", VOLUME,
    params=(
        _p("period", "평균 기간", default=20, min=3, max=120),
        _p("ratio", "평균 대비 배수", "float", default=2.0, min=1.0, max=20.0, step=0.1),
    ),
    description="당일 거래량이 직전 평균의 ratio배 이상.",
)
def volume_surge(ctx: Ctx, period: int, ratio: float) -> float:
    return soft_gt(ctx.last(ctx.volume_ratio(period)), ratio, tol=ratio * 0.25)


@condition(
    "volume_dryup", "거래량 감소", VOLUME,
    params=(
        _p("period", "평균 기간", default=20, min=3, max=120),
        _p("ratio", "평균 대비 배수 이하", "float", default=0.6, min=0.05, max=1.5, step=0.05),
    ),
    description="거래량이 평균 대비 크게 줄어든 상태 (매물 소화 구간).",
)
def volume_dryup(ctx: Ctx, period: int, ratio: float) -> float:
    return soft_lt(ctx.last(ctx.volume_ratio(period)), ratio, tol=0.2)


@condition(
    "min_trading_value", "최소 거래대금", FILTER,
    params=(
        _p("period", "평균 기간", default=20, min=1, max=120),
        _p("amount", "최소 평균 거래대금 (백만)", "float", default=1000.0, min=0.0, max=1e6, step=100.0),
    ),
    description="유동성 필터. 평균 거래대금이 기준 이상 (통화는 시장 기준: 원/달러).",
    min_bars=5,
)
def min_trading_value(ctx: Ctx, period: int, amount: float) -> float:
    value = ctx.last(ctx.trading_value(period))
    if value is None:
        return 0.0
    return soft_gt(value / 1e6, amount, tol=max(amount * 0.2, 1.0))


# --------------------------------------------------------------------------- 가격위치


@condition(
    "near_high", "신고가 근접", POSITION,
    params=(
        _p("period", "기간 (일)", default=252, min=20, max=1000),
        _p("max_gap", "고점 대비 낙폭 이내 (%)", "float", default=5.0, min=0.0, max=50.0, step=0.5),
    ),
    description="기간 최고가 대비 max_gap% 이내.",
    min_bars=60,
)
def near_high(ctx: Ctx, period: int, max_gap: float) -> float:
    dd = ctx.last(ind.drawdown_from_high(ctx.close, period))
    if dd is None:
        return 0.0
    return soft_lt(abs(dd), max_gap, tol=max(max_gap * 0.5, 1.0))


@condition(
    "near_low", "신저가 근접", POSITION,
    params=(
        _p("period", "기간 (일)", default=252, min=20, max=1000),
        _p("max_gap", "저점 대비 상승폭 이내 (%)", "float", default=5.0, min=0.0, max=50.0, step=0.5),
    ),
    description="기간 최저가 대비 max_gap% 이내 (바닥권).",
    min_bars=60,
)
def near_low(ctx: Ctx, period: int, max_gap: float) -> float:
    low = ctx.last(ind.rolling_low(ctx.close, period))
    close = ctx.last(ctx.close)
    if not low or close is None:
        return 0.0
    return soft_lt((close / low - 1.0) * 100.0, max_gap, tol=max(max_gap * 0.5, 1.0))


@condition(
    "drawdown_range", "고점 대비 낙폭 구간", POSITION,
    params=(
        _p("period", "기간 (일)", default=252, min=20, max=1000),
        _p("low", "최소 낙폭 (%)", "float", default=20.0, min=0.0, max=95.0, step=1.0),
        _p("high", "최대 낙폭 (%)", "float", default=50.0, min=0.0, max=99.0, step=1.0),
    ),
    description="기간 최고가 대비 낙폭이 구간 안 (조정 국면 탐색).",
    min_bars=60,
)
def drawdown_range(ctx: Ctx, period: int, low: float, high: float) -> float:
    dd = ctx.last(ind.drawdown_from_high(ctx.close, period))
    return soft_between(abs(dd) if dd is not None else None, low, high)


@condition(
    "price_range", "주가 구간", FILTER,
    params=(
        _p("low", "하한", "float", default=0.0, min=0.0, max=1e7, step=100.0),
        _p("high", "상한", "float", default=1e7, min=0.0, max=1e9, step=100.0),
    ),
    description="현재가가 지정 구간 안.",
    min_bars=1,
)
def price_range(ctx: Ctx, low: float, high: float) -> float:
    return binary(low <= (ctx.last(ctx.close) or -1) <= high)


# --------------------------------------------------------------------------- 패턴


@condition(
    "box_range", "박스권 횡보", PATTERN,
    params=(
        _p("period", "기간 (일)", default=60, min=10, max=250),
        _p("max_width", "고저 폭 이내 (%)", "float", default=15.0, min=2.0, max=60.0, step=1.0),
    ),
    description="최근 N일 고가~저가 폭이 좁은 횡보 구간.",
    min_bars=40,
)
def box_range(ctx: Ctx, period: int, max_width: float) -> float:
    window = ctx.df.tail(period)
    if len(window) < max(10, period // 2):
        return 0.0
    high, low = float(window["high"].max()), float(window["low"].min())
    if low <= 0:
        return 0.0
    width = (high / low - 1.0) * 100.0
    return soft_lt(width, max_width, tol=max_width * 0.3)


@condition(
    "breakout_high", "N일 신고가 돌파", PATTERN,
    params=(
        _p("period", "기간 (일)", default=60, min=5, max=500),
        _p("within", "최근 N일 이내", default=3, min=1, max=30),
    ),
    description="종가가 직전 N일 최고가를 넘어선 시점이 최근일수록 고점.",
    min_bars=40,
)
def breakout_high(ctx: Ctx, period: int, within: int) -> float:
    prior_high = ind.rolling_high(ctx.high, period).shift(1)
    broke = ctx.close > prior_high
    return _recency_score(broke.fillna(False), within)


@condition(
    "pullback_rebound", "조정 후 반등", PATTERN,
    params=(
        _p("lookback", "관찰 기간 (일)", default=60, min=10, max=250),
        _p("min_drop", "최소 조정폭 (%)", "float", default=10.0, min=1.0, max=60.0, step=1.0),
        _p("min_rebound", "최소 반등폭 (%)", "float", default=3.0, min=0.5, max=40.0, step=0.5),
    ),
    description="기간 고점에서 min_drop% 이상 밀린 뒤 저점 대비 min_rebound% 이상 반등.",
    min_bars=40,
)
def pullback_rebound(
    ctx: Ctx, lookback: int, min_drop: float, min_rebound: float
) -> float:
    window = ctx.close.tail(lookback)
    if len(window) < 10:
        return 0.0
    peak_idx = int(window.to_numpy().argmax())
    after_peak = window.iloc[peak_idx:]
    if len(after_peak) < 3:
        return 0.0
    peak = float(after_peak.iloc[0])
    trough = float(after_peak.min())
    close = float(window.iloc[-1])
    if peak <= 0 or trough <= 0:
        return 0.0
    drop = (1.0 - trough / peak) * 100.0
    rebound = (close / trough - 1.0) * 100.0
    # 반등이 이미 전고점을 넘었다면 '조정 후 반등'이 아니라 신고가 국면
    still_below = soft_lt((close / peak - 1.0) * 100.0, 0.0, tol=3.0)
    return (
        soft_gt(drop, min_drop, tol=min_drop * 0.4)
        * soft_gt(rebound, min_rebound, tol=min_rebound)
        * still_below
    )


@condition(
    "gap_up", "갭 상승", PATTERN,
    params=(
        _p("min_gap", "최소 갭 (%)", "float", default=2.0, min=0.1, max=30.0, step=0.1),
        _p("within", "최근 N일 이내", default=1, min=1, max=20),
    ),
    description="시가가 전일 고가보다 min_gap% 이상 위에서 출발.",
    min_bars=10,
)
def gap_up(ctx: Ctx, min_gap: float, within: int) -> float:
    prev_high = ctx.high.shift(1)
    gap_pct = (ctx.df["open"] / prev_high - 1.0) * 100.0
    return _recency_score((gap_pct >= min_gap).fillna(False), within)


@condition(
    "near_price", "특정 가격 근접", POSITION,
    params=(
        _p("target", "목표가", "float", default=0.0, min=0.0, max=1e9, step=100.0),
        _p("tol_pct", "허용 오차 (%)", "float", default=3.0, min=0.1, max=30.0, step=0.5),
    ),
    description="현재가가 목표가에 근접.",
    min_bars=1,
)
def near_price(ctx: Ctx, target: float, tol_pct: float) -> float:
    close = ctx.last(ctx.close)
    if close is None or target <= 0:
        return 0.0
    return soft_near((close / target - 1.0) * 100.0, 0.0, tol=tol_pct)
