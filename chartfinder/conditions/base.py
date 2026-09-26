"""조건 정의 규약과 레지스트리.

조건 하나는 `Ctx`(종목의 일봉 + 지표 캐시)를 받아 0~1 점수를 돌려주는 함수다.
파라미터를 스키마로 선언해두면 CLI 옵션과 Streamlit 위젯이 여기서 자동 생성된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from .. import indicators as ind

#: 지표 계산에 필요한 최소 봉 수
MIN_BARS = 30


class Ctx:
    """종목 하나의 일봉과 지표 계산 결과 캐시.

    여러 조건이 같은 지표(예: MA20)를 요구해도 한 번만 계산한다.
    재무 데이터(fundamentals)는 있을 때만 채워진다.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        fundamentals: pd.DataFrame | None = None,
        profile: dict[str, float] | None = None,
    ) -> None:
        self.df = df
        self.fundamentals = fundamentals
        self._profile = profile or {}
        self._memo: dict[tuple, Any] = {}

    # ---------------------------------------------------------------- 기본 시세
    @property
    def close(self) -> pd.Series:
        return self.df["close"]

    @property
    def high(self) -> pd.Series:
        return self.df["high"]

    @property
    def low(self) -> pd.Series:
        return self.df["low"]

    @property
    def volume(self) -> pd.Series:
        return self.df["volume"]

    @property
    def bars(self) -> int:
        return len(self.df)

    def fundamental(self, name: str, years: int | None = None) -> pd.Series | None:
        """재무 항목 시계열 (연도 오름차순). 없으면 None.

        years 를 주면 최근 N개만. 결측 연도는 제외한다.
        """
        if self.fundamentals is None or self.fundamentals.empty:
            return None
        if name not in self.fundamentals.columns:
            return None
        series = pd.to_numeric(self.fundamentals[name], errors="coerce").dropna()
        if series.empty:
            return None
        return series.tail(years) if years else series

    def profile(self, name: str) -> float | None:
        """종목 정보 스냅샷 값 (시가총액·주식수·지분율 등). 없으면 None.

        `chartfinder update --profiles` 를 돌리지 않았거나, 그 항목을 주는
        출처가 막혀 있으면 None 이다. 조건은 None 을 0점으로 돌려야 한다.
        """
        value = self._profile.get(name)
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(value) else value

    def turnover(self, period: int = 1) -> pd.Series:
        """거래대금 (종가 × 거래량). period 를 주면 그 기간 평균."""
        return self._memoized(
            ("turnover", period),
            lambda: (self.close * self.volume).rolling(period).mean()
            if period > 1
            else self.close * self.volume,
        )

    def flow(self, name: str) -> pd.Series | None:
        """투자자별 순매수 컬럼 (foreign_net / inst_net / indi_net).

        수급을 받지 않은 캐시나 지원하지 않는 시장에서는 None.
        """
        if name not in self.df.columns:
            return None
        series = pd.to_numeric(self.df[name], errors="coerce")
        return None if series.dropna().empty else series

    # ---------------------------------------------------------------- 지표 (메모이즈)
    def _memoized(self, key: tuple, fn: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def ma(self, period: int) -> pd.Series:
        return self._memoized(("ma", period), lambda: ind.sma(self.close, period))

    def ema(self, period: int) -> pd.Series:
        return self._memoized(("ema", period), lambda: ind.ema(self.close, period))

    def rsi(self, period: int = 14) -> pd.Series:
        return self._memoized(("rsi", period), lambda: ind.rsi(self.close, period))

    def macd(self, fast: int = 12, slow: int = 26, signal: int = 9):
        return self._memoized(
            ("macd", fast, slow, signal), lambda: ind.macd(self.close, fast, slow, signal)
        )

    def bollinger(self, period: int = 20, mult: float = 2.0):
        return self._memoized(
            ("boll", period, mult), lambda: ind.bollinger(self.close, period, mult)
        )

    def band_width(self, period: int = 20, mult: float = 2.0) -> pd.Series:
        return self._memoized(
            ("bw", period, mult), lambda: ind.band_width(self.close, period, mult)
        )

    def dmi(self, period: int = 14, adx_period: int = 14):
        return self._memoized(
            ("dmi", period, adx_period), lambda: ind.dmi(self.df, period, adx_period)
        )

    def stochastic(self, period: int = 14, smooth_k: int = 3, smooth_d: int = 3):
        return self._memoized(
            ("stoch", period, smooth_k, smooth_d),
            lambda: ind.stochastic_slow(self.df, period, smooth_k, smooth_d),
        )

    def atr(self, period: int = 14) -> pd.Series:
        return self._memoized(("atr", period), lambda: ind.atr(self.df, period))

    def volume_ratio(self, period: int = 20) -> pd.Series:
        return self._memoized(
            ("vr", period), lambda: ind.volume_ratio(self.volume, period)
        )

    def trading_value(self, period: int = 20) -> pd.Series:
        return self._memoized(
            ("tv", period), lambda: ind.trading_value(self.df, period)
        )

    # ---------------------------------------------------------------- 유틸
    @staticmethod
    def last(series: pd.Series, offset: int = 0) -> float | None:
        """마지막 값(offset=1이면 전일). 값이 없으면 None."""
        if series is None or len(series) <= offset:
            return None
        value = series.iloc[-1 - offset]
        return None if pd.isna(value) else float(value)


@dataclass(frozen=True)
class Param:
    """조건 파라미터 스키마 (UI 위젯 / CLI 옵션 생성에 사용)."""

    name: str
    label: str
    type: str = "int"  # int | float | bool | choice
    default: Any = 0
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    help: str = ""

    def cast(self, raw: Any) -> Any:
        if self.type == "int":
            return int(float(raw))
        if self.type == "float":
            return float(raw)
        if self.type == "bool":
            if isinstance(raw, str):
                return raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}
            return bool(raw)
        return str(raw)


@dataclass(frozen=True)
class Condition:
    key: str
    label: str
    category: str
    fn: Callable[..., float]
    params: tuple[Param, ...] = ()
    description: str = ""
    min_bars: int = MIN_BARS

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def resolve(self, params: dict[str, Any] | None) -> dict[str, Any]:
        """사용자 입력을 기본값 위에 덮어쓰고 타입을 맞춘다."""
        values = self.defaults()
        schema = {p.name: p for p in self.params}
        for name, raw in (params or {}).items():
            if name not in schema:
                raise ValueError(f"'{self.key}' 조건에 없는 파라미터: {name}")
            values[name] = schema[name].cast(raw)
        return values

    def score(self, ctx: Ctx, params: dict[str, Any] | None = None) -> float:
        """0~1 점수. 데이터가 모자라거나 계산 불가면 0."""
        if ctx.bars < self.min_bars:
            return 0.0
        try:
            value = self.fn(ctx, **self.resolve(params))
        except (ValueError, TypeError, ZeroDivisionError, IndexError, KeyError):
            return 0.0
        if value is None or pd.isna(value):
            return 0.0
        return float(min(1.0, max(0.0, value)))


_REGISTRY: dict[str, Condition] = {}


def condition(
    key: str,
    label: str,
    category: str,
    params: tuple[Param, ...] = (),
    description: str = "",
    min_bars: int = MIN_BARS,
) -> Callable[[Callable[..., float]], Callable[..., float]]:
    """조건 등록 데코레이터."""

    def wrapper(fn: Callable[..., float]) -> Callable[..., float]:
        if key in _REGISTRY:
            raise ValueError(f"조건 키 중복: {key}")
        _REGISTRY[key] = Condition(
            key=key,
            label=label,
            category=category,
            fn=fn,
            params=params,
            description=description or (fn.__doc__ or "").strip(),
            min_bars=min_bars,
        )
        return fn

    return wrapper


def get(key: str) -> Condition:
    if key not in _REGISTRY:
        raise KeyError(f"알 수 없는 조건: {key}")
    return _REGISTRY[key]


def all_conditions() -> list[Condition]:
    return list(_REGISTRY.values())


def by_category() -> dict[str, list[Condition]]:
    grouped: dict[str, list[Condition]] = {}
    for cond in _REGISTRY.values():
        grouped.setdefault(cond.category, []).append(cond)
    return grouped
