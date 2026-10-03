"""결과 숫자가 맞는지 검증한다.

수익률 예측은 보장할 수 없지만, 프로그램이 내놓는 숫자가 원본 데이터와
맞는지는 검증할 수 있다. 세 가지를 본다.

1. 표시·저장되는 값이 원본에서 손계산한 값과 같은가
2. 지표 공식이 맞는가 (pandas 없이 다시 계산해 대조)
3. 미래를 엿보지 않는가 (i까지 잘라 계산한 값 == 전체로 계산한 i번째 값)
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from chartfinder import cache, indicators as ind
from chartfinder.conditions import all_conditions
from chartfinder.conditions.base import Ctx
from chartfinder.datasource import get_source
from chartfinder.screener import ConditionSpec, combine, screen
from chartfinder.conditions import get as get_condition


@pytest.fixture
def filled(tmp_path, monkeypatch):
    """시세·수급·종목정보를 모두 받아둔 캐시."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    symbols = [t.symbol for t in get_source("demo").list_tickers()][:12]
    cache.update("demo", symbols=symbols, years=2, flows=True)
    cache.update_profiles("demo", symbols=symbols)
    return cache.get_tickers("demo", "all")[:12]


# ----------------------------------------------------------- 표시되는 값


def test_every_reported_number_matches_a_hand_calculation(filled):
    specs = [
        ConditionSpec("turnover_value", {"period": 20, "min_value": 30.0}),
        ConditionSpec("both_net_buy", {"days": 5}),
    ]
    result = screen("demo", specs, tickers=filled)
    profiles = cache.load_profiles("demo")
    assert not result.empty

    for row in result.to_dict("records"):
        df = cache.load("demo", row["symbol"])

        assert row["close"] == pytest.approx(float(df["close"].iloc[-1]))

        want_chg = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-2]) - 1) * 100
        assert row["chg_pct"] == pytest.approx(round(want_chg, 2))

        want_turnover = (df["close"] * df["volume"]).tail(20).mean() / 1e8
        assert row["turnover_20d"] == pytest.approx(round(want_turnover, 1))

        want_marcap = round(float(profiles.loc[row["symbol"], "marcap"]) / 1e8, 0)
        assert row["marcap"] == pytest.approx(want_marcap)

        pair = df[["foreign_net", "inst_net"]].dropna().tail(20)
        want_both = int(((pair["foreign_net"] > 0) & (pair["inst_net"] > 0)).sum())
        assert row["both_buy_days_20d"] == want_both

        # 순매수 금액은 날짜별 (수량 × 그날 종가) 의 합이다
        want_value = float((df["foreign_net"] * df["close"]).dropna().tail(20).sum()) / 1e8
        assert row["foreign_value_20d"] == pytest.approx(round(want_value, 1))


def test_headline_score_is_exactly_the_weighted_average(filled):
    """적합도는 조건별 점수의 가중평균이어야 한다."""
    specs = [
        ConditionSpec("above_ma", weight=2.0),
        ConditionSpec("volume_surge", weight=1.0),
        ConditionSpec("disparity_range", weight=1.5),
    ]
    result = screen("demo", specs, tickers=filled)

    for row in result.to_dict("records"):
        scores = {spec.key: row[f"s_{spec.key}"] for spec in specs}
        # 저장된 점수는 소수 셋째 자리로 반올림돼 있어 그만큼 허용한다
        assert row["score"] == pytest.approx(combine(scores, specs), abs=5e-4)


# ----------------------------------------------------------- 지표 공식


def _series() -> pd.DataFrame:
    from chartfinder.datasource.demo import generate
    from datetime import date, timedelta

    end = date.today()
    return generate("DEMO000", end - timedelta(days=400), end).tail(200).reset_index(drop=True)


def test_sma_is_a_plain_average():
    df = _series()
    closes = [float(x) for x in df["close"]]
    assert ind.sma(df["close"], 20).iloc[-1] == pytest.approx(sum(closes[-20:]) / 20)


def test_rsi_uses_wilder_smoothing():
    df = _series()
    c = [float(x) for x in df["close"]]
    n = 14
    gains = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    want = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    assert ind.rsi(df["close"], 14).iloc[-1] == pytest.approx(want, abs=0.5)


def test_atr_smooths_the_true_range():
    df = _series()
    c = [float(x) for x in df["close"]]
    h = [float(x) for x in df["high"]]
    l = [float(x) for x in df["low"]]
    tr = [
        max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        for i in range(1, len(c))
    ]
    a = sum(tr[:14]) / 14
    for i in range(14, len(tr)):
        a = (a * 13 + tr[i]) / 14
    assert ind.atr(df, 14).iloc[-1] == pytest.approx(a, abs=0.5)


def test_disparity_and_envelope_and_drawdown():
    df = _series()
    c = [float(x) for x in df["close"]]
    ma = sum(c[-20:]) / 20

    assert ind.disparity(df["close"], 20).iloc[-1] == pytest.approx(c[-1] / ma * 100)
    lower, center, upper = ind.envelope(df["close"], 20, 10.0)
    assert lower.iloc[-1] == pytest.approx(ma * 0.9)
    assert center.iloc[-1] == pytest.approx(ma)
    assert upper.iloc[-1] == pytest.approx(ma * 1.1)
    assert ind.pct_change_n(df["close"], 20).iloc[-1] == pytest.approx(
        (c[-1] / c[-21] - 1) * 100
    )
    assert ind.drawdown_from_high(df["close"], 60).iloc[-1] == pytest.approx(
        (c[-1] / max(c[-60:]) - 1) * 100
    )


# ----------------------------------------------------------- 미래 엿보기


@pytest.mark.parametrize("name", [
    "sma", "ema", "rsi", "macd", "bollinger", "atr", "rolling_high",
    "drawdown", "slope", "volume_ratio", "envelope", "disparity", "dmi", "stoch",
])
def test_indicators_do_not_look_ahead(name):
    """i까지 잘라서 계산한 마지막 값 == 전체로 계산한 i번째 값.

    창이 중앙정렬이거나 shift(-1) 이 섞이면 둘이 달라진다. 그러면 백테스트가
    실제로는 알 수 없었던 정보로 종목을 고르게 된다.
    """
    df = _series()
    makers = {
        "sma": lambda d: ind.sma(d["close"], 20),
        "ema": lambda d: ind.ema(d["close"], 20),
        "rsi": lambda d: ind.rsi(d["close"], 14),
        "macd": lambda d: ind.macd(d["close"])[2],
        "bollinger": lambda d: ind.bollinger(d["close"], 20, 2.0)[2],
        "atr": lambda d: ind.atr(d, 14),
        "rolling_high": lambda d: ind.rolling_high(d["close"], 60),
        "drawdown": lambda d: ind.drawdown_from_high(d["close"], 120),
        "slope": lambda d: ind.slope_pct(d["close"], 5),
        "volume_ratio": lambda d: ind.volume_ratio(d["volume"], 20),
        "envelope": lambda d: ind.envelope(d["close"], 20, 10.0)[0],
        "disparity": lambda d: ind.disparity(d["close"], 20),
        "dmi": lambda d: ind.dmi(d, 14, 14)[2],
        "stoch": lambda d: ind.stochastic_slow(d, 14, 3, 3)[0],
    }
    make = makers[name]
    whole = make(df)
    for i in (len(df) - 1, len(df) - 5, len(df) - 30):
        cut = make(df.iloc[: i + 1]).iloc[-1]
        full = whole.iloc[i]
        if pd.isna(cut) and pd.isna(full):
            continue
        assert not (pd.isna(cut) ^ pd.isna(full)), f"{name} @{i}"
        assert float(cut) == pytest.approx(float(full), abs=1e-9), f"{name} @{i}"


# ----------------------------------------------------------- 조건 전수 점검


def test_no_condition_crashes_or_leaves_its_range(filled):
    """조건 68개가 모두 0~1 을 돌려주고 예외를 내지 않아야 한다."""
    profiles = cache.load_profiles("demo")
    benchmark = cache.load_index("demo", "DEMOIDX")

    for ticker in filled[:4]:
        df = cache.load("demo", ticker.symbol)
        profile = (
            profiles.loc[ticker.symbol].to_dict() if ticker.symbol in profiles.index else None
        )
        ctx = Ctx(df, None, profile, benchmark)
        for condition in all_conditions():
            params = {p.name: p.default for p in condition.params}
            score = condition.score(ctx, params)
            assert 0.0 <= score <= 1.0, f"{condition.key} → {score}"
            assert not math.isnan(score), condition.key


def test_missing_data_scores_zero_never_a_guess():
    """데이터가 없을 때 0 이 아닌 값을 주면 없는 근거로 순위가 매겨진다."""
    index = pd.date_range("2024-01-01", periods=60, freq="D")
    bare = Ctx(
        pd.DataFrame(
            {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0},
            index=index,
        )
    )
    # 수급·재무·종목정보·지수를 하나도 안 받은 상태
    needs_data = [
        "both_net_buy", "net_buy_ratio", "net_buy_value", "net_buy_accelerating",
        "revenue_growth", "roe", "no_capital_impairment",
        "market_cap", "float_ratio", "major_holder", "low_short_ratio",
        "relative_strength", "turnover_to_marcap",
    ]
    for key in needs_data:
        condition = get_condition(key)
        params = {p.name: p.default for p in condition.params}
        assert condition.score(bare, params) == 0.0, key
