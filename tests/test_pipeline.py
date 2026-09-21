"""데모 데이터 소스로 캐시 → 스크린 전 과정을 검증 (네트워크 불필요)."""

from datetime import date, timedelta

import pytest

from chartfinder import cache, presets as presets_mod
from chartfinder.datasource import get_source
from chartfinder.screener import ConditionSpec, screen


@pytest.fixture
def demo_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    source = get_source("demo")
    tickers = source.list_tickers()[:12]
    end = date.today()
    for ticker in tickers:
        cache.save("demo", ticker.symbol,
                   source.fetch_ohlcv(ticker.symbol, end - timedelta(days=800), end))
    return tickers


def test_cached_prices_round_trip(demo_cache):
    df = cache.load("demo", demo_cache[0].symbol)
    assert df is not None and not df.empty
    assert list(df.columns)[:5] == ["open", "high", "low", "close", "volume"]
    assert df.index.is_monotonic_increasing


def test_merge_deduplicates_overlapping_rows(demo_cache):
    df = cache.load("demo", demo_cache[0].symbol)
    merged = cache.merge(df, df.tail(20))
    assert len(merged) == len(df)
    assert not merged.index.duplicated().any()


def test_screen_ranks_by_score(demo_cache):
    specs = [ConditionSpec("ma_alignment"), ConditionSpec("volume_surge", weight=2)]
    result = screen("demo", specs, tickers=demo_cache)
    assert not result.empty
    assert result["score"].is_monotonic_decreasing
    assert result["score"].between(0, 1).all()
    assert {"s_ma_alignment", "s_volume_surge"} <= set(result.columns)


def test_strict_mode_only_keeps_full_matches(demo_cache):
    specs = [ConditionSpec("ma_alignment"), ConditionSpec("rsi_oversold")]
    loose = screen("demo", specs, tickers=demo_cache)
    strict = screen("demo", specs, tickers=demo_cache, strict=True)
    assert len(strict) <= len(loose)
    assert (strict["score"] == 1.0).all()


def test_top_and_min_score_are_applied(demo_cache):
    specs = [ConditionSpec("above_ma")]
    result = screen("demo", specs, tickers=demo_cache, top=3, min_score=0.1)
    assert len(result) <= 3
    assert (result["score"] >= 0.1).all()


def test_screen_without_conditions_is_rejected(demo_cache):
    with pytest.raises(ValueError):
        screen("demo", [], tickers=demo_cache)


def test_shipped_presets_load_and_have_valid_conditions():
    paths = presets_mod.list_presets("presets")
    assert paths, "presets 폴더가 비어 있습니다"
    for path in paths:
        preset = presets_mod.load(path)
        assert preset.conditions
        for spec in preset.conditions:
            from chartfinder.conditions import get
            get(spec.key).resolve(spec.params)  # 파라미터 이름/타입 검증


def test_preset_round_trip(tmp_path):
    preset = presets_mod.Preset(
        name="테스트", market="demo", universe="all",
        conditions=[ConditionSpec("rsi_oversold", {"threshold": 25}, weight=2.0)],
    )
    path = presets_mod.save(preset, tmp_path / "t.yaml")
    loaded = presets_mod.load(path)
    assert loaded.name == "테스트"
    assert loaded.conditions[0].params == {"threshold": 25}
    assert loaded.conditions[0].weight == 2.0


def test_universes_are_declared_per_market():
    from chartfinder.datasource import MARKETS, default_universe, universes

    for market in MARKETS:
        allowed = universes(market)
        assert allowed, market
        assert default_universe(market) in allowed


def test_demo_source_rejects_unknown_universe():
    with pytest.raises(ValueError):
        get_source("demo").list_tickers("sp500")


def test_demo_cache_carries_flow_columns(demo_cache):
    df = cache.load("demo", demo_cache[0].symbol)
    assert {"foreign_net", "inst_net"} <= set(df.columns)


def test_attach_flows_preserves_values_outside_fetched_range(demo_cache):
    """증분 갱신이 예전 수급 값을 지우지 않아야 한다."""
    import pandas as pd

    symbol = demo_cache[0].symbol
    df = cache.load("demo", symbol)
    original_first = float(df["foreign_net"].iloc[0])

    class RecentOnly:
        """최근 5일치 수급만 돌려주는 가짜 소스."""

        def fetch_flows(self, sym, start, end):
            recent = df.tail(5)
            return pd.DataFrame({"foreign_net": [999.0] * 5}, index=recent.index)

    updated = cache.attach_flows(RecentOnly(), symbol, df.copy(), date.today(), date.today())
    assert float(updated["foreign_net"].iloc[0]) == original_first
    assert float(updated["foreign_net"].iloc[-1]) == 999.0


def test_flow_preset_runs_on_demo_data(demo_cache):
    preset = presets_mod.load("presets/bottom_reversal.yaml")
    result = screen("demo", preset.conditions, tickers=demo_cache)
    assert not result.empty
    assert result["score"].between(0, 1).all()
    # 수급 조건도 실제로 채점된다 (demo 는 합성 수급을 갖고 있다)
    assert result["s_foreign_net_buy"].max() > 0
