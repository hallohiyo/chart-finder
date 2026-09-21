"""네이버 기업실적분석 파서 테스트 (저장된 HTML 로)."""

from pathlib import Path

import pandas as pd
import pytest

from chartfinder.datasource import naver_fundamentals as nf

FIXTURE = Path(__file__).parent / "fixtures" / "naver_main.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_page_reads_annual_figures(html):
    df = nf.parse_page(html)
    assert df.loc[2024, "revenue"] == 2_500_000
    assert df.loc[2024, "operating_income"] == 300_000
    assert df.loc[2023, "roe"] == 11.0
    assert df.loc[2022, "debt_ratio"] == 80.0


def test_parse_page_excludes_quarterly_columns(html):
    """분기 실적을 연간으로 착각하면 증가 추세 판정이 망가진다."""
    df = nf.parse_page(html)
    assert df.index.tolist() == [2022, 2023, 2024]


def test_parse_page_excludes_consensus_estimates(html):
    """2025.12(E) 는 추정치라 실적 증가로 세면 안 된다."""
    assert 2025 not in nf.parse_page(html).index
    assert 2025 in nf.parse_page(html, include_estimates=True).index


def test_parse_page_ignores_other_tables(html):
    assert not nf.parse_page(html).empty


def test_parse_page_returns_empty_for_unrelated_html():
    assert nf.parse_page("<html><table><tr><td>1</td></tr></table></html>").empty


def test_parsed_frame_feeds_the_conditions(html):
    from chartfinder.conditions import Ctx, get
    from tests.conftest import make_df

    ctx = Ctx(make_df([100.0] * 40), nf.parse_page(html))
    assert get("revenue_growth").score(ctx, {"years": 3}) == 1.0
    assert get("operating_income_growth").score(ctx, {"years": 3}) == 1.0
    assert get("debt_ratio").score(ctx, {"max_ratio": 100.0, "years": 3}) == 1.0
    assert get("roe").score(ctx, {"min_roe": 10.0, "years": 2}) == 1.0
    # 이 표에는 현금흐름이 없다
    assert get("positive_cash_flow").score(ctx, {"years": 3}) == 0.0
