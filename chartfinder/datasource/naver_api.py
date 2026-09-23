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


def fetch_flows(symbol: str, start: date, end: date) -> pd.DataFrame:
    """외국인·기관 일별 순매수 (주식 수)."""
    _, data = try_paths(symbol, TREND_PATHS, {"pageSize": 100, "page": 1})
    records = find_records(data)
    if not records:
        return pd.DataFrame()

    rows = []
    for record in records:
        day = _pick(record, DATE_KEYS)
        if day is None:
            continue
        row = {"date": pd.to_datetime(str(day), errors="coerce", format="mixed")}
        for target, keywords in FLOW_KEYS.items():
            value = _number(_pick(record, keywords))
            if value is not None:
                row[target] = value
        if len(row) > 1:
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).dropna(subset=["date"]).set_index("date")
    df.index = df.index.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.loc[str(start) : str(end)]


def fetch_fundamentals(symbol: str) -> pd.DataFrame:
    """연간 재무 지표."""
    from ..fundamentals import normalize

    _, data = try_paths(symbol, FINANCE_PATHS)
    records = find_records(data)
    if not records:
        return pd.DataFrame()

    rows = {}
    for record in records:
        period = _pick(record, PERIOD_KEYS)
        if period is None:
            continue
        row = {}
        for target, keywords in FINANCE_KEYS.items():
            value = _number(_pick(record, keywords))
            if value is not None:
                row[target] = value
        if row:
            rows[str(period)] = row

    return normalize(pd.DataFrame(rows).T) if rows else pd.DataFrame()


def probe(symbol: str) -> dict:
    """어떤 엔드포인트가 살아 있고 어떤 키를 주는지 확인한다."""
    report: dict[str, Any] = {}
    for label, paths in (("trend", TREND_PATHS), ("finance", FINANCE_PATHS)):
        for base in BASES:
            for path in paths:
                url = f"{base}/{path.format(code=symbol)}"
                try:
                    data = get_json(url, {"pageSize": 5, "page": 1})
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
