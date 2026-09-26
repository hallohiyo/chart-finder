"""종목 정보(시가총액·주식수·지분율·공매도) 조건과 캐시."""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder import cache
from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx
from chartfinder.datasource import get_source
from chartfinder.datasource.base import PROFILE_FIELDS
from chartfinder.datasource.demo import generate, generate_profiles
from chartfinder.screener import ConditionSpec, screen, unscored_conditions


def _ctx(profile: dict | None = None, **overrides) -> Ctx:
    from datetime import date, timedelta

    end = date.today()
    df = generate("DEMO001", end - timedelta(days=400), end)
    for column, value in overrides.items():
        df[column] = value
    return Ctx(df, None, profile)


def _score(key: str, profile: dict | None = None, **params) -> float:
    condition = get_condition(key)
    merged = {p.name: p.default for p in condition.params} | params
    return condition.score(_ctx(profile), merged)


# --------------------------------------------------------------------- 거래대금


def test_turnover_uses_price_times_volume():
    ctx = Ctx(
        pd.DataFrame(
            {"open": [1, 1], "high": [1, 1], "low": [1, 1],
             "close": [1000.0, 2000.0], "volume": [100.0, 50.0]}
        )
    )
    assert list(ctx.turnover()) == [100_000.0, 100_000.0]


def test_turnover_value_scores_by_money_not_shares():
    """싼 주식이 많이 거래된 것과 비싼 주식이 적게 거래된 것을 구분한다."""
    cheap = Ctx(_frame(close=500.0, volume=100_000.0))   # 5천만원
    rich = Ctx(_frame(close=100_000.0, volume=100_000.0))  # 100억
    condition = get_condition("turnover_value")
    params = {"min_value": 50.0, "period": 5}
    assert condition.score(rich, params) > condition.score(cheap, params)


def _frame(close: float, volume: float, rows: int = 30) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="D")
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": volume},
        index=index,
    )


def test_turnover_surge_needs_a_jump_over_the_average():
    frame = _frame(close=1000.0, volume=10_000.0)
    quiet = Ctx(frame)
    spiked = frame.copy()
    spiked.iloc[-1, spiked.columns.get_loc("volume")] = 50_000.0
    condition = get_condition("turnover_surge")
    params = {"mult": 3.0, "period": 20}
    assert condition.score(quiet, params) < 0.2
    assert condition.score(Ctx(spiked), params) > 0.9


# --------------------------------------------------------------------- 스냅샷 조건


@pytest.mark.parametrize(
    "key, params",
    [
        ("market_cap", {}),
        ("float_ratio", {}),
        ("major_holder", {}),
        ("foreign_holding", {}),
        ("low_short_ratio", {}),
        ("low_short_balance", {}),
        ("low_dilution", {}),
        ("share_turnover", {}),
        ("inst_accumulation", {}),
    ],
)
def test_profile_conditions_score_zero_without_data(key, params):
    """스냅샷을 안 받았으면 0점이어야 한다 (엉뚱한 점수를 주면 순위가 망가진다)."""
    assert _score(key, None, **params) == 0.0


def test_market_cap_prefers_the_requested_band():
    small = _score("market_cap", {"marcap": 1_000 * 1e8}, low=500.0, high=2000.0)
    huge = _score("market_cap", {"marcap": 400_000 * 1e8}, low=500.0, high=2000.0)
    assert small == 1.0
    assert huge < 0.1


def test_float_ratio_needs_both_shares_and_float():
    assert _score("float_ratio", {"shares": 1e8}) == 0.0
    assert _score("float_ratio", {"shares": 1e8, "float_shares": 6e7},
                  low=50.0, high=100.0) == 1.0


def test_major_holder_flags_both_extremes():
    assert _score("major_holder", {"major_pct": 45.0}, low=30.0, high=60.0) == 1.0
    assert _score("major_holder", {"major_pct": 92.0}, low=30.0, high=60.0) < 0.3


def test_low_short_ratio_rewards_less_short_selling():
    quiet = _score("low_short_ratio", {"short_ratio": 0.5}, max_pct=3.0)
    heavy = _score("low_short_ratio", {"short_ratio": 25.0}, max_pct=3.0)
    assert quiet == 1.0
    assert heavy < 0.05


def test_low_dilution_treats_no_cb_as_perfect():
    assert _score("low_dilution", {"dilution_pct": 0.0}) == 1.0
    assert _score("low_dilution", {"dilution_pct": 40.0}) < 0.05


def test_inst_accumulation_needs_shares_and_flows():
    # 수급 컬럼은 있지만 상장주식수가 없으면 비율을 못 낸다
    assert _score("inst_accumulation", {}) == 0.0
    assert _score("inst_accumulation", {"shares": 1e6}, min_pct=0.1, days=60) > 0.0


# --------------------------------------------------------------------- 캐시


def test_profiles_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    frame = generate_profiles(["DEMO000", "DEMO001"])
    cache.save_profiles("demo", frame)

    loaded = cache.load_profiles("demo")
    assert loaded is not None
    assert list(loaded.index) == ["DEMO000", "DEMO001"]
    assert cache.has_profiles("demo")


def test_update_profiles_reports_which_fields_arrived(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()][:5]

    stats = cache.update_profiles("demo", symbols=symbols)

    assert stats["updated"] == 5
    assert stats["filled"]["marcap"] == 5
    # 항목이 하나라도 비면 어떤 게 비었는지 알려줘야 한다
    assert "missing" in stats


def test_update_profiles_skips_while_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()][:3]

    cache.update_profiles("demo", symbols=symbols)
    again = cache.update_profiles("demo", symbols=symbols)

    assert again["updated"] == 0
    assert again["skipped"] == 3


def test_unsupported_market_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    stats = cache.update_profiles("us", symbols=["AAPL"])
    assert "error" in stats


def test_profile_fields_all_have_a_label():
    from chartfinder.cli import _PROFILE_LABELS

    assert set(PROFILE_FIELDS) <= set(_PROFILE_LABELS)


# --------------------------------------------------------------------- 통합


def test_screen_uses_the_cached_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()][:12]
    cache.update("demo", symbols=symbols, years=2)
    cache.update_profiles("demo", symbols=symbols)

    # 합성 시가총액은 폭이 넓으므로 범위를 넉넉히 준다 (여기서 보려는 건
    # 스냅샷이 조건까지 전달되는지다)
    specs = [
        ConditionSpec("market_cap", {"low": 1.0, "high": 5_000_000.0}),
        ConditionSpec("low_dilution"),
    ]
    result = screen("demo", specs, tickers=cache.get_tickers("demo", "all")[:12])

    assert not result.empty
    # 스냅샷이 붙었으니 두 조건 모두 어딘가에서는 점수가 나야 한다
    assert unscored_conditions(result, specs) == []


def test_screen_without_the_snapshot_warns_instead_of_ranking_noise(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()][:8]
    cache.update("demo", symbols=symbols, years=2)

    specs = [ConditionSpec("market_cap", {"low": 1.0, "high": 5_000_000.0})]
    result = screen("demo", specs, tickers=cache.get_tickers("demo", "all")[:8])

    assert unscored_conditions(result, specs) == ["market_cap"]


def test_supply_preset_loads_and_declares_its_data_need():
    from chartfinder import presets as presets_mod

    preset = next(
        preset for _, preset in presets_mod.load_all() if "물량" in preset.name
    )
    assert preset.needs_profiles
    for spec in preset.conditions:
        get_condition(spec.key)  # 없는 조건을 가리키면 여기서 터진다
