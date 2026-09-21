"""재무 스키마·조건·캐시 테스트 (네트워크 없이)."""

from datetime import date

import pandas as pd
import pytest

from chartfinder import cache
from chartfinder.conditions import Ctx, get
from chartfinder.fundamentals import FUNDAMENTAL_COLUMNS, normalize
from tests.conftest import make_df

NAVER_STYLE = pd.DataFrame(
    {
        "매출액": [2_000_000, 2_200_000, 2_500_000],
        "영업이익": [200_000, 250_000, 300_000],
        "영업이익률": [10.0, 11.4, 12.0],
        "ROE(지배주주)": [8.0, 11.0, 13.0],
        "부채비율": [80.0, 70.0, 60.0],
        "당기순이익": [150_000, 190_000, 240_000],
    },
    index=["2022/12", "2023/12", "2024/12"],
)


def _fundamentals(**columns) -> pd.DataFrame:
    years = len(next(iter(columns.values())))
    index = list(range(2025 - years + 1, 2026))
    return normalize(pd.DataFrame(columns, index=index))


# --------------------------------------------------------------------------- 스키마


def test_normalize_maps_korean_column_names():
    df = normalize(NAVER_STYLE)
    assert list(df.columns) == FUNDAMENTAL_COLUMNS
    assert df.loc[2024, "revenue"] == 2_500_000
    assert df.loc[2024, "roe"] == 13.0
    assert df.index.tolist() == [2022, 2023, 2024]


def test_normalize_maps_english_column_names():
    df = normalize(
        pd.DataFrame(
            {"Total Revenue": [10.0, 12.0], "Operating Income": [1.0, 2.0],
             "Operating Cash Flow": [3.0, 4.0]},
            index=[pd.Timestamp("2023-12-31"), pd.Timestamp("2024-12-31")],
        )
    )
    assert df.loc[2024, "revenue"] == 12.0
    assert df.loc[2024, "operating_cash_flow"] == 4.0


def test_normalize_derives_operating_margin_when_missing():
    df = normalize(pd.DataFrame({"매출액": [1000.0], "영업이익": [150.0]}, index=[2024]))
    assert df.loc[2024, "operating_margin"] == pytest.approx(15.0)


def test_normalize_keeps_existing_margin():
    df = normalize(NAVER_STYLE)
    assert df.loc[2023, "operating_margin"] == 11.4


def test_normalize_handles_empty_and_unknown_frames():
    assert normalize(pd.DataFrame()).empty
    assert normalize(pd.DataFrame({"엉뚱한컬럼": [1]}, index=[2024])).empty


def test_normalize_drops_rows_without_a_year():
    df = normalize(pd.DataFrame({"매출액": [1.0, 2.0]}, index=["합계", "2024/12"]))
    assert df.index.tolist() == [2024]


# --------------------------------------------------------------------------- 조건


def test_revenue_growth_full_score_for_steady_rise():
    ctx = Ctx(make_df([100.0] * 5), _fundamentals(revenue=[100, 110, 120, 130]))
    assert get("revenue_growth").score(ctx, {"years": 4}) == 1.0


def test_revenue_growth_partial_when_one_year_dips():
    ctx = Ctx(make_df([100.0] * 5), _fundamentals(revenue=[100, 90, 120, 130]))
    score = get("revenue_growth").score(ctx, {"years": 4})
    assert 0.3 < score < 1.0


def test_revenue_growth_zero_when_shrinking():
    ctx = Ctx(make_df([100.0] * 5), _fundamentals(revenue=[130, 120, 110, 100]))
    assert get("revenue_growth").score(ctx, {"years": 4}) < 0.2


def test_growth_conditions_need_enough_years():
    ctx = Ctx(make_df([100.0] * 5), _fundamentals(revenue=[100, 110]))
    assert get("revenue_growth").score(ctx, {"years": 4}) == 0.0


def test_operating_income_growth_uses_its_own_column():
    ctx = Ctx(
        make_df([100.0] * 5),
        _fundamentals(revenue=[100, 110, 120, 130], operating_income=[10, 5, 3, 1]),
    )
    assert get("revenue_growth").score(ctx, {"years": 4}) == 1.0
    assert get("operating_income_growth").score(ctx, {"years": 4}) < 0.2


def test_operating_margin_threshold():
    cond = get("operating_margin")
    ctx = Ctx(make_df([100.0] * 5), _fundamentals(revenue=[1000, 1000], operating_income=[120, 130]))
    assert cond.score(ctx, {"min_margin": 10.0, "years": 2}) == 1.0
    assert cond.score(ctx, {"min_margin": 30.0, "years": 2}) < 0.5


def test_debt_ratio_prefers_lower():
    cond = get("debt_ratio")
    safe = Ctx(make_df([100.0] * 5), _fundamentals(debt_ratio=[40.0, 50.0]))
    risky = Ctx(make_df([100.0] * 5), _fundamentals(debt_ratio=[300.0, 320.0]))
    assert cond.score(safe, {"max_ratio": 100.0, "years": 2}) == 1.0
    assert cond.score(risky, {"max_ratio": 100.0, "years": 2}) < 0.2


def test_roe_threshold():
    cond = get("roe")
    ctx = Ctx(make_df([100.0] * 5), _fundamentals(roe=[12.0, 15.0]))
    assert cond.score(ctx, {"min_roe": 10.0, "years": 2}) == 1.0
    assert cond.score(ctx, {"min_roe": 25.0, "years": 2}) < 0.5


def test_positive_cash_flow_counts_years():
    cond = get("positive_cash_flow")
    good = Ctx(make_df([100.0] * 5), _fundamentals(operating_cash_flow=[10, 20, 30]))
    mixed = Ctx(make_df([100.0] * 5), _fundamentals(operating_cash_flow=[10, -5, 30]))
    assert cond.score(good, {"years": 3}) == 1.0
    assert cond.score(mixed, {"years": 3}) == pytest.approx(2 / 3)


def test_every_fundamental_condition_scores_zero_without_data():
    """재무를 받지 않은 종목은 예외 없이 0점이어야 한다."""
    ctx = Ctx(make_df([100.0] * 40))
    for cond in get("revenue_growth").__class__.__mro__[:1]:  # 타입 고정용
        pass
    for key in ("revenue_growth", "operating_income_growth", "operating_margin",
                "debt_ratio", "roe", "positive_cash_flow"):
        assert get(key).score(ctx) == 0.0


def test_fundamental_conditions_ignore_missing_columns():
    """일부 항목만 있는 재무(예: 네이버 표에는 현금흐름이 없다)도 견뎌야 한다."""
    ctx = Ctx(make_df([100.0] * 5), normalize(NAVER_STYLE))
    assert get("revenue_growth").score(ctx, {"years": 3}) == 1.0
    assert get("positive_cash_flow").score(ctx, {"years": 3}) == 0.0


# --------------------------------------------------------------------------- 캐시


@pytest.fixture
def demo_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTFINDER_HOME", str(tmp_path))
    return tmp_path


def test_fundamentals_round_trip(demo_home):
    from chartfinder.datasource import get_source

    symbol = get_source("demo").list_tickers()[0].symbol
    df = get_source("demo").fetch_fundamentals(symbol)
    cache.save_fundamentals("demo", symbol, df)

    loaded = cache.load_fundamentals("demo", symbol)
    assert loaded is not None
    assert list(loaded.columns) == FUNDAMENTAL_COLUMNS
    assert loaded.index.is_monotonic_increasing


def test_update_fundamentals_skips_fresh_cache(demo_home):
    from chartfinder.datasource import get_source

    symbols = [t.symbol for t in get_source("demo").list_tickers()][:3]
    first = cache.update_fundamentals("demo", symbols=symbols)
    assert first["updated"] == 3

    again = cache.update_fundamentals("demo", symbols=symbols)
    assert again["skipped"] == 3
    assert again["updated"] == 0

    forced = cache.update_fundamentals("demo", symbols=symbols, force=True)
    assert forced["updated"] == 3


def test_update_fundamentals_reports_unsupported_market(demo_home, monkeypatch):
    from chartfinder.datasource import get_source

    source = get_source("demo")
    monkeypatch.setattr(source, "supports_fundamentals", False, raising=False)
    stats = cache.update_fundamentals("demo", symbols=["DEMO000"])
    assert stats["updated"] == 0
    assert "지원하지" in str(stats["error"])


def test_update_fundamentals_reports_failures(demo_home, monkeypatch):
    from chartfinder.datasource import get_source

    source = get_source("demo")
    monkeypatch.setattr(
        source, "fetch_fundamentals",
        lambda symbol: (_ for _ in ()).throw(RuntimeError("차단됨")),
        raising=False,
    )
    stats = cache.update_fundamentals("demo", symbols=["DEMO000"], force=True)
    assert stats["failed"] == 1
    assert "차단됨" in str(stats["error"])


def test_screen_reads_fundamentals_only_when_needed(demo_home):
    """재무 조건이 없으면 재무 캐시를 읽지 않는다."""
    from chartfinder.datasource import get_source
    from chartfinder.screener import ConditionSpec, screen

    source = get_source("demo")
    tickers = source.list_tickers()[:4]
    cache.update("demo", symbols=[t.symbol for t in tickers], years=1)
    cache.update_fundamentals("demo", symbols=[t.symbol for t in tickers])

    with_fund = screen("demo", [ConditionSpec("revenue_growth")], tickers=tickers)
    assert with_fund["s_revenue_growth"].max() > 0

    chart_only = screen("demo", [ConditionSpec("above_ma")], tickers=tickers)
    assert not chart_only.empty
