"""백테스트 테스트.

가장 중요한 두 가지:
  1. 기준일 이후의 정보를 쓰지 않는가 (미래 참조)
  2. 진짜 신호가 있을 때 그걸 잡아내는가 (도구 자체의 검증)
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from chartfinder import backtest as bt
from chartfinder.screener import ConditionSpec
from tests.conftest import make_df


# --------------------------------------------------------------------------- 시점 자르기


def test_fundamentals_as_of_hides_unpublished_years():
    """2024년 결산은 2025년 3월 말에야 공시된다."""
    fundamentals = pd.DataFrame({"revenue": [100.0, 120.0]}, index=[2023, 2024])

    before_filing = bt.fundamentals_as_of(fundamentals, date(2025, 2, 1))
    assert list(before_filing.index) == [2023]

    after_filing = bt.fundamentals_as_of(fundamentals, date(2025, 5, 1))
    assert list(after_filing.index) == [2023, 2024]


def test_fundamentals_as_of_returns_none_when_nothing_published():
    fundamentals = pd.DataFrame({"revenue": [100.0]}, index=[2024])
    assert bt.fundamentals_as_of(fundamentals, date(2024, 6, 1)) is None


def test_fundamentals_as_of_passes_through_empty():
    assert bt.fundamentals_as_of(None, date(2025, 1, 1)) is None
    assert bt.fundamentals_as_of(pd.DataFrame(), date(2025, 1, 1)).empty


def test_forward_return_uses_future_close():
    df = make_df([100.0] * 10 + [110.0])
    assert bt.forward_return(df, 0, 10) == pytest.approx(10.0)


def test_forward_return_is_none_without_enough_future():
    df = make_df([100.0] * 10)
    assert bt.forward_return(df, 5, 10) is None


def test_trading_dates_leave_room_for_the_horizon():
    df = make_df(list(np.linspace(100, 200, bt.MIN_BARS + 100)))
    dates = bt.trading_dates(df, count=5, every=10, horizon=20)

    assert len(dates) == 5
    assert dates == sorted(dates)
    # 마지막 기준일 뒤에도 horizon 만큼 남아 있어야 한다
    assert pd.Timestamp(dates[-1]) <= df.index[-21]


def test_trading_dates_empty_when_history_is_short():
    assert bt.trading_dates(make_df([100.0] * 50), count=5, every=5, horizon=20) == []


# --------------------------------------------------------------------------- 미래 참조


class _Ticker:
    def __init__(self, symbol):
        self.symbol = symbol
        self.name = symbol


def _install(monkeypatch, frames: dict, fundamentals: dict | None = None):
    monkeypatch.setattr(bt.cache, "load", lambda market, symbol: frames.get(symbol))
    monkeypatch.setattr(
        bt.cache, "load_fundamentals",
        lambda market, symbol: (fundamentals or {}).get(symbol),
    )


def test_scoring_never_sees_bars_after_the_asof_date(monkeypatch):
    """기준일 이후 봉이 채점에 들어가면 백테스트 전체가 무의미해진다."""
    length = bt.MIN_BARS + 80
    df = make_df(list(np.linspace(100, 150, length)))
    _install(monkeypatch, {"A": df})

    seen = []
    import chartfinder.backtest as module

    original = module.score_one

    def spy(window, specs, fundamentals=None):
        seen.append(window.index[-1])
        return original(window, specs, fundamentals)

    monkeypatch.setattr(module, "score_one", spy)

    asof = df.index[bt.MIN_BARS + 10].date()
    bt.run("demo", [ConditionSpec("above_ma")], [_Ticker("A")], [asof], horizon=20)

    assert seen
    for last_bar in seen:
        assert last_bar.date() <= asof


def test_unpublished_fundamentals_are_withheld_during_scoring(monkeypatch):
    length = bt.MIN_BARS + 80
    df = make_df(list(np.linspace(100, 150, length)))
    asof = df.index[bt.MIN_BARS + 10].date()
    # 기준일 기준으로 아직 공시되지 않은 결산기
    fundamentals = pd.DataFrame({"revenue": [100.0]}, index=[asof.year])
    _install(monkeypatch, {"A": df}, {"A": fundamentals})

    received = []
    import chartfinder.backtest as module

    original = module.score_one
    monkeypatch.setattr(
        module, "score_one",
        lambda window, specs, f=None: received.append(f) or original(window, specs, f),
    )

    bt.run("demo", [ConditionSpec("revenue_growth")], [_Ticker("A")], [asof], horizon=20)
    assert received == [None]


# --------------------------------------------------------------------------- 신호 탐지


def _rigged_frames(count: int = 40, length: int | None = None):
    """점수가 높을수록 이후가 실제로 오르는 데이터를 만든다.

    above_ma 조건이 높게 나오는 종목(이평 위)일수록 이후 수익을 크게 준다.
    백테스트가 이런 신호조차 못 잡으면 도구가 고장 난 것이다.
    """
    length = length or bt.MIN_BARS + 60
    frames = {}
    for i in range(count):
        rising = i % 2 == 0
        base = np.linspace(100, 130, length) if rising else np.linspace(130, 100, length)
        # 오르던 종목은 이후에도 더 오르게 (신호 심기)
        tail = np.linspace(base[-1], base[-1] * (1.15 if rising else 0.9), 25)
        frames[f"S{i:02d}"] = make_df(list(base[:-25]) + list(tail))
    return frames


def test_backtest_detects_a_real_signal(monkeypatch):
    frames = _rigged_frames()
    _install(monkeypatch, frames)
    tickers = [_Ticker(symbol) for symbol in frames]

    df = next(iter(frames.values()))
    asof = df.index[-26].date()
    result = bt.run("demo", [ConditionSpec("above_ma")], tickers, [asof], horizon=20, top=10)

    summary = result.summary()
    assert summary["평균IC"] > 0.3
    assert summary["평균초과수익"] > 0


def test_backtest_reports_no_signal_on_noise(monkeypatch):
    rng = np.random.default_rng(0)
    length = bt.MIN_BARS + 60
    frames = {
        f"R{i:02d}": make_df(100 * np.exp(np.cumsum(rng.normal(0, 0.015, length))))
        for i in range(40)
    }
    _install(monkeypatch, frames)

    df = next(iter(frames.values()))
    asof = df.index[-26].date()
    result = bt.run(
        "demo", [ConditionSpec("above_ma")], [_Ticker(s) for s in frames], [asof],
        horizon=20, top=10,
    )
    assert abs(result.summary()["평균IC"]) < 0.6  # 무작위에서 강한 신호가 나오면 이상하다


# --------------------------------------------------------------------------- 집계


def test_by_date_compares_picks_against_everything(monkeypatch):
    rows = pd.DataFrame({
        "asof": [date(2026, 1, 5)] * 4,
        "symbol": list("ABCD"),
        "name": list("ABCD"),
        "score": [0.9, 0.8, 0.2, 0.1],
        "forward_return": [10.0, 6.0, -4.0, -8.0],
    })
    result = bt.Backtest(rows=rows, horizon=20, top=2)

    table = result.by_date()
    assert table.loc[0, "상위평균"] == pytest.approx(8.0)
    assert table.loc[0, "전체평균"] == pytest.approx(1.0)
    assert table.loc[0, "초과"] == pytest.approx(7.0)
    assert table.loc[0, "상위승률"] == 100.0


def test_score_buckets_are_ordered(monkeypatch):
    rows = pd.DataFrame({
        "asof": [date(2026, 1, 5)] * 20,
        "symbol": [f"S{i}" for i in range(20)],
        "name": [f"S{i}" for i in range(20)],
        "score": np.linspace(0, 1, 20),
        "forward_return": np.linspace(-10, 10, 20),
    })
    table = bt.Backtest(rows=rows, horizon=20, top=5).by_score_bucket(bins=4)
    assert len(table) == 4
    assert table["평균수익"].is_monotonic_increasing


def test_empty_backtest_is_safe():
    empty = bt.Backtest(rows=pd.DataFrame(), horizon=20, top=10)
    assert empty.summary() == {}
    assert empty.by_date().empty
    assert empty.by_score_bucket().empty
    assert empty.dates == []


def _rows_with_ic(ics: list[float], per_date: int = 60) -> pd.DataFrame:
    """기준일마다 원하는 방향의 점수-수익 관계를 갖는 표본을 만든다."""
    frames = []
    for i, ic in enumerate(ics):
        scores = np.linspace(0, 1, per_date)
        returns = scores * 10 * ic + np.linspace(-1, 1, per_date) * 0.01
        frames.append(pd.DataFrame({
            "asof": date(2026, 1, 1) + timedelta(days=30 * i),
            "symbol": [f"S{j}" for j in range(per_date)],
            "name": [f"S{j}" for j in range(per_date)],
            "score": scores,
            "forward_return": returns,
        }))
    return pd.concat(frames, ignore_index=True)


def test_ic_is_measured_per_date_not_pooled():
    """같은 날 종목들은 독립이 아니다. 독립 단위는 기준일이다."""
    result = bt.Backtest(rows=_rows_with_ic([1.0] * 6), horizon=20, top=10)
    stats = result.ic_stats()

    assert stats["기준일수"] == 6
    assert stats["평균IC"] == pytest.approx(1.0, abs=0.01)
    assert stats["t값"] > 2


def test_mixed_signs_across_dates_give_a_weak_t_value():
    """어떤 날은 맞고 어떤 날은 틀리면 신호로 볼 수 없다."""
    result = bt.Backtest(rows=_rows_with_ic([1.0, -1.0, 1.0, -1.0, 0.5, -0.5]), horizon=20, top=10)
    assert abs(result.ic_stats()["t값"]) < 2


def test_ic_stats_need_at_least_two_dates():
    result = bt.Backtest(rows=_rows_with_ic([1.0]), horizon=20, top=10)
    stats = result.ic_stats()
    assert stats["기준일수"] == 1
    assert np.isnan(stats["t값"])


def test_by_date_reports_ic_per_row():
    result = bt.Backtest(rows=_rows_with_ic([1.0, -1.0]), horizon=20, top=10)
    table = result.by_date()
    assert "IC" in table.columns
    assert table["IC"].iloc[0] > 0.9
    assert table["IC"].iloc[1] < -0.9


def test_spearman_handles_degenerate_input():
    assert np.isnan(bt._spearman(pd.Series([1, 2]), pd.Series([1, 2])))  # 표본 부족
    assert np.isnan(bt._spearman(pd.Series(range(10)), pd.Series([5] * 10)))  # 상수
    assert bt._spearman(pd.Series(range(10)), pd.Series(range(10))) == pytest.approx(1.0)


def test_summary_includes_excess_t_value():
    result = bt.Backtest(rows=_rows_with_ic([1.0] * 5), horizon=20, top=10)
    summary = result.summary()
    assert "초과t값" in summary
    assert summary["초과t값"] > 0


# --------------------------------------------------------------------------- 여러 전략 한 번에


def test_run_multi_matches_running_each_strategy_separately(monkeypatch):
    """한 번에 돌린 결과가 따로 돌린 것과 같아야 한다."""
    frames = _rigged_frames(count=12)
    _install(monkeypatch, frames)
    tickers = [_Ticker(symbol) for symbol in frames]
    df = next(iter(frames.values()))
    dates = [df.index[-26].date(), df.index[-40].date()]

    strategies = {
        "A": [ConditionSpec("above_ma")],
        "B": [ConditionSpec("near_low"), ConditionSpec("volume_surge")],
    }
    together = bt.run_multi("demo", strategies, tickers, dates, horizon=20)

    for name, specs in strategies.items():
        alone = bt.run("demo", specs, tickers, dates, horizon=20)
        merged = together[name].rows.sort_values(["asof", "symbol"]).reset_index(drop=True)
        single = alone.rows.sort_values(["asof", "symbol"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(merged, single)


def test_run_multi_shares_condition_work(monkeypatch):
    """같은 조건이 여러 전략에 있어도 (종목, 기준일)당 한 번만 계산한다."""
    import dataclasses

    from chartfinder.conditions import base

    frames = _rigged_frames(count=6)
    _install(monkeypatch, frames)
    tickers = [_Ticker(symbol) for symbol in frames]
    dates = [next(iter(frames.values())).index[-26].date()]

    calls = []
    original = base._REGISTRY["above_ma"]
    monkeypatch.setitem(
        base._REGISTRY, "above_ma",
        dataclasses.replace(
            original, fn=lambda ctx, **kw: calls.append(1) or original.fn(ctx, **kw)
        ),
    )

    bt.run_multi(
        "demo",
        {"A": [ConditionSpec("above_ma")], "B": [ConditionSpec("above_ma")],
         "C": [ConditionSpec("above_ma")]},
        tickers, dates, horizon=20,
    )
    assert len(calls) == len(tickers)


def test_run_multi_keeps_strategies_with_no_samples(monkeypatch):
    frames = _rigged_frames(count=4)
    _install(monkeypatch, frames)
    results = bt.run_multi(
        "demo", {"A": [ConditionSpec("above_ma")]},
        [_Ticker(s) for s in frames], [], horizon=20,
    )
    assert set(results) == {"A"}
    assert results["A"].rows.empty


# --------------------------------------------------------------------------- 죽은 조건


def test_conditions_with_no_data_are_reported(monkeypatch):
    """수급은 최근 120일치만 받는다. 그보다 앞선 구간을 검증하면 늘 0점이다."""
    frames = _rigged_frames(count=6)
    _install(monkeypatch, frames)
    tickers = [_Ticker(symbol) for symbol in frames]
    asof = next(iter(frames.values())).index[-26].date()

    result = bt.run(
        "demo",
        [ConditionSpec("above_ma"), ConditionSpec("foreign_net_buy")],
        tickers, [asof], horizon=20,
    )
    assert result.dead_conditions == ["foreign_net_buy"]


def test_run_multi_reports_dead_conditions_per_strategy(monkeypatch):
    frames = _rigged_frames(count=6)
    _install(monkeypatch, frames)
    tickers = [_Ticker(symbol) for symbol in frames]
    asof = next(iter(frames.values())).index[-26].date()

    results = bt.run_multi(
        "demo",
        {"차트": [ConditionSpec("above_ma")],
         "수급": [ConditionSpec("foreign_net_buy"), ConditionSpec("above_ma")]},
        tickers, [asof], horizon=20,
    )
    assert results["차트"].dead_conditions == []
    assert results["수급"].dead_conditions == ["foreign_net_buy"]


def test_live_conditions_are_not_flagged(monkeypatch):
    frames = _rigged_frames(count=6)
    _install(monkeypatch, frames)
    tickers = [_Ticker(symbol) for symbol in frames]
    asof = next(iter(frames.values())).index[-26].date()

    result = bt.run("demo", [ConditionSpec("above_ma")], tickers, [asof], horizon=20)
    assert result.dead_conditions == []


# --------------------------------------------------------------------------- 채점 불가 처리


def _flat_and_varied(per_date: int = 40) -> pd.DataFrame:
    """한 날은 전 종목 동점, 다른 날은 점수가 갈리는 표본."""
    flat = pd.DataFrame({
        "asof": date(2026, 1, 5),
        "symbol": [f"{i:06d}" for i in range(per_date)],
        "name": [f"S{i}" for i in range(per_date)],
        "score": [0.0] * per_date,
        # 종목코드 순서와 수익이 얽혀 있다 (앞 번호일수록 좋음)
        "forward_return": np.linspace(20, -20, per_date),
    })
    varied = pd.DataFrame({
        "asof": date(2026, 2, 5),
        "symbol": [f"{i:06d}" for i in range(per_date)],
        "name": [f"S{i}" for i in range(per_date)],
        "score": np.linspace(0, 1, per_date),
        "forward_return": np.linspace(-10, 10, per_date),
    })
    return pd.concat([flat, varied], ignore_index=True)


def test_dates_where_every_score_is_equal_are_marked_unscorable():
    result = bt.Backtest(rows=_flat_and_varied(), horizon=20, top=10)
    table = result.by_date()

    assert list(table["채점가능"]) == [False, True]
    assert result.scorable_dates() == [date(2026, 2, 5)]


def test_unscorable_dates_are_excluded_from_the_summary():
    """전 종목 동점인 날의 '초과수익'은 종목코드 순서가 만든 허상이다."""
    result = bt.Backtest(rows=_flat_and_varied(), horizon=20, top=10)
    summary = result.summary()

    assert summary["기준일수"] == 1
    assert summary["채점불가일수"] == 1


def test_ties_are_broken_randomly_not_by_symbol_order():
    """동점을 종목코드 순으로 뽑으면 오래된 대형주가 계속 뽑힌다."""
    result = bt.Backtest(rows=_flat_and_varied(per_date=200), horizon=20, top=20)
    flat = result.by_date().iloc[0]

    # 앞 번호만 뽑혔다면 수익이 +20 근처로 쏠린다
    assert flat["상위평균"] < 15


def test_score_buckets_ignore_unscorable_dates():
    table = bt.Backtest(rows=_flat_and_varied(), horizon=20, top=10).by_score_bucket(bins=4)
    assert len(table) == 4  # 동점인 날이 섞이면 구간이 무너진다
