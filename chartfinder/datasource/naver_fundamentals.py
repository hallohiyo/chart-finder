"""네이버 금융 '기업실적분석' 표에서 연간 재무 지표를 읽는다.

매출액·영업이익·영업이익률·ROE·부채비율을 제공한다.
영업활동현금흐름은 이 표에 없어 비워둔다 (DART 등 별도 경로가 필요).
"""

from __future__ import annotations

from io import StringIO

import pandas as pd

from ..fundamentals import normalize
from .naver_flows import HEADERS, decode

URL = "https://finance.naver.com/item/main.naver"
#: 이 표가 맞는지 확인할 항목들
REQUIRED_ROWS = ("매출액", "영업이익")
#: 연간 실적 컬럼을 구분하는 머리글
ANNUAL_MARKER = "연간"
#: 컨센서스(추정치) 표시. 실적이 아니므로 기본적으로 제외한다.
ESTIMATE_MARKER = "(E)"


def request_page(symbol: str, timeout: float = 10.0):
    import requests

    response = requests.get(
        URL, params={"code": symbol}, headers=HEADERS, timeout=timeout
    )
    response.raise_for_status()
    return response


def fetch_page(symbol: str, timeout: float = 10.0) -> str:
    return decode(request_page(symbol, timeout).content)[0]


def parse_page(html: str, include_estimates: bool = False) -> pd.DataFrame:
    """페이지에서 기업실적분석 표를 찾아 연간 실적만 표준 스키마로.

    2025.12(E) 같은 컨센서스는 실적이 아니므로 기본적으로 제외한다.
    """
    for table in pd.read_html(StringIO(html)):
        parsed = _parse_table(table, include_estimates)
        if parsed is not None and not parsed.empty:
            return parsed
    return pd.DataFrame()


def _parse_table(table: pd.DataFrame, include_estimates: bool = False) -> pd.DataFrame | None:
    labels = [str(v) for v in table.iloc[:, 0]]
    index_labels = [str(v) for v in table.index]
    # 항목 이름이 첫 컬럼에 있을 수도, 인덱스에 있을 수도 있다
    if all(any(req in label for label in labels) for req in REQUIRED_ROWS):
        table = table.set_index(table.columns[0])
    elif not all(any(req in label for label in index_labels) for req in REQUIRED_ROWS):
        return None

    annual = _annual_columns(table)
    if not include_estimates:
        annual = [c for c in annual if ESTIMATE_MARKER not in _period_label(c)]
    if not annual:
        return None

    frame = table[annual]
    frame.columns = [_period_label(col) for col in annual]
    # 행=항목, 열=기간 이므로 전치해서 표준 스키마에 맞춘다
    return normalize(frame.T)


def _annual_columns(table: pd.DataFrame) -> list:
    """'최근 연간 실적' 아래 컬럼만 고른다 (분기 실적 제외)."""
    columns = list(table.columns)
    if isinstance(table.columns, pd.MultiIndex):
        return [c for c in columns if any(ANNUAL_MARKER in str(part) for part in c)]
    return [c for c in columns if ANNUAL_MARKER in str(c)]


def _period_label(column) -> str:
    """('최근 연간 실적', '2024.12', 'IFRS연결') → '2024.12'"""
    parts = column if isinstance(column, tuple) else (column,)
    for part in parts:
        text = str(part)
        if text[:4].isdigit():
            return text
    return str(parts[-1])


def fetch(symbol: str, include_estimates: bool = False) -> pd.DataFrame:
    return parse_page(fetch_page(symbol), include_estimates)


def diagnose(symbol: str) -> dict:
    """왜 비었는지 보기 위한 진단 정보."""
    info: dict[str, object] = {}
    response = request_page(symbol)
    info["status"] = response.status_code
    info["bytes"] = len(response.content)

    text, used = decode(response.content)
    info["decoded_with"] = used
    info["has_marker"] = all(row in text for row in REQUIRED_ROWS)

    try:
        tables = pd.read_html(StringIO(text))
    except Exception as exc:
        info["tables"] = f"{type(exc).__name__}: {exc}"
        return info

    info["table_count"] = len(tables)
    for i, table in enumerate(tables):
        labels = [str(v) for v in table.iloc[:, 0]][:6]
        if any("매출액" in label for label in labels):
            info["candidate_table"] = i
            info["candidate_rows"] = labels
            info["candidate_columns"] = [str(c) for c in table.columns][:10]
    info["parsed_rows"] = len(parse_page(text))
    return info
