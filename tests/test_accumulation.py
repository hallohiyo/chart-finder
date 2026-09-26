"""외국인·기관이 '많이 담는' 것을 재는 조건.

며칠 연속인지(기존)와 얼마나 많이인지(여기)는 다른 질문이다.
절대 주식 수는 종목 크기에 따라 의미가 달라지므로 비중·금액으로 본다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chartfinder.conditions import get as get_condition
from chartfinder.conditions.base import Ctx


def _ctx(foreign: list[float], inst: list[float], close: float = 10_000.0,
         profile: dict | None = None) -> Ctx:
    rows = len(foreign)
    index = pd.date_range("2024-01-01", periods=rows, freq="D")
    return Ctx(
        pd.DataFrame(
            {"open": close, "high": close, "low": close, "close": close,
             "volume": 1_000_000.0, "foreign_net": foreign, "inst_net": inst},
            index=index,
        ),
        None,
        profile,
    )


def _score(key: str, ctx: Ctx, **params) -> float:
    condition = get_condition(key)
    return condition.score(ctx, {p.name: p.default for p in condition.params} | params)


# --------------------------------------------------------------- 매집 비중


def test_same_share_count_means_different_things_by_company_size():
    """10만주는 소형주엔 대량, 대형주엔 미미하다 — 그걸 구분해야 한다."""
    flows = [5_000.0] * 20                      # 20일간 10만주 순매수
    small = _ctx(flows, [0.0] * 20, profile={"shares": 2_000_000.0})    # 5%
    large = _ctx(flows, [0.0] * 20, profile={"shares": 500_000_000.0})  # 0.02%

    assert _score("net_buy_ratio", small, who="foreign", days=20, min_pct=1.0) == 1.0
    assert _score("net_buy_ratio", large, who="foreign", days=20, min_pct=1.0) < 0.05


def test_net_buy_ratio_counts_both_sides_together():
    both = _ctx([3_000.0] * 20, [3_000.0] * 20, profile={"shares": 2_000_000.0})
    # 외국인만 3%, 합치면 6%
    foreign_only = _score("net_buy_ratio", both, who="foreign", days=20, min_pct=5.0)
    combined = _score("net_buy_ratio", both, who="both", days=20, min_pct=5.0)
    assert foreign_only < combined
    assert combined == 1.0


def test_net_selling_scores_zero():
    dumping = _ctx([-5_000.0] * 20, [-5_000.0] * 20, profile={"shares": 2_000_000.0})
    assert _score("net_buy_ratio", dumping, days=20, min_pct=0.5) == 0.0


def test_net_buy_ratio_without_the_snapshot_is_zero():
    """상장주식수가 없으면 비중을 낼 수 없다."""
    ctx = _ctx([5_000.0] * 20, [5_000.0] * 20)
    assert _score("net_buy_ratio", ctx, days=20) == 0.0


# --------------------------------------------------------------- 순매수 금액


def test_net_buy_value_uses_money_not_shares():
    """같은 주식 수라도 주가가 다르면 들어온 돈이 다르다."""
    cheap = _ctx([10_000.0] * 20, [0.0] * 20, close=1_000.0)    # 2억
    pricey = _ctx([10_000.0] * 20, [0.0] * 20, close=200_000.0)  # 400억

    assert _score("net_buy_value", cheap, who="foreign", days=20, min_value=100.0) < 0.05
    assert _score("net_buy_value", pricey, who="foreign", days=20, min_value=100.0) == 1.0


# --------------------------------------------------------------- 쌍끌이


def test_both_sides_buying_together_beats_one_side_only():
    """한쪽이 사고 다른 쪽이 파는 것과 둘이 같이 담는 것은 다르다."""
    together = _ctx([100.0] * 10, [100.0] * 10)
    opposed = _ctx([100.0] * 10, [-100.0] * 10)   # 외국인 매수, 기관 매도

    assert _score("both_net_buy", together, days=5) == 1.0
    assert _score("both_net_buy", opposed, days=5) == 0.0


def test_partial_overlap_gives_a_partial_score():
    # 최근 4일 중 2일만 둘이 함께 순매수 (조건의 최소 봉 수를 채우려고 앞을 채운다)
    ctx = _ctx(
        [0.0] * 8 + [100.0, 100.0, 100.0, 100.0],
        [0.0] * 8 + [-100.0, -100.0, 100.0, 100.0],
    )
    assert _score("both_net_buy", ctx, days=4) == pytest.approx(0.5)


def test_both_net_buy_needs_both_columns():
    index = pd.date_range("2024-01-01", periods=10, freq="D")
    only_foreign = Ctx(
        pd.DataFrame(
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
             "foreign_net": 100.0},
            index=index,
        )
    )
    assert _score("both_net_buy", only_foreign, days=5) == 0.0


# --------------------------------------------------------------- 매집 가속


def test_accelerating_accumulation_is_detected():
    # 앞은 하루 100주, 최근 5일은 하루 1,000주
    flows = [100.0] * 35 + [1_000.0] * 5
    ctx = _ctx(flows, [0.0] * 40)
    assert _score("net_buy_accelerating", ctx, who="foreign",
                  short=5, long=20, mult=2.0) == 1.0


def test_steady_accumulation_is_not_acceleration():
    steady = _ctx([500.0] * 40, [0.0] * 40)
    assert _score("net_buy_accelerating", steady, who="foreign",
                  short=5, long=20, mult=2.0) < 0.2


def test_acceleration_compares_daily_averages_not_totals():
    """기간이 다르므로 합계로 비교하면 항상 장기가 크게 나온다."""
    steady = _ctx([500.0] * 40, [0.0] * 40)
    # 하루평균이 같으므로 배수는 1.0 — 1.0 기준이면 만점이어야 한다
    assert _score("net_buy_accelerating", steady, who="foreign",
                  short=5, long=20, mult=1.0) == 1.0


def test_acceleration_needs_a_positive_baseline():
    """그동안 팔던 종목은 비교 기준이 없다."""
    was_selling = _ctx([-500.0] * 35 + [1_000.0] * 5, [0.0] * 40)
    assert _score("net_buy_accelerating", was_selling, who="foreign", short=5, long=20) == 0.0


def test_acceleration_rejects_a_short_window_longer_than_the_long_one():
    ctx = _ctx([500.0] * 40, [0.0] * 40)
    assert _score("net_buy_accelerating", ctx, short=20, long=20) == 0.0


# --------------------------------------------------------------- 프리셋


def test_accumulation_preset_is_complete():
    from chartfinder import presets as presets_mod

    preset = next(
        preset for _, preset in presets_mod.load_all() if "쌍끌이" in preset.name
    )
    assert preset.needs_flows and preset.needs_profiles
    keys = {spec.key for spec in preset.conditions}
    assert {"both_net_buy", "net_buy_ratio", "net_buy_value"} <= keys
    for spec in preset.conditions:
        get_condition(spec.key)


# --------------------------------------------------------------- 저장 파일


def test_saved_file_carries_the_actual_flow_numbers(tmp_path, monkeypatch):
    """점수만 저장하면 왜 뽑혔는지 확인할 수 없다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder import cache
    from chartfinder.datasource import get_source
    from chartfinder.screener import ConditionSpec, export_frame, screen

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:8]
    cache.update("demo", symbols=symbols, years=1, flows=True)
    cache.update_profiles("demo", symbols=symbols)

    result = screen(
        "demo", [ConditionSpec("both_net_buy")],
        tickers=cache.get_tickers("demo", "all")[:8],
    )
    for column in ("foreign_net_5d", "inst_net_5d", "both_buy_days_20d",
                   "turnover_20d", "marcap"):
        assert column in result.columns, column

    saved = export_frame(result)
    assert "외국인 순매수 5일(주)" in saved.columns
    assert "기관 순매수 20일(주)" in saved.columns
    assert "쌍끌이 일수 20일" in saved.columns
    # 내부 컬럼명은 그대로 남아야 한다 (코드가 참조한다)
    assert "score" in result.columns


def test_flow_columns_are_absent_without_flow_data(tmp_path, monkeypatch):
    """수급을 안 받았으면 빈 컬럼을 만들지 않는다 (0 으로 채우면 오해를 준다)."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder import cache
    from chartfinder.datasource import get_source
    from chartfinder.screener import ConditionSpec, screen

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:5]
    cache.update("demo", symbols=symbols, years=1)
    # 데모 일봉에는 합성 수급이 섞여 있으므로 지운다
    for sym in symbols:
        df = cache.load("demo", sym)
        cache.save("demo", sym, df.drop(columns=[c for c in df.columns if c.endswith("_net")]))

    result = screen(
        "demo", [ConditionSpec("turnover_value")],
        tickers=cache.get_tickers("demo", "all")[:5],
    )
    assert "foreign_net_5d" not in result.columns
    assert "turnover_20d" in result.columns  # 시세만으로 되는 건 남아야 한다


def test_export_rounds_the_price():
    import pandas as pd

    from chartfinder.screener import export_frame

    frame = pd.DataFrame({"symbol": ["A"], "close": [596_612.8414485113]})
    assert export_frame(frame)["현재가"].iloc[0] == 596_612.84


def test_real_numbers_come_before_the_strategy_score_columns(tmp_path, monkeypatch):
    """전략을 많이 고르면 p_ 컬럼이 그만큼 늘어난다.

    실제 숫자를 그 뒤에 두면 엑셀에서 30열 밖으로 밀려 안 보인다.
    """
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder import cache
    from chartfinder.datasource import get_source
    from chartfinder.screener import ConditionSpec, screen_multi

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:6]
    cache.update("demo", symbols=symbols, years=1, flows=True)
    cache.update_profiles("demo", symbols=symbols)

    # 전략 열 개를 고른 상황
    strategies = {
        f"전략{i}": [ConditionSpec("above_ma"), ConditionSpec("volume_surge")]
        for i in range(10)
    }
    result = screen_multi("demo", strategies, tickers=cache.get_tickers("demo", "all")[:6])

    columns = list(result.columns)
    turnover = columns.index("turnover_20d")
    first_strategy = min(i for i, c in enumerate(columns) if c.startswith("p_"))
    assert turnover < first_strategy, "거래대금이 전략 점수 뒤로 밀렸다"
    # 엑셀 첫 화면에 들어와야 한다
    assert turnover < 12


def test_screen_puts_real_numbers_before_condition_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder import cache
    from chartfinder.datasource import get_source
    from chartfinder.screener import ConditionSpec, screen

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:6]
    cache.update("demo", symbols=symbols, years=1, flows=True)

    specs = [ConditionSpec("above_ma"), ConditionSpec("volume_surge")]
    result = screen("demo", specs, tickers=cache.get_tickers("demo", "all")[:6])

    columns = list(result.columns)
    assert columns.index("turnover_20d") < columns.index("s_above_ma")


def test_empty_result_keeps_the_same_column_order(tmp_path, monkeypatch):
    """종목이 하나도 안 나올 때도 컬럼 순서가 같아야 한다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    from chartfinder import cache
    from chartfinder.datasource import get_source
    from chartfinder.screener import ConditionSpec, screen_multi

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:4]
    cache.update("demo", symbols=symbols, years=1)

    empty = screen_multi(
        "demo", {"A": [ConditionSpec("above_ma")]},
        tickers=cache.get_tickers("demo", "all")[:4], min_score=1.1,
    )
    assert empty.empty
    columns = list(empty.columns)
    assert columns.index("turnover_20d") < columns.index("p_A")


def test_net_buy_amount_uses_each_days_close_not_the_latest_price(tmp_path, monkeypatch):
    """'총 수량 × 현재가' 로 계산하면 기간 중 주가가 움직였을 때 틀린다."""
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    import pandas as pd

    from chartfinder.screener import facts

    index = pd.date_range("2024-01-01", periods=5, freq="D")
    # 첫 이틀은 1만원에 100주, 뒤 사흘은 2만원에 100주씩 순매수
    df = pd.DataFrame(
        {"open": 0.0, "high": 0.0, "low": 0.0,
         "close": [10_000.0, 10_000.0, 20_000.0, 20_000.0, 20_000.0],
         "volume": 1_000.0,
         "foreign_net": [100.0] * 5, "inst_net": [0.0] * 5},
        index=index,
    )
    out = facts(df)
    # 날짜별: 2×100×1만 + 3×100×2만 = 800만원 = 0.08억
    assert out["foreign_value_5d"] == pytest.approx(0.1, abs=0.05)
    # 총 수량(500주) × 현재가(2만) = 1,000만원 = 0.1억 — 이쪽이면 과대계상이다
    assert out["foreign_net_5d"] == 500


def test_amount_columns_come_before_share_columns(tmp_path, monkeypatch):
    """금액이 먼저 보여야 한다 — 주식 수는 종목끼리 비교가 안 된다."""
    from chartfinder.screener import FACT_COLUMNS

    assert FACT_COLUMNS.index("foreign_value_5d") < FACT_COLUMNS.index("foreign_net_5d")
    assert FACT_COLUMNS.index("inst_value_5d") < FACT_COLUMNS.index("inst_net_5d")
