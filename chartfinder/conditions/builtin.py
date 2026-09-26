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
FLOW = "수급"
FUNDAMENTAL = "재무"
PATTERN = "패턴"
FILTER = "필터"
PROFILE = "종목정보"


def _p(name, label, type="int", default=0, min=None, max=None, step=None,
       choices=(), help=""):
    return Param(name, label, type, default, min, max, step, choices, help)


def _hist_zone(ctx: Ctx, hist: pd.Series, below: bool, window: int = 60) -> float:
    """히스토그램이 0선 아래(또는 위)에 있는 정도.

    히스토그램 크기는 종목·국면마다 제각각이라 절대값이나 주가 대비 %로 자르면
    완만한 차트에서 0 근처에 붙은 값이 그대로 통과해버린다. 그래서 히스토그램
    자신의 최근 변동폭으로 정규화해서 판단한다.
    """
    value = ctx.last(hist)
    if value is None:
        return 0.0
    scale = hist.tail(window).std(ddof=0)
    if scale is None or pd.isna(scale) or scale <= 0:
        return 0.0
    ratio = value / float(scale)
    return soft_lt(ratio, 0.0, tol=0.3) if below else soft_gt(ratio, 0.0, tol=0.3)


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
    "macd_cross_up", "MACD 골든크로스", MOMENTUM,
    params=(
        _p("fast", "단기", default=12, min=2, max=60),
        _p("slow", "장기", default=26, min=5, max=120),
        _p("signal", "시그널", default=9, min=2, max=60),
        _p("within", "최근 N일 이내", default=5, min=1, max=60),
        _p("zone", "교차 위치", "choice", default="any", choices=("any", "below", "above"),
           help="below=0선 아래(바닥권 초입), above=0선 위(상승 추세 중)"),
    ),
    description="MACD가 시그널선을 최근 N일 안에 상향 돌파. zone으로 0선 아래(약세권)·위(강세권) 교차를 구분한다.",
    min_bars=60,
)
def macd_cross_up(
    ctx: Ctx, fast: int, slow: int, signal: int, within: int, zone: str
) -> float:
    macd_line, signal_line, _ = ctx.macd(fast, slow, signal)
    crossed = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    if zone == "below":
        crossed &= macd_line < 0
    elif zone == "above":
        crossed &= macd_line > 0
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
        _p("low", "하한 (%)", "float", default=2.0, min=0.0, max=30.0, step=0.1),
        _p("high", "상한 (%)", "float", default=6.0, min=0.1, max=50.0, step=0.1),
    ),
    description="주가 대비 ATR 비율이 지정 구간 안. 너무 둔하지도 과하지도 않은 변동성을 고른다.",
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
        _p("low", "하한", "float", default=1000.0, min=0.0, max=1e7, step=100.0),
        _p("high", "상한", "float", default=500_000.0, min=0.0, max=1e9, step=100.0),
    ),
    description="현재가가 지정 구간 안. 기본값은 동전주와 초고가주를 걸러낸다.",
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



# --------------------------------------------------------------------------- 반등 확인 신호


@condition(
    "bb_lower_recovery", "볼린저 하단 이탈 후 복귀", VOLATILITY,
    params=(
        _p("period", "기간", default=20, min=5, max=120),
        _p("mult", "표준편차 배수", "float", default=2.0, min=0.5, max=4.0, step=0.1),
        _p("within", "이탈 후 N일 이내", default=5, min=1, max=30),
    ),
    description="최근 N일 안에 하단을 이탈했다가 현재는 밴드 안으로 돌아온 상태.",
)
def bb_lower_recovery(ctx: Ctx, period: int, mult: float, within: int) -> float:
    lower, _, _ = ctx.bollinger(period, mult)
    broke = (ctx.close < lower).fillna(False)
    if not bool(broke.tail(within).any()):
        return 0.0
    # 이탈 이력이 있고 지금은 밴드 안이어야 '복귀'
    inside = soft_gt(ctx.last(ctx.close), ctx.last(lower) or 0.0, tol=1e-9)
    return _recency_score(broke, within) * inside


@condition(
    "rsi_cross_up", "RSI 기준선 상향 돌파", MOMENTUM,
    params=(
        _p("period", "RSI 기간", default=14, min=2, max=60),
        _p("threshold", "기준선", "float", default=30.0, min=5.0, max=95.0, step=1.0),
        _p("within", "최근 N일 이내", default=5, min=1, max=30),
    ),
    description="RSI가 기준선 아래에 있다가 위로 올라선 시점이 최근일수록 높은 점수.",
)
def rsi_cross_up(ctx: Ctx, period: int, threshold: float, within: int) -> float:
    values = ctx.rsi(period)
    crossed = (values > threshold) & (values.shift(1) <= threshold)
    return _recency_score(crossed.fillna(False), within)


@condition(
    "dmi_cross_up", "DMI +DI 상향 돌파", TREND,
    params=(
        _p("period", "DMI 기간", default=14, min=2, max=60),
        _p("within", "최근 N일 이내", default=5, min=1, max=30),
    ),
    description="+DI가 -DI를 아래에서 위로 돌파 (추세 전환 신호).",
    min_bars=40,
)
def dmi_cross_up(ctx: Ctx, period: int, within: int) -> float:
    plus_di, minus_di, _ = ctx.dmi(period)
    crossed = (plus_di > minus_di) & (plus_di.shift(1) <= minus_di.shift(1))
    return _recency_score(crossed.fillna(False), within)


@condition(
    "dmi_spread_widening", "DMI 간격 확대", TREND,
    params=(
        _p("period", "DMI 기간", default=14, min=2, max=60),
        _p("lookback", "비교 기준 일수", default=3, min=1, max=30),
        _p("min_spread", "최소 간격", "float", default=3.0, min=0.0, max=50.0, step=0.5),
    ),
    description="+DI가 -DI 위에 있고, 둘의 간격이 lookback일 전보다 벌어지는 중.",
    min_bars=40,
)
def dmi_spread_widening(ctx: Ctx, period: int, lookback: int, min_spread: float) -> float:
    plus_di, minus_di, _ = ctx.dmi(period)
    spread = plus_di - minus_di
    now, before = ctx.last(spread), ctx.last(spread, lookback)
    if now is None or before is None:
        return 0.0
    above = soft_gt(now, min_spread, tol=max(min_spread, 2.0))
    widening = soft_gt(now - before, 0.0, tol=2.0)
    return above * widening


@condition(
    "stoch_oversold", "스토캐스틱 과매도", MOMENTUM,
    params=(
        _p("period", "기간", default=14, min=3, max=60),
        _p("smooth_k", "%K 평활", default=3, min=1, max=15),
        _p("smooth_d", "%D 평활", default=3, min=1, max=15),
        _p("threshold", "기준값 이하", "float", default=20.0, min=1.0, max=50.0, step=1.0),
    ),
    description="Slow %K가 과매도 기준 이하.",
    min_bars=40,
)
def stoch_oversold(
    ctx: Ctx, period: int, smooth_k: int, smooth_d: int, threshold: float
) -> float:
    slow_k, _ = ctx.stochastic(period, smooth_k, smooth_d)
    return soft_lt(ctx.last(slow_k), threshold, tol=8.0)


@condition(
    "stoch_cross_up", "스토캐스틱 골든크로스", MOMENTUM,
    params=(
        _p("period", "기간", default=14, min=3, max=60),
        _p("smooth_k", "%K 평활", default=3, min=1, max=15),
        _p("smooth_d", "%D 평활", default=3, min=1, max=15),
        _p("within", "최근 N일 이내", default=5, min=1, max=30),
        _p("max_level", "교차 시 최대 레벨", "float", default=40.0, min=5.0, max=100.0, step=5.0),
    ),
    description="%K가 %D를 아래에서 위로 돌파. 과매도권(max_level 이하)에서의 교차만 인정.",
    min_bars=40,
)
def stoch_cross_up(
    ctx: Ctx, period: int, smooth_k: int, smooth_d: int, within: int, max_level: float
) -> float:
    slow_k, slow_d = ctx.stochastic(period, smooth_k, smooth_d)
    crossed = (slow_k > slow_d) & (slow_k.shift(1) <= slow_d.shift(1)) & (slow_d <= max_level)
    return _recency_score(crossed.fillna(False), within)


@condition(
    "ma_turn_up", "이동평균 상승 전환", TREND,
    params=(
        _p("period", "이평 기간", default=5, min=2, max=120),
        _p("within", "최근 N일 이내", default=3, min=1, max=20),
        _p("confirm", "전환 전 하락 일수", default=2, min=1, max=20),
    ),
    description="내리던 이동평균이 방향을 틀어 올라선 시점이 최근일수록 높은 점수.",
)
def ma_turn_up(ctx: Ctx, period: int, within: int, confirm: int) -> float:
    ma = ctx.ma(period)
    rising = ma.diff() > 0
    # 직전 confirm일 동안 내리다가 오늘 오른 지점이 '전환'
    was_falling = (~rising).shift(1).rolling(confirm, min_periods=confirm).sum() == confirm
    turned = rising & was_falling.fillna(False)
    return _recency_score(turned.fillna(False), within)


@condition(
    "ma_converging", "이동평균 수렴", TREND,
    params=(
        _p("short", "단기 이평", default=5, min=2, max=60),
        _p("long", "장기 이평", default=20, min=3, max=240),
        _p("max_gap", "최대 이격 (%)", "float", default=3.0, min=0.1, max=20.0, step=0.5),
    ),
    description="단기 이평이 장기 이평에 근접 (골든크로스 직전 구간).",
)
def ma_converging(ctx: Ctx, short: int, long: int, max_gap: float) -> float:
    fast, slow = ctx.last(ctx.ma(short)), ctx.last(ctx.ma(long))
    if not fast or not slow:
        return 0.0
    gap = abs(fast / slow - 1.0) * 100.0
    return soft_lt(gap, max_gap, tol=max_gap)


@condition(
    "up_candle_volume", "상승 캔들 + 거래량 증가", VOLUME,
    params=(
        _p("period", "평균 기간", default=20, min=3, max=120),
        _p("ratio", "평균 대비 배수", "float", default=1.5, min=1.0, max=10.0, step=0.1),
        _p("min_gain", "최소 상승률 (%)", "float", default=0.0, min=-5.0, max=30.0, step=0.5),
    ),
    description="종가가 오른 날에 거래량도 함께 늘었는지. 둘 다 만족해야 점수가 나온다.",
)
def up_candle_volume(ctx: Ctx, period: int, ratio: float, min_gain: float) -> float:
    gain = ctx.last(ctx.close.pct_change() * 100.0)
    volume_up = soft_gt(ctx.last(ctx.volume_ratio(period)), ratio, tol=ratio * 0.3)
    return soft_gt(gain, min_gain, tol=1.5) * volume_up


# --------------------------------------------------------------------------- 수급
# 외국인·기관 순매수는 `update --flows` 로 받은 캐시에서만 값이 나온다 (한국 시장 전용).


def _net_sum(ctx: Ctx, who: str, days: int) -> float | None:
    """대상(외국인/기관/둘 다)의 N일 누적 순매수 주식 수. 수급이 없으면 None."""
    series = []
    if who in ("foreign", "both"):
        series.append(ctx.flow("foreign_net"))
    if who in ("inst", "both"):
        series.append(ctx.flow("inst_net"))
    available = [s for s in series if s is not None]
    if not available:
        return None
    return sum(float(s.dropna().tail(days).sum()) for s in available)


def _net_buy_days(series, days: int) -> float:
    """최근 days일 중 순매수였던 날의 비율.

    당일 수급은 장 마감 후에야 공시되므로 마지막 행이 비어 있는 경우가 흔하다.
    빈 행은 건너뛰고 값이 있는 최근 days일로 판단한다.
    """
    if series is None:
        return 0.0
    window = series.dropna().tail(days)
    if len(window) < days:
        return 0.0
    return float((window > 0).sum()) / days


@condition(
    "foreign_net_buy", "외국인 연속 순매수", FLOW,
    params=(_p("days", "연속 일수", default=3, min=1, max=20),),
    description="최근 N일 연속 외국인 순매수. 일부만 맞으면 그 비율만큼 부분점수.",
    min_bars=5,
)
def foreign_net_buy(ctx: Ctx, days: int) -> float:
    return _net_buy_days(ctx.flow("foreign_net"), days)


@condition(
    "inst_net_buy", "기관 연속 순매수", FLOW,
    params=(_p("days", "연속 일수", default=3, min=1, max=20),),
    description="최근 N일 연속 기관 순매수.",
    min_bars=5,
)
def inst_net_buy(ctx: Ctx, days: int) -> float:
    return _net_buy_days(ctx.flow("inst_net"), days)


@condition(
    "net_buy_volume", "누적 순매수 수량", FLOW,
    params=(
        _p("days", "누적 일수", default=5, min=1, max=60),
        _p("min_shares", "최소 순매수 (주)", "float", default=100_000.0, min=0.0, max=1e9, step=10_000.0),
        _p("who", "대상", "choice", default="both", choices=("foreign", "inst", "both")),
    ),
    description="외국인/기관의 N일 누적 순매수 주식 수가 기준 이상.",
    min_bars=5,
)
def net_buy_volume(ctx: Ctx, days: int, min_shares: float, who: str) -> float:
    series = []
    if who in ("foreign", "both"):
        series.append(ctx.flow("foreign_net"))
    if who in ("inst", "both"):
        series.append(ctx.flow("inst_net"))
    available = [s for s in series if s is not None]
    if not available:
        return 0.0
    total = sum(float(s.dropna().tail(days).sum()) for s in available)
    return soft_gt(total, min_shares, tol=max(min_shares * 0.3, 1.0))


@condition(
    "macd_hist_turn_up", "MACD 히스토그램 상승 전환 (초입)", MOMENTUM,
    params=(
        _p("fast", "단기", default=12, min=2, max=60),
        _p("slow", "장기", default=26, min=5, max=120),
        _p("signal", "시그널", default=9, min=2, max=60),
        _p("rising_days", "연속 상승 일수", default=2, min=1, max=15),
        _p("below_zero", "0선 아래에서만", "bool", default=True),
    ),
    description=(
        "MACD 오실레이터(히스토그램)가 저점을 찍고 상승 전환. "
        "골든크로스보다 먼저 나오는 초입 신호이며, below_zero=True면 0선 아래 구간만 인정한다."
    ),
    min_bars=60,
)
def macd_hist_turn_up(
    ctx: Ctx, fast: int, slow: int, signal: int, rising_days: int, below_zero: bool
) -> float:
    _, _, hist = ctx.macd(fast, slow, signal)
    diff = hist.diff()
    rising = (diff > 0).tail(rising_days)
    if len(rising) < rising_days or hist.tail(rising_days).isna().any():
        return 0.0
    if not bool(rising.iloc[-1]):
        return 0.0  # 오늘 오르지 않았으면 전환이 아니다

    # rising_days 중 오른 날의 비율 (3일 중 2일이면 0.67)
    momentum = float(rising.sum()) / rising_days
    return momentum * (_hist_zone(ctx, hist, below=True) if below_zero else 1.0)


@condition(
    "macd_hist_weakening", "MACD 히스토그램 하락 전환", MOMENTUM,
    params=(
        _p("fast", "단기", default=12, min=2, max=60),
        _p("slow", "장기", default=26, min=5, max=120),
        _p("signal", "시그널", default=9, min=2, max=60),
        _p("falling_days", "연속 하락 일수", default=2, min=1, max=15),
        _p("above_zero", "0선 위에서만", "bool", default=True),
    ),
    description="히스토그램이 고점을 찍고 꺾이는 구간. 보유 종목 점검이나 약세 탐색용.",
    min_bars=60,
)
def macd_hist_weakening(
    ctx: Ctx, fast: int, slow: int, signal: int, falling_days: int, above_zero: bool
) -> float:
    _, _, hist = ctx.macd(fast, slow, signal)
    falling = (hist.diff() < 0).tail(falling_days)
    if len(falling) < falling_days or hist.tail(falling_days).isna().any():
        return 0.0
    if not bool(falling.iloc[-1]):
        return 0.0

    momentum = float(falling.sum()) / falling_days
    return momentum * (_hist_zone(ctx, hist, below=False) if above_zero else 1.0)


@condition(
    "volume_floor", "거래량 하한", VOLUME,
    params=(
        _p("days", "확인 일수", default=3, min=1, max=60),
        _p("min_volume", "최소 거래량 (주)", "float", default=300_000.0, min=0.0, max=1e10, step=10_000.0),
        _p("mode", "판정 방식", "choice", default="each", choices=("each", "sum", "avg"),
           help="each=N일 모두 기준 이상, sum=N일 합계, avg=N일 평균"),
    ),
    description="최근 N일 거래량이 기준 이상인지. 유동성이 받쳐주는 종목만 남길 때 쓴다.",
    min_bars=5,
)
def volume_floor(ctx: Ctx, days: int, min_volume: float, mode: str) -> float:
    window = ctx.volume.dropna().tail(days)
    if len(window) < days:
        return 0.0
    if mode == "sum":
        return soft_gt(float(window.sum()), min_volume, tol=max(min_volume * 0.2, 1.0))
    if mode == "avg":
        return soft_gt(float(window.mean()), min_volume, tol=max(min_volume * 0.2, 1.0))
    # each: 하루라도 미달이면 그만큼 감점 (3일 중 2일 충족이면 0.67)
    return float((window >= min_volume).sum()) / days


# --------------------------------------------------------------------------- 재무
# `update --fundamentals` 로 받은 재무 캐시가 있어야 점수가 나온다.


def _rising_ratio(series, years: int, min_growth: float) -> float:
    """최근 years개 값이 해마다 min_growth% 이상 늘었는지의 비율."""
    if series is None or len(series) < years:
        return 0.0
    window = series.tail(years)
    steps = []
    for before, after in zip(window[:-1], window[1:]):
        if before is None or before == 0:
            continue
        growth = (after / abs(before) - 1.0) * 100.0
        steps.append(soft_gt(growth, min_growth, tol=max(abs(min_growth), 5.0)))
    return sum(steps) / len(steps) if steps else 0.0


@condition(
    "revenue_growth", "매출액 증가", FUNDAMENTAL,
    params=(
        # 네이버가 주는 연간 실적은 3개 연도뿐이다. 4를 기본값으로 두면
        # 데이터가 모자라 늘 0점이 나온다.
        _p("years", "확인 연수", default=3, min=2, max=10),
        _p("min_growth", "연간 최소 증가율 (%)", "float", default=0.0, min=-50.0, max=100.0, step=1.0),
    ),
    description="최근 N년 매출액이 해마다 늘었는지. 일부 해만 늘면 그 비율만큼 부분점수.",
    min_bars=1,
)
def revenue_growth(ctx: Ctx, years: int, min_growth: float) -> float:
    return _rising_ratio(ctx.fundamental("revenue"), years, min_growth)


@condition(
    "operating_income_growth", "영업이익 증가", FUNDAMENTAL,
    params=(
        _p("years", "확인 연수", default=3, min=2, max=10),
        _p("min_growth", "연간 최소 증가율 (%)", "float", default=0.0, min=-50.0, max=100.0, step=1.0),
    ),
    description="최근 N년 영업이익이 해마다 늘었는지.",
    min_bars=1,
)
def operating_income_growth(ctx: Ctx, years: int, min_growth: float) -> float:
    return _rising_ratio(ctx.fundamental("operating_income"), years, min_growth)


@condition(
    "operating_margin", "영업이익률", FUNDAMENTAL,
    params=(
        _p("min_margin", "최소 영업이익률 (%)", "float", default=5.0, min=-50.0, max=90.0, step=0.5),
        _p("years", "평균 낼 연수", default=1, min=1, max=10),
    ),
    description="최근 N년 평균 영업이익률이 기준 이상.",
    min_bars=1,
)
def operating_margin(ctx: Ctx, min_margin: float, years: int) -> float:
    series = ctx.fundamental("operating_margin", years)
    if series is None:
        return 0.0
    return soft_gt(float(series.mean()), min_margin, tol=max(abs(min_margin) * 0.4, 2.0))


@condition(
    "debt_ratio", "부채비율", FUNDAMENTAL,
    params=(
        _p("max_ratio", "최대 부채비율 (%)", "float", default=100.0, min=0.0, max=1000.0, step=10.0),
        _p("years", "확인 연수", default=1, min=1, max=10),
    ),
    description="최근 N년 평균 부채비율이 기준 이하. 낮을수록 안정적.",
    min_bars=1,
)
def debt_ratio(ctx: Ctx, max_ratio: float, years: int) -> float:
    series = ctx.fundamental("debt_ratio", years)
    if series is None:
        return 0.0
    return soft_lt(float(series.mean()), max_ratio, tol=max(max_ratio * 0.3, 10.0))


@condition(
    "roe", "ROE", FUNDAMENTAL,
    params=(
        _p("min_roe", "최소 ROE (%)", "float", default=10.0, min=-50.0, max=100.0, step=1.0),
        _p("years", "평균 낼 연수", default=1, min=1, max=10),
    ),
    description="최근 N년 평균 자기자본이익률이 기준 이상.",
    min_bars=1,
)
def roe(ctx: Ctx, min_roe: float, years: int) -> float:
    series = ctx.fundamental("roe", years)
    if series is None:
        return 0.0
    return soft_gt(float(series.mean()), min_roe, tol=max(abs(min_roe) * 0.4, 2.0))


@condition(
    "positive_cash_flow", "영업현금흐름 플러스", FUNDAMENTAL,
    params=(_p("years", "확인 연수", default=3, min=1, max=10),),
    description="최근 N년 영업활동현금흐름이 모두 플러스. 일부만 플러스면 그 비율.",
    min_bars=1,
)
def positive_cash_flow(ctx: Ctx, years: int) -> float:
    series = ctx.fundamental("operating_cash_flow", years)
    if series is None or len(series) < years:
        return 0.0
    return float((series > 0).sum()) / years


# --------------------------------------------------------------------------- 종목정보
#
# 여기서부터는 일봉이 아니라 "지금 이 종목은 이렇다" 는 스냅샷 값을 본다.
# 거래대금·회전율은 시세만으로 계산되므로 언제나 채점된다. 나머지는
# `chartfinder update --profiles` 로 받아 둔 값이 있어야 하고, 항목별로
# 출처가 달라 일부는 못 받을 수 있다 (못 받으면 0점 → 실행 후 경고가 뜬다).


@condition(
    "turnover_value", "거래대금", VOLUME,
    params=(
        _p("min_value", "최소 평균 거래대금 (억원)", "float",
           default=10.0, min=0.1, max=10000.0, step=1.0),
        _p("period", "평균 낼 일수", default=20, min=1, max=120),
    ),
    description="최근 N일 평균 거래대금이 기준 이상. 거래량보다 실제로 들어온 돈을 본다.",
    min_bars=5,
)
def turnover_value(ctx: Ctx, min_value: float, period: int) -> float:
    value = ctx.last(ctx.turnover(period))
    if value is None or value <= 0:
        return 0.0
    return soft_gt(value / 1e8, min_value, tol=max(min_value * 0.5, 1.0))


@condition(
    "turnover_surge", "거래대금 급증", VOLUME,
    params=(
        _p("mult", "평균 대비 배수", "float", default=2.0, min=1.1, max=20.0, step=0.1),
        _p("period", "비교할 평균 일수", default=20, min=5, max=120),
    ),
    description="최근 거래대금이 평소 평균의 N배 이상. 거래량 급증보다 돈의 유입을 본다.",
    min_bars=25,
)
def turnover_surge(ctx: Ctx, mult: float, period: int) -> float:
    turnover = ctx.turnover()
    today = ctx.last(turnover)
    average = ctx.last(turnover.rolling(period).mean())
    if not today or not average or average <= 0:
        return 0.0
    return soft_gt(today / average, mult, tol=mult * 0.4)


@condition(
    "market_cap", "시가총액", PROFILE,
    params=(
        _p("low", "최소 (억원)", "float", default=500.0, min=10.0, max=5_000_000.0, step=100.0),
        _p("high", "최대 (억원)", "float", default=20000.0, min=50.0, max=5_000_000.0, step=100.0),
    ),
    description="시가총액이 범위 안. 작은 종목일수록 변동성이 크다.",
    min_bars=1,
)
def market_cap(ctx: Ctx, low: float, high: float) -> float:
    marcap = ctx.profile("marcap")
    if marcap is None or marcap <= 0:
        return 0.0
    return soft_between(marcap / 1e8, low, high)


@condition(
    "float_ratio", "유통주식 비율", PROFILE,
    params=(
        _p("low", "최소 (%)", "float", default=30.0, min=1.0, max=100.0, step=1.0),
        _p("high", "최대 (%)", "float", default=100.0, min=1.0, max=100.0, step=1.0),
    ),
    description="실제로 시장에서 돌아다니는 물량의 비율. 낮으면 잠긴 물량이 많다.",
    min_bars=1,
)
def float_ratio(ctx: Ctx, low: float, high: float) -> float:
    shares = ctx.profile("shares")
    floating = ctx.profile("float_shares")
    if not shares or not floating or shares <= 0:
        return 0.0
    return soft_between(floating / shares * 100.0, low, high)


@condition(
    "share_turnover", "회전율", VOLUME,
    params=(
        _p("low", "최소 (%)", "float", default=0.5, min=0.01, max=100.0, step=0.1),
        _p("high", "최대 (%)", "float", default=20.0, min=0.1, max=500.0, step=1.0),
        _p("period", "평균 낼 일수", default=20, min=1, max=120),
    ),
    description="상장주식수 대비 하루 거래량 비율. 너무 낮으면 못 팔고, 너무 높으면 과열이다.",
    min_bars=5,
)
def share_turnover(ctx: Ctx, low: float, high: float, period: int) -> float:
    shares = ctx.profile("shares")
    volume = ctx.last(ctx.volume.rolling(period).mean())
    if not shares or shares <= 0 or volume is None:
        return 0.0
    return soft_between(volume / shares * 100.0, low, high)


@condition(
    "major_holder", "대주주 지분율", PROFILE,
    params=(
        _p("low", "최소 (%)", "float", default=30.0, min=0.0, max=100.0, step=1.0),
        _p("high", "최대 (%)", "float", default=70.0, min=0.0, max=100.0, step=1.0),
    ),
    description="최대주주+특수관계인 지분율. 너무 낮으면 경영 불안, 너무 높으면 물량이 잠긴다.",
    min_bars=1,
)
def major_holder(ctx: Ctx, low: float, high: float) -> float:
    pct = ctx.profile("major_pct")
    if pct is None:
        return 0.0
    return soft_between(pct, low, high)


@condition(
    "foreign_holding", "외국인 보유비중", PROFILE,
    params=(_p("min_pct", "최소 (%)", "float", default=5.0, min=0.0, max=100.0, step=1.0),),
    description="외국인이 들고 있는 비중. 높으면 그만큼 검증된 종목으로 본다.",
    min_bars=1,
)
def foreign_holding(ctx: Ctx, min_pct: float) -> float:
    pct = ctx.profile("foreign_pct")
    if pct is None:
        return 0.0
    return soft_gt(pct, min_pct, tol=max(min_pct * 0.6, 2.0))


@condition(
    "net_buy_ratio", "수급 누적 매집 비중", FLOW,
    params=(
        _p("who", "대상", "choice", default="both", choices=("foreign", "inst", "both")),
        _p("days", "누적 일수", default=20, min=3, max=250),
        _p("min_pct", "최소 비중 (%)", "float", default=0.5, min=0.0, max=50.0, step=0.1),
    ),
    description="N일 누적 순매수를 상장주식수로 나눈 값. 절대 주식 수는 종목 크기에 "
                "따라 의미가 달라지므로(10만주는 소형주엔 대량, 대형주엔 미미) "
                "비중으로 본다. 상장주식수가 필요하다 (--profiles).",
    min_bars=10,
)
def net_buy_ratio(ctx: Ctx, who: str, days: int, min_pct: float) -> float:
    shares = ctx.profile("shares")
    net = _net_sum(ctx, who, days)
    if not shares or shares <= 0 or net is None:
        return 0.0
    return soft_gt(net / shares * 100.0, min_pct, tol=max(min_pct * 0.5, 0.1))


@condition(
    "net_buy_value", "수급 누적 순매수 금액", FLOW,
    params=(
        _p("who", "대상", "choice", default="both", choices=("foreign", "inst", "both")),
        _p("days", "누적 일수", default=20, min=3, max=250),
        _p("min_value", "최소 금액 (억원)", "float",
           default=100.0, min=1.0, max=100000.0, step=10.0),
    ),
    description="N일 누적 순매수 주식 수에 종가를 곱한 금액. 주식 수보다 실제로 "
                "얼마의 돈이 들어왔는지를 본다.",
    min_bars=10,
)
def net_buy_value(ctx: Ctx, who: str, days: int, min_value: float) -> float:
    net = _net_sum(ctx, who, days)
    price = ctx.last(ctx.close)
    if net is None or price is None or price <= 0:
        return 0.0
    return soft_gt(net * price / 1e8, min_value, tol=max(min_value * 0.4, 10.0))


@condition(
    "both_net_buy", "외국인·기관 쌍끌이", FLOW,
    params=(_p("days", "확인 일수", default=5, min=1, max=60),),
    description="최근 N일 중 외국인과 기관이 '같은 날 함께' 순매수한 날의 비율. "
                "한쪽이 사고 다른 쪽이 파는 것과 둘이 같이 담는 것은 다르다.",
    min_bars=10,
)
def both_net_buy(ctx: Ctx, days: int) -> float:
    foreign, inst = ctx.flow("foreign_net"), ctx.flow("inst_net")
    if foreign is None or inst is None:
        return 0.0
    frame = pd.concat([foreign, inst], axis=1).dropna().tail(days)
    if len(frame) < days:
        return 0.0
    both = (frame.iloc[:, 0] > 0) & (frame.iloc[:, 1] > 0)
    return float(both.sum()) / days


@condition(
    "net_buy_accelerating", "수급 매집 가속", FLOW,
    params=(
        _p("who", "대상", "choice", default="both", choices=("foreign", "inst", "both")),
        _p("short", "최근 일수", default=5, min=2, max=60),
        _p("long", "비교 일수", default=20, min=5, max=250),
        _p("mult", "최소 배수", "float", default=1.5, min=1.0, max=20.0, step=0.1),
    ),
    description="최근 며칠의 하루평균 순매수가 그 전 기간 평균의 N배 이상. "
                "꾸준히 담던 것에서 담는 속도가 붙었는지를 본다.",
    min_bars=30,
)
def net_buy_accelerating(ctx: Ctx, who: str, short: int, long: int, mult: float) -> float:
    if short >= long:
        return 0.0
    recent = _net_sum(ctx, who, short)
    baseline = _net_sum(ctx, who, long)
    if recent is None or baseline is None or baseline <= 0:
        return 0.0
    # 하루평균끼리 비교한다 (기간이 다르므로 합계로 비교하면 안 된다)
    ratio = (recent / short) / (baseline / long)
    # 허용폭은 배수 자체가 아니라 '1배를 넘어 요구한 증가분' 에 비례해야 한다.
    # mult*0.4 로 잡으면 가속이 전혀 없는 1.0배 종목이 0.21점을 받는다.
    return soft_gt(ratio, mult, tol=max((mult - 1.0) * 0.5, 0.05))


@condition(
    "low_short_ratio", "공매도 비중 낮음", PROFILE,
    params=(_p("max_pct", "최대 (%)", "float", default=3.0, min=0.0, max=50.0, step=0.5),),
    description="거래량 대비 공매도 비중이 낮을수록 좋다. 높으면 하락에 베팅한 물량이 많다.",
    min_bars=1,
)
def low_short_ratio(ctx: Ctx, max_pct: float) -> float:
    pct = ctx.profile("short_ratio")
    if pct is None:
        return 0.0
    return soft_lt(pct, max_pct, tol=max(max_pct * 0.8, 1.0))


@condition(
    "low_short_balance", "공매도 잔고 부담 낮음", PROFILE,
    params=(_p("max_pct", "최대 (%)", "float", default=2.0, min=0.0, max=50.0, step=0.5),),
    description="상장주식수 대비 아직 안 갚은 공매도 잔고 비중. "
                "대차잔고 자체는 거래소 회원사만 볼 수 있어 이걸로 본다.",
    min_bars=1,
)
def low_short_balance(ctx: Ctx, max_pct: float) -> float:
    pct = ctx.profile("loan_ratio")
    if pct is None:
        return 0.0
    return soft_lt(pct, max_pct, tol=max(max_pct * 0.8, 1.0))


@condition(
    "low_dilution", "잠재 희석 물량 적음", PROFILE,
    params=(_p("max_pct", "최대 (%)", "float", default=5.0, min=0.0, max=100.0, step=1.0),),
    description="전환사채·신주인수권부사채·유상증자로 새로 풀릴 수 있는 물량의 비중. 많으면 주식 수가 늘어나 기존 주주 몫이 줄어든다.",
    min_bars=1,
)
def low_dilution(ctx: Ctx, max_pct: float) -> float:
    pct = ctx.profile("dilution_pct")
    if pct is None:
        return 0.0
    return soft_lt(pct, max_pct, tol=max(max_pct * 0.8, 2.0))


# --------------------------------------------------------------------------- 상장폐지 위험
#
# 관리종목·상장폐지 요건 상당수는 숫자로 정해져 있어 기계적으로 확인된다.
# 여기 조건들은 "좋은 종목 고르기" 가 아니라 "탈락 위험 종목 빼기" 용이다.
# 기준 수치는 바뀌므로 거래소 공시로 확인해야 한다.
#
# 이 조건들로 볼 수 없는 것: 감사의견(의견거절·부적정), 관리종목 지정 여부,
# 주식분산 요건. 감사의견은 실제 상장폐지 사유 중 큰 비중이므로, 이 필터를
# 통과했다고 안전하다고 볼 수 없다.


@condition(
    "revenue_floor", "매출액 하한", FUNDAMENTAL,
    params=(
        _p("min_revenue", "최소 매출액 (억원)", "float",
           default=100.0, min=1.0, max=100000.0, step=10.0),
        _p("years", "확인 연수", default=1, min=1, max=10),
    ),
    description="매출액이 기준 이상. 코스피 50억·코스닥 30억 미만은 관리종목 지정 "
                "사유이므로, 여유를 두고 그 위를 본다.",
    min_bars=1,
)
def revenue_floor(ctx: Ctx, min_revenue: float, years: int) -> float:
    series = ctx.fundamental("revenue", years)
    if series is None:
        return 0.0
    # 네이버·DART 매출액 단위는 억원이다
    return soft_gt(float(series.min()), min_revenue, tol=max(min_revenue * 0.5, 10.0))


@condition(
    "no_capital_impairment", "자본잠식 아님", FUNDAMENTAL,
    params=(
        _p("min_reserve", "최소 유보율 (%)", "float",
           default=100.0, min=-100.0, max=5000.0, step=50.0),
        _p("years", "확인 연수", default=1, min=1, max=10),
    ),
    description="유보율이 기준 이상. 유보율이 음수면 결손금이 자본금을 깎아먹은 "
                "자본잠식 상태다. 자본금 50% 이상 잠식은 관리종목, 전액 잠식은 "
                "상장폐지 사유다.",
    min_bars=1,
)
def no_capital_impairment(ctx: Ctx, min_reserve: float, years: int) -> float:
    series = ctx.fundamental("reserve_ratio", years)
    if series is None:
        return 0.0
    worst = float(series.min())
    # 유보율이 음수면 이미 자본잠식이다. 이건 기준에 '가까운' 상태가 아니라
    # 법적으로 구분되는 상태이므로 근접 점수를 주지 않고 0으로 자른다.
    if worst < 0:
        return 0.0
    return soft_gt(worst, min_reserve, tol=max(abs(min_reserve), 50.0))


@condition(
    "no_operating_loss_streak", "영업손실 연속 아님", FUNDAMENTAL,
    params=(_p("years", "확인 연수", default=3, min=2, max=10),),
    description="최근 N년 중 영업이익이 흑자인 해의 비율. 코스닥은 4년 연속 "
                "영업손실이면 관리종목이다. 네이버는 3개 연도만 주므로 4년 확인은 "
                "DART 가 있어야 한다.",
    min_bars=1,
)
def no_operating_loss_streak(ctx: Ctx, years: int) -> float:
    series = ctx.fundamental("operating_income", years)
    if series is None or len(series) < 2:
        return 0.0
    return float((series > 0).sum()) / len(series)


# --------------------------------------------------------------------------- 유동성·위치·상대강도


@condition(
    "turnover_to_marcap", "시총 대비 거래대금", PROFILE,
    params=(
        _p("min_pct", "최소 (%)", "float", default=1.0, min=0.01, max=100.0, step=0.1),
        _p("period", "평균 낼 일수", default=20, min=1, max=120),
    ),
    description="하루 거래대금을 시가총액으로 나눈 값. 시총 1조에 거래대금 50억(0.05%)인 "
                "종목과 시총 2천억에 500억(2.5%)인 종목은 움직임이 전혀 다르다.",
    min_bars=5,
)
def turnover_to_marcap(ctx: Ctx, min_pct: float, period: int) -> float:
    marcap = ctx.profile("marcap")
    turnover = ctx.last(ctx.turnover(period))
    if not marcap or marcap <= 0 or turnover is None or turnover <= 0:
        return 0.0
    # 이 비율은 0.5%~25% 처럼 폭이 넓다. 허용폭을 넓게 잡으면 절반밖에 안 되는
    # 종목도 0.5점을 받아 기준을 세운 의미가 없어진다.
    return soft_gt(turnover / marcap * 100.0, min_pct, tol=max(min_pct * 0.3, 0.05))


@condition(
    "price_position", "기간 내 주가 위치", POSITION,
    params=(
        _p("period", "기준 일수", default=252, min=20, max=1000),
        _p("low", "최소 위치 (%)", "float", default=0.0, min=0.0, max=100.0, step=5.0),
        _p("high", "최대 위치 (%)", "float", default=30.0, min=0.0, max=100.0, step=5.0),
    ),
    description="52주 최저~최고를 0~100으로 보고 지금 어디인지. 0에 가까우면 바닥권, "
                "100에 가까우면 신고가권. 바닥 반등과 신고가 돌파는 전략이 다르므로 "
                "구간을 직접 정한다.",
    min_bars=30,
)
def price_position(ctx: Ctx, period: int, low: float, high: float) -> float:
    window = ctx.close.tail(period)
    if len(window) < 20:
        return 0.0
    bottom, top = float(window.min()), float(window.max())
    if top <= bottom:
        return 0.0
    position = (float(ctx.close.iloc[-1]) - bottom) / (top - bottom) * 100.0
    return soft_between(position, low, high)


@condition(
    "relative_strength", "지수 대비 상대강도", MOMENTUM,
    params=(
        _p("period", "비교 일수", default=20, min=3, max=250),
        _p("min_excess", "최소 초과수익 (%p)", "float",
           default=0.0, min=-50.0, max=100.0, step=1.0),
    ),
    description="같은 기간 코스피/코스닥 지수보다 얼마나 더 올랐는지. 시장이 오를 때 "
                "덜 오른 종목과 시장이 빠질 때 버틴 종목을 구분한다.",
    min_bars=10,
)
def relative_strength(ctx: Ctx, period: int, min_excess: float) -> float:
    own = ctx.own_return(period)
    market = ctx.benchmark_return(period)
    if own is None or market is None:
        return 0.0
    return soft_gt(own - market, min_excess, tol=max(abs(min_excess) * 0.5, 3.0))
