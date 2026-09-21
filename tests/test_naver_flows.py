"""네이버 금융 수급 파서 테스트 (네트워크 없이, 저장된 HTML 로)."""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from chartfinder.datasource import naver_flows

FIXTURE = Path(__file__).parent / "fixtures" / "naver_frgn.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_page_extracts_foreign_and_institution(html):
    df = naver_flows.parse_page(html)
    assert list(df.columns) == ["foreign_net", "inst_net"]
    assert len(df) == 3
    assert df.index.is_monotonic_increasing


def test_parse_page_reads_values_with_commas_and_signs(html):
    df = naver_flows.parse_page(html)
    latest = df.loc["2026-09-21"]
    assert latest["foreign_net"] == 234_567
    assert latest["inst_net"] == 123_456
    assert df.loc["2026-09-18", "inst_net"] == -45_678


def test_parse_page_ignores_navigation_table(html):
    """페이지 번호 표를 수급 표로 착각하면 안 된다."""
    assert not naver_flows.parse_page(html).empty


def test_parse_page_returns_empty_for_unrelated_html():
    assert naver_flows.parse_page("<html><table><tr><td>1</td></tr></table></html>").empty


def test_fetch_stops_once_start_date_is_covered(html):
    """시작일을 덮은 페이지에서 멈춰야 한다 (불필요한 요청 방지)."""
    requested = []

    def fake_fetcher(symbol, page):
        requested.append(page)
        return html

    flows = naver_flows.fetch(
        "005930", date(2026, 9, 17), date(2026, 9, 21), page_fetcher=fake_fetcher
    )
    assert requested == [1]
    assert len(flows) == 3


def test_fetch_pages_until_max_when_range_is_long(html):
    requested = []

    def fake_fetcher(symbol, page):
        requested.append(page)
        return html

    naver_flows.fetch(
        "005930", date(2020, 1, 1), date(2026, 9, 21), max_pages=4, page_fetcher=fake_fetcher
    )
    assert requested == [1, 2, 3, 4]


def test_fetch_trims_rows_outside_the_range(html):
    flows = naver_flows.fetch(
        "005930", date(2026, 9, 18), date(2026, 9, 21),
        page_fetcher=lambda symbol, page: html,
    )
    assert flows.index.min().date() == date(2026, 9, 18)
    assert flows.index.max().date() == date(2026, 9, 21)


def test_fetch_returns_empty_when_every_page_fails():
    def broken(symbol, page):
        raise ConnectionError("차단")

    assert naver_flows.fetch("005930", date(2026, 1, 1), date(2026, 9, 21),
                             page_fetcher=broken).empty


def test_fetch_stops_on_empty_page(html):
    pages = {1: html}

    def fetcher(symbol, page):
        return pages.get(page, "<html><body>조회 결과가 없습니다</body></html>")

    flows = naver_flows.fetch(
        "005930", date(2000, 1, 1), date(2026, 9, 21), page_fetcher=fetcher
    )
    assert len(flows) == 3
