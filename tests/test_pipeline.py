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

    updated, applied = cache.attach_flows(
        RecentOnly(), symbol, df.copy(), date.today(), date.today()
    )
    assert applied == 5
    assert float(updated["foreign_net"].iloc[0]) == original_first
    assert float(updated["foreign_net"].iloc[-1]) == 999.0


def test_flow_preset_runs_on_demo_data(demo_cache):
    preset = presets_mod.load("presets/bottom_reversal.yaml")
    result = screen("demo", preset.conditions, tickers=demo_cache)
    assert not result.empty
    assert result["score"].between(0, 1).all()
    # 수급 조건도 실제로 채점된다 (demo 는 합성 수급을 갖고 있다)
    assert result["s_foreign_net_buy"].max() > 0


def test_update_reports_why_flows_failed(tmp_path, monkeypatch):
    """수급 실패를 조용히 삼키지 말고 이유를 올려보내야 한다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))

    from chartfinder.datasource import get_source

    source = get_source("demo")
    monkeypatch.setattr(
        source, "fetch_flows",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pykrx 가 필요합니다")),
        raising=False,
    )
    symbols = [t.symbol for t in source.list_tickers()][:2]
    stats = cache.update("demo", symbols=symbols, years=1, flows=True)

    assert stats["updated"] == 2       # 시세는 정상 저장
    assert stats["flows"] == 0
    assert "pykrx" in str(stats["flow_error"])


def test_update_without_flows_reports_no_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder.datasource import get_source

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:2]
    stats = cache.update("demo", symbols=symbols, years=1, flows=False)
    assert "flow_error" not in stats


def test_flows_are_fetched_even_when_prices_are_current(tmp_path, monkeypatch):
    """시세가 이미 최신이어도 --flows 로 수급만 채울 수 있어야 한다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder.datasource import get_source

    source = get_source("demo")
    symbols = [t.symbol for t in source.list_tickers()][:3]

    # 1차: 수급 없이 시세만 받는다
    first = cache.update("demo", symbols=symbols, years=1, flows=False)
    assert first["updated"] == 3
    stripped = cache.load("demo", symbols[0]).drop(columns=["foreign_net", "inst_net", "indi_net"])
    for sym in symbols:
        df = cache.load("demo", sym)
        cache.save("demo", sym, df.drop(columns=[c for c in df.columns if c.endswith("_net")]))

    # 2차: 시세는 최신이므로 건너뛰지만 수급은 채워야 한다
    second = cache.update("demo", symbols=symbols, years=1, flows=True)
    assert second["flows"] == 3
    assert second["skipped"] == 0
    assert "foreign_net" in cache.load("demo", symbols[0]).columns
    assert stripped is not None  # 원본 시세 컬럼은 그대로


def test_flows_up_to_date_detects_missing_and_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder.datasource import get_source

    source = get_source("demo")
    symbol = source.list_tickers()[0].symbol
    df = source.fetch_ohlcv(symbol, date.today() - timedelta(days=400), date.today())
    last = df.index[-1].date()

    assert cache.flows_up_to_date(df, last) is True
    assert cache.flows_up_to_date(df.drop(columns=["foreign_net", "inst_net", "indi_net"]), last) is False

    stale = df.copy()
    stale.loc[stale.index[-3:], ["foreign_net", "inst_net", "indi_net"]] = None
    assert cache.flows_up_to_date(stale, last) is False


def test_third_run_skips_when_flows_already_present(tmp_path, monkeypatch):
    """수급까지 최신이면 다시 받지 않는다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder.datasource import get_source

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:2]
    cache.update("demo", symbols=symbols, years=1, flows=True)
    again = cache.update("demo", symbols=symbols, years=1, flows=True)
    assert again["skipped"] == 2
    assert again["flows"] == 0


def test_empty_flow_response_is_not_counted_as_success(tmp_path, monkeypatch):
    """수급을 못 받았는데 성공으로 세면 안 된다 (조용한 실패 방지)."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    import pandas as pd

    from chartfinder.datasource import get_source

    source = get_source("demo")
    monkeypatch.setattr(source, "fetch_flows", lambda *a, **k: pd.DataFrame(), raising=False)
    symbols = [t.symbol for t in source.list_tickers()][:2]

    stats = cache.update("demo", symbols=symbols, years=1, flows=True)
    assert stats["updated"] == 2
    assert stats["flows"] == 0
    assert "비어" in str(stats["flow_error"])


def test_flow_only_refresh_produces_scorable_data(tmp_path, monkeypatch):
    """시세만 있는 캐시에 수급을 채우면 수급 조건이 실제로 채점돼야 한다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder.datasource import get_source
    from chartfinder.screener import ConditionSpec

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:5]
    cache.update("demo", symbols=symbols, years=1, flows=False)
    for sym in symbols:  # 수급 컬럼을 지워 '시세만 받은 캐시'를 만든다
        df = cache.load("demo", sym)
        cache.save("demo", sym, df.drop(columns=[c for c in df.columns if c.endswith("_net")]))

    stats = cache.update("demo", symbols=symbols, years=1, flows=True)
    assert stats["flows"] == 5

    tickers = [t for t in get_source("demo").list_tickers() if t.symbol in symbols]
    result = screen("demo", [ConditionSpec("foreign_net_buy")], tickers=tickers)
    assert result["s_foreign_net_buy"].max() > 0


def test_price_and_flow_rows_line_up(tmp_path, monkeypatch):
    """조회 종료일이 어긋나면 마지막 행 수급이 비어버린다 (회귀 방지)."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder.datasource import get_source

    symbol = get_source("demo").list_tickers()[0].symbol
    cache.update("demo", symbols=[symbol], years=1, flows=True)

    df = cache.load("demo", symbol)
    # 당일치는 아직 공시 전일 수 있으므로 마지막 한 행까지는 비어도 된다
    assert df["foreign_net"].tail(2).notna().any()
    assert df["foreign_net"].iloc[:-1].notna().all()
