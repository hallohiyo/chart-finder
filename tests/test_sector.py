"""업종 강도·업종 내 상대강도.

종목 하나만 봐서는 업종 강도를 알 수 없다. 화면이 같은 업종 종목들의
수익률을 미리 집계해 Ctx 에 넘겨준다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder import cache
from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx, SectorStats
from chartfinder.datasource import get_source
from chartfinder.screener import ConditionSpec, _sector_stats, screen, unscored_conditions


def _bars(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1e6},
        index=index,
    )


def _score(key: str, ctx: Ctx, **params) -> float:
    condition = get_condition(key)
    return condition.score(ctx, {p.name: p.default for p in condition.params} | params)


def _ctx(own: list[float], sector_return: float, size: int = 20,
         market_return: float = 0.0) -> Ctx:
    bars = _bars(own)
    index = pd.date_range("2024-01-01", periods=len(own), freq="D")
    base = 100.0
    market = _bars([base] * (len(own) - 1) + [base * (1 + market_return / 100)])
    market.index = index
    return Ctx(
        bars, None, None, market,
        SectorStats("반도체", size, {20: sector_return}),
    )


# ------------------------------------------------------------- 업종 강도


def test_sector_strength_compares_the_sector_to_the_index():
    """업종이 무너지는 와중에 혼자 버티는 종목을 고르지 않으려면 이걸 본다."""
    own = [100.0] * 20 + [110.0]
    hot = _ctx(own, sector_return=8.0, market_return=1.0)     # 업종 +8%, 지수 +1%
    cold = _ctx(own, sector_return=-6.0, market_return=1.0)   # 업종 -6%, 지수 +1%

    assert _score("sector_strength", hot, period=20, min_excess=0.0) == 1.0
    assert _score("sector_strength", cold, period=20, min_excess=0.0) < 0.05


def test_sector_strength_needs_both_the_sector_and_the_index():
    own = [100.0] * 20 + [110.0]
    no_sector = Ctx(_bars(own), None, None, _bars([100.0] * 21), None)
    assert _score("sector_strength", no_sector, period=20) == 0.0

    no_index = Ctx(_bars(own), None, None, None, SectorStats("반도체", 20, {20: 8.0}))
    assert _score("sector_strength", no_index, period=20) == 0.0


def test_a_tiny_sector_is_not_worth_comparing():
    """업종에 종목이 몇 개뿐이면 중간값이 의미 없다."""
    own = [100.0] * 20 + [110.0]
    tiny = _ctx(own, sector_return=8.0, size=3, market_return=1.0)
    assert _score("sector_strength", tiny, period=20) == 0.0


# ------------------------------------------------------- 업종 내 상대강도


def test_sector_relative_strength_separates_a_leader_from_a_follower():
    """업종이 다 같이 오른 것과 그 안에서 앞서 가는 것은 다르다."""
    leader = _ctx([100.0] * 20 + [130.0], sector_return=10.0)   # 종목 +30%, 업종 +10%
    follower = _ctx([100.0] * 20 + [103.0], sector_return=10.0)  # 종목 +3%, 업종 +10%

    assert _score("sector_relative_strength", leader, period=20, min_excess=5.0) == 1.0
    assert _score("sector_relative_strength", follower, period=20, min_excess=5.0) < 0.05


def test_sector_relative_strength_is_zero_without_sector_data():
    plain = Ctx(_bars([100.0] * 20 + [130.0]))
    assert _score("sector_relative_strength", plain, period=20) == 0.0


# ------------------------------------------------------------- 집계


def test_sector_stats_uses_the_median_not_the_mean(tmp_path, monkeypatch):
    """업종에 급등주 하나가 섞이면 평균이 끌려간다."""
    stats = SectorStats("반도체", 5, {20: 3.0})
    assert stats.median_return(20) == 3.0
    assert stats.median_return(60) is None


def test_sector_stats_are_only_computed_when_a_sector_condition_is_used(tmp_path, monkeypatch):
    """업종 조건이 없으면 종목 수만큼 파일을 읽을 이유가 없다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()][:10]
    cache.update("demo", symbols=symbols, years=1)
    tickers = cache.get_tickers("demo", "all", refresh=True)[:10]

    assert _sector_stats("demo", tickers, [ConditionSpec("above_ma")]) == {}
    assert _sector_stats("demo", tickers, [ConditionSpec("sector_strength")]) != {}


def test_screen_feeds_the_matching_sector(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()]
    cache.update("demo", symbols=symbols, years=2)
    tickers = cache.get_tickers("demo", "all", refresh=True)

    specs = [ConditionSpec("sector_relative_strength", {"period": 20, "min_excess": -50.0})]
    result = screen("demo", specs, tickers=tickers)
    assert not result.empty
    assert unscored_conditions(result, specs) == []


# ------------------------------------------------------------- 캐시 판 갱신


def test_old_ticker_cache_is_refetched_when_the_schema_changes(tmp_path, monkeypatch):
    """업종 필드가 없던 캐시를 그대로 쓰면 업종 조건이 조용히 0점이 된다."""
    import json

    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    folder = tmp_path / "tickers"
    folder.mkdir(parents=True)
    (folder / "demo_all.json").write_text(
        json.dumps({
            "fetched_at": pd.Timestamp.today().date().isoformat(),
            "tickers": [{"symbol": "DEMO000", "name": "옛캐시", "market": "demo",
                         "exchange": "DEMO", "marcap": None}],
        }),
        encoding="utf-8",
    )

    tickers = cache.get_tickers("demo", "all")
    assert len(tickers) > 1, "옛 캐시를 그대로 썼다"
    assert all(t.sector for t in tickers)


def test_unknown_keys_in_a_cached_ticker_do_not_crash(tmp_path, monkeypatch):
    """앞으로 항목이 빠져도 터지지 않아야 한다."""
    import json

    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    folder = tmp_path / "tickers"
    folder.mkdir(parents=True)
    (folder / "demo_all.json").write_text(
        json.dumps({
            "schema": cache.TICKER_SCHEMA,
            "fetched_at": pd.Timestamp.today().date().isoformat(),
            "tickers": [{"symbol": "A", "name": "가", "market": "demo",
                         "sector": "반도체", "옛날항목": 1}],
        }),
        encoding="utf-8",
    )
    tickers = cache.get_tickers("demo", "all")
    assert tickers[0].sector == "반도체"


# ------------------------------------------------------------- 프리셋


def test_sector_leader_preset_is_complete():
    from chartfinder import presets as presets_mod

    preset = next(p for _, p in presets_mod.load_all() if "업종 주도주" in p.name)
    assert preset.market == "kr"
    keys = {spec.key for spec in preset.conditions}
    assert {"sector_strength", "sector_relative_strength"} <= keys
    for spec in preset.conditions:
        get_condition(spec.key)
