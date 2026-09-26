"""시총 대비 거래대금 · 기간 내 주가 위치 · 지수 대비 상대강도."""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder import cache
from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx
from chartfinder.datasource import get_source


def _bars(closes: list[float], volume: float = 1000.0) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volume},
        index=index,
    )


def _score(key: str, ctx: Ctx, **params) -> float:
    condition = get_condition(key)
    return condition.score(ctx, {p.name: p.default for p in condition.params} | params)


# ------------------------------------------------------- 시총 대비 거래대금


def test_turnover_to_marcap_separates_a_big_and_a_small_cap():
    """시총 1조 · 거래대금 50억 과 시총 2천억 · 거래대금 500억 은 다르다."""
    # 종가 10,000원 × 50만주 = 50억
    heavy = Ctx(_bars([10_000.0] * 30, volume=500_000), profile={"marcap": 1_0000e8})
    # 종가 10,000원 × 500만주 = 500억
    lively = Ctx(_bars([10_000.0] * 30, volume=5_000_000), profile={"marcap": 2_000e8})

    assert _score("turnover_to_marcap", heavy, min_pct=1.0) < 0.2
    assert _score("turnover_to_marcap", lively, min_pct=1.0) == 1.0


def test_turnover_to_marcap_needs_the_snapshot():
    bars = Ctx(_bars([10_000.0] * 30, volume=5_000_000))
    assert _score("turnover_to_marcap", bars) == 0.0


# ------------------------------------------------------- 기간 내 주가 위치


def test_price_position_is_zero_at_the_low_and_full_at_the_high():
    rising = _bars([100.0 + i for i in range(100)])       # 마지막이 최고가
    falling = _bars([200.0 - i for i in range(100)])      # 마지막이 최저가

    # 바닥권(0~30%)을 찾는 설정
    assert _score("price_position", Ctx(falling), period=252, low=0.0, high=30.0) == 1.0
    assert _score("price_position", Ctx(rising), period=252, low=0.0, high=30.0) < 0.05

    # 신고가권(70~100%)을 찾는 설정 — 같은 조건, 구간만 반대
    assert _score("price_position", Ctx(rising), period=252, low=70.0, high=100.0) == 1.0
    assert _score("price_position", Ctx(falling), period=252, low=70.0, high=100.0) < 0.05


def test_price_position_handles_a_flat_series():
    """전 구간 같은 값이면 위치를 정의할 수 없다."""
    assert _score("price_position", Ctx(_bars([100.0] * 60))) == 0.0


# ------------------------------------------------------- 상대강도


def _with_index(stock: list[float], index: list[float]) -> Ctx:
    return Ctx(_bars(stock), benchmark=_bars(index))


def test_relative_strength_rewards_beating_the_index():
    # 종목 +20%, 지수 +5% → 초과 15%p
    strong = _with_index([100.0] * 10 + [120.0], [100.0] * 10 + [105.0])
    # 종목 +2%, 지수 +10% → 초과 -8%p
    weak = _with_index([100.0] * 10 + [102.0], [100.0] * 10 + [110.0])

    assert _score("relative_strength", strong, period=10, min_excess=5.0) == 1.0
    assert _score("relative_strength", weak, period=10, min_excess=5.0) < 0.05


def test_a_stock_that_falls_less_than_the_market_is_strong():
    """시장이 빠질 때 버틴 종목도 상대강도가 높다."""
    held = _with_index([100.0] * 10 + [98.0], [100.0] * 10 + [90.0])
    assert _score("relative_strength", held, period=10, min_excess=0.0) == 1.0


def test_relative_strength_is_zero_without_the_index():
    assert _score("relative_strength", Ctx(_bars([100.0] * 30)), period=10) == 0.0


def test_benchmark_is_cut_to_the_stocks_last_trading_day():
    """지수가 더 최신이면 기간이 어긋나 초과수익이 엉뚱해진다."""
    stock = _bars([100.0] * 10 + [110.0])
    index = _bars([100.0] * 10 + [105.0, 300.0, 400.0])  # 지수만 이틀 더 있다
    ctx = Ctx(stock, benchmark=index)
    # 종목 마지막 날(11번째)까지만 보면 지수는 +5% 여야 한다
    assert ctx.benchmark_return(10) == pytest.approx(5.0)


def test_benchmark_return_is_none_when_history_is_too_short():
    ctx = Ctx(_bars([100.0] * 30), benchmark=_bars([100.0] * 5))
    assert ctx.benchmark_return(20) is None


# ------------------------------------------------------- 지수 캐시


def test_indices_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    stats = cache.update_indices("demo", years=2)

    assert stats["updated"] == 1
    assert cache.has_indices("demo")
    frame = cache.load_index("demo", "DEMOIDX")
    assert frame is not None and "close" in frame.columns


def test_update_also_fetches_the_index(tmp_path, monkeypatch):
    """상대강도 조건이 조용히 0점이 되지 않도록 시세와 함께 받는다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()][:3]

    stats = cache.update("demo", symbols=symbols, years=1)

    assert stats["indices"] == 1
    assert cache.has_indices("demo")


def test_each_exchange_maps_to_its_own_index():
    source = get_source("kr")
    assert source.benchmark_for("KOSPI") == "KS11"
    assert source.benchmark_for("KOSDAQ") == "KQ11"
    # 코스피 종목을 코스닥 지수와 비교하면 안 된다
    assert source.benchmark_for("KOSPI") != source.benchmark_for("KOSDAQ")


def test_screen_feeds_the_matching_index(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder.screener import ConditionSpec, screen, unscored_conditions

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:12]
    cache.update("demo", symbols=symbols, years=2)

    specs = [ConditionSpec("relative_strength", {"period": 20, "min_excess": -50.0})]
    result = screen("demo", specs, tickers=cache.get_tickers("demo", "all")[:12])

    assert not result.empty
    assert unscored_conditions(result, specs) == []


# ------------------------------------------------------- 근접 vs 돌파


def _rising_then_stalling() -> pd.DataFrame:
    """고점을 찍고 그 바로 아래에서 횡보 — 근접이지만 못 뚫은 자리."""
    closes = [100.0 + i for i in range(80)]       # 100 → 179 까지 상승
    closes += [176.0] * 20                        # 고점 179 아래에서 막혀 횡보
    return _bars(closes)


def _breaking_out() -> pd.DataFrame:
    """박스 위로 실제로 넘어선 자리."""
    closes = [100.0] * 80 + [101.0, 103.0, 108.0]
    return _bars(closes)


def test_near_high_cannot_tell_a_breakout_from_a_stall():
    """'고점 5% 이내' 는 저항선 아래에서 막혀 돌아선 자리도 만점을 준다.

    이걸 돌파 신호로 쓰면 안 되는 이유다.
    """
    stalling = Ctx(_rising_then_stalling())
    # 돌파 조건과 같은 60일 창으로 견준다
    assert _score("near_high", stalling, period=60, max_gap=5.0) == 1.0


def test_breakout_high_rejects_the_stall():
    stalling = Ctx(_rising_then_stalling())
    breaking = Ctx(_breaking_out())

    assert _score("breakout_high", breaking, period=60, within=5) == 1.0
    assert _score("breakout_high", stalling, period=60, within=5) == 0.0


def test_high_strength_preset_confirms_an_actual_breakout():
    """신고가권 전략은 '근처' 가 아니라 '뚫었는지' 를 봐야 한다."""
    from chartfinder import presets as presets_mod

    preset = next(p for _, p in presets_mod.load_all() if "신고가권" in p.name)
    keys = {spec.key for spec in preset.conditions}
    assert "breakout_high" in keys
    # 돌파 확인이 위치 조건보다 무거워야 한다
    weights = {spec.key: spec.weight for spec in preset.conditions}
    assert weights["breakout_high"] > weights["price_position"]


def test_bottom_and_high_presets_do_not_share_conditions_that_contradict():
    """바닥권과 신고가권이 한 전략에 섞이면 점수가 평준화돼 순위가 죽는다."""
    from chartfinder import presets as presets_mod

    for _, preset in presets_mod.load_all():
        keys = {spec.key for spec in preset.conditions}
        bottom = keys & {"near_low", "rsi_oversold", "bb_lower_touch", "stoch_oversold"}
        top = keys & {"near_high", "breakout_high"}
        assert not (bottom and top), f"{preset.name}: {bottom} 와 {top} 가 섞여 있다"
