"""네이버 금융 JSON API.

새 네이버 금융은 화면을 자바스크립트로 그리기 때문에 HTML에 표가 없다.
대신 화면이 호출하는 JSON API를 직접 쓴다. 엔드포인트가 바뀔 수 있어
후보를 순서대로 시도하고, 응답 키는 이름 키워드로 찾는다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import pandas as pd

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json",
}

#: 같은 데이터를 주는 호스트 후보
BASES = ("https://m.stock.naver.com/api/stock", "https://api.stock.naver.com/stock")

#: 외국인·기관 매매동향 경로 후보
TREND_PATHS = ("{code}/trend", "{code}/investor", "{code}/foreignInstitution")
#: 연간 재무 경로 후보
FINANCE_PATHS = ("{code}/finance/annual", "{code}/finance/annual/summary", "{code}/finance")
#: 한 번에 요청할 수 있는 최대 건수. 크게 넣으면 404 가 돌아온다.
PAGE_SIZE = 20
#: 수급을 받을 때 넘길 최대 페이지 수
MAX_PAGES = 10

#: 응답 키를 표준 이름으로 잇는 키워드 (소문자 비교)
FLOW_KEYS = {
    "foreign_net": ("foreignerpurebuyquant", "foreignpurebuyquant", "foreigner", "외국인"),
    "inst_net": ("organpurebuyquant", "institutionpurebuyquant", "organ", "기관"),
    "indi_net": ("individualpurebuyquant", "individual", "개인"),
}
DATE_KEYS = ("localtradedat", "localdate", "bizdate", "tradedate", "date", "일자", "날짜")

FINANCE_KEYS = {
    "revenue": ("salesaccount", "sales", "revenue", "매출액"),
    "operating_income": ("operatingprofit", "operatingincome", "영업이익"),
    "net_income": ("netincome", "당기순이익"),
    "operating_margin": ("operatingprofitratio", "operatingmargin", "영업이익률"),
    "debt_ratio": ("debtratio", "부채비율"),
    "roe": ("roe", "자기자본이익률"),
    "operating_cash_flow": ("operatingcashflow", "영업활동현금흐름"),
}
PERIOD_KEYS = ("yearmonth", "period", "date", "term", "기간", "결산")


def get_json(url: str, params: dict | None = None, timeout: float = 10.0):
    import requests

    response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def try_paths(symbol: str, paths: Iterable[str], params: dict | None = None):
    """후보 경로를 차례로 두드려 처음 성공한 (url, json) 을 돌려준다."""
    errors = []
    for base in BASES:
        for path in paths:
            url = f"{base}/{path.format(code=symbol)}"
            try:
                data = get_json(url, params)
            except Exception as exc:
                errors.append(f"{url} → {type(exc).__name__}")
                continue
            if data:
                return url, data
            errors.append(f"{url} → 빈 응답")
    raise RuntimeError(" / ".join(errors[:6]))


def find_records(data: Any) -> list[dict]:
    """JSON 어딘가에 있는 '딕셔너리 리스트'를 찾는다 (응답 껍데기가 제각각이라)."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        best: list[dict] = []
        for value in data.values():
            found = find_records(value)
            if len(found) > len(best):
                best = found
        return best
    return []


def _pick(record: dict, keywords: Iterable[str]) -> Any:
    lower = {str(k).lower(): v for k, v in record.items()}
    for keyword in keywords:
        if keyword in lower:
            return lower[keyword]
    for keyword in keywords:
        for key, value in lower.items():
            if keyword in key:
                return value
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: Any) -> pd.Timestamp:
    """'20260923' 과 '2026-09-23' 을 모두 받는다."""
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(text, errors="coerce")


def parse_trend(records: list[dict]) -> pd.DataFrame:
    """매매동향 레코드를 (date × foreign_net/inst_net/indi_net) 로."""
    rows = []
    for record in records:
        day = parse_date(_pick(record, DATE_KEYS))
        if pd.isna(day):
            continue
        row = {"date": day}
        for target, keywords in FLOW_KEYS.items():
            value = _number(_pick(record, keywords))
            if value is not None:
                row[target] = value
        if len(row) > 1:
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("date")
    df.index = df.index.normalize()
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch_flows(symbol: str, start: date, end: date, max_pages: int = MAX_PAGES) -> pd.DataFrame:
    """외국인·기관 일별 순매수 (주식 수).

    한 페이지가 PAGE_SIZE 건이라 시작일을 덮을 때까지 페이지를 넘긴다.
    """
    url, data = try_paths(symbol, TREND_PATHS, {"pageSize": PAGE_SIZE, "page": 1})
    frames = [parse_trend(find_records(data))]

    for page in range(2, max_pages + 1):
        if frames[-1].empty or frames[-1].index[0].date() <= start:
            break
        try:
            more = parse_trend(find_records(get_json(url, {"pageSize": PAGE_SIZE, "page": page})))
        except Exception:
            break
        if more.empty:
            break
        frames.append(more)

    merged = pd.concat([f for f in frames if not f.empty]) if any(
        not f.empty for f in frames
    ) else pd.DataFrame()
    if merged.empty:
        return merged
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    return merged.loc[str(start) : str(end)]


def parse_finance(records: list[dict], include_estimates: bool = False) -> pd.DataFrame:
    """재무 응답을 (기간 × 항목) 프레임으로.

    응답은 항목이 행이고 기간이 열인 형태다:
        {"title": "매출액", "columns": {"202412": {"value": "3,008,709"}, ...}}
    아직 오지 않은 결산기(예: 오늘이 2026-09 인데 202612)는 컨센서스이므로 뺀다.
    """
    table: dict[str, dict[str, float]] = {}
    for record in records:
        title = record.get("title")
        columns = record.get("columns")
        if not title or not isinstance(columns, dict):
            continue
        for period, cell in columns.items():
            value = _number(cell.get("value") if isinstance(cell, dict) else cell)
            if value is None:
                continue
            if not include_estimates and _is_future_period(period):
                continue
            table.setdefault(str(period), {})[str(title)] = value

    if not table:
        return pd.DataFrame()

    from ..fundamentals import normalize

    return normalize(pd.DataFrame(table).T)


def _is_future_period(period: Any) -> bool:
    """'202612' 처럼 아직 끝나지 않은 결산기인지."""
    text = str(period).strip()
    if len(text) < 6 or not text[:6].isdigit():
        return False
    year, month = int(text[:4]), int(text[4:6])
    today = date.today()
    return (year, month) >= (today.year, today.month)


def fetch_fundamentals(symbol: str, include_estimates: bool = False) -> pd.DataFrame:
    """연간 재무 지표."""
    _, data = try_paths(symbol, FINANCE_PATHS)
    records = find_records(data)
    return parse_finance(records, include_estimates) if records else pd.DataFrame()


def probe(symbol: str) -> dict:
    """어떤 엔드포인트가 살아 있고 어떤 키를 주는지 확인한다."""
    report: dict[str, Any] = {}
    for label, paths in (("trend", TREND_PATHS), ("finance", FINANCE_PATHS)):
        for base in BASES:
            for path in paths:
                url = f"{base}/{path.format(code=symbol)}"
                try:
                    data = get_json(url, {"pageSize": 5, "page": 1})  # 크게 넣으면 404
                except Exception as exc:
                    report[url] = f"{type(exc).__name__}: {str(exc)[:80]}"
                    continue
                records = find_records(data)
                report[url] = {
                    "top_keys": list(data)[:10] if isinstance(data, dict) else f"list[{len(data)}]",
                    "records": len(records),
                    "record_keys": list(records[0])[:20] if records else [],
                    "sample": {k: records[0][k] for k in list(records[0])[:8]} if records else None,
                }
    return report
