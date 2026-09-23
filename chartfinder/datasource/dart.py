"""금융감독원 DART 전자공시 — 영업활동현금흐름.

네이버는 현금흐름 항목을 주지 않아 이 경로가 필요하다. 무료 API 키가 있어야
하며(opendart.fss.or.kr에서 발급), 키가 없으면 조용히 비활성 상태로 둔다.

사업보고서 한 번 조회로 3개 연도(당기·전기·전전기)를 얻으므로 종목당 1요청이면 된다.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

BASE = "https://opendart.fss.or.kr/api"
#: 사업보고서 (연간)
ANNUAL_REPORT = "11011"
#: 연결재무제표 → 없으면 별도
STATEMENT_ORDER = ("CFS", "OFS")
#: 현금흐름표 구분
CASH_FLOW_DIV = "CF"
#: 영업활동현금흐름 계정명에 들어가는 말
OPERATING_KEYWORDS = ("영업활동", "영업으로부터")
#: 종목코드 ↔ 고유번호 표 유효기간
CORP_CODE_TTL_DAYS = 30


def api_key() -> str | None:
    """DART_API_KEY 환경변수. 없으면 이 경로는 쓰지 않는다."""
    key = os.environ.get("DART_API_KEY", "").strip()
    return key or None


def enabled() -> bool:
    return api_key() is not None


def _get(path: str, **params) -> Any:
    import requests

    key = api_key()
    if not key:
        raise RuntimeError(
            "DART 키가 없습니다. opendart.fss.or.kr 에서 발급받아 "
            "DART_API_KEY 환경변수에 넣어주세요."
        )
    response = requests.get(f"{BASE}/{path}", params={"crtfc_key": key, **params}, timeout=15)
    response.raise_for_status()
    return response


def _corp_code_path() -> Path:
    from ..cache import cache_home

    return cache_home() / "dart_corp_codes.json"


def corp_codes(refresh: bool = False) -> dict[str, str]:
    """{종목코드: DART 고유번호}. 전체 목록을 한 번 받아 캐시한다."""
    path = _corp_code_path()
    if not refresh and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = date.fromisoformat(payload["fetched_at"])
        if (date.today() - fetched).days <= CORP_CODE_TTL_DAYS:
            return payload["codes"]

    codes = _download_corp_codes()
    path.write_text(
        json.dumps({"fetched_at": date.today().isoformat(), "codes": codes}, ensure_ascii=False),
        encoding="utf-8",
    )
    return codes


def _download_corp_codes() -> dict[str, str]:
    """corpCode.xml 은 ZIP 으로 내려온다."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    response = _get("corpCode.xml")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        xml_bytes = archive.read(archive.namelist()[0])

    codes: dict[str, str] = {}
    for item in ET.fromstring(xml_bytes).iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        corp = (item.findtext("corp_code") or "").strip()
        if stock and corp:  # 상장사만 (비상장은 stock_code 가 비어 있다)
            codes[stock] = corp
    return codes


def fetch_cash_flow(symbol: str, year: int | None = None) -> pd.DataFrame:
    """영업활동현금흐름 (index=연도, column=operating_cash_flow).

    한 번 조회로 당기·전기·전전기 3개 연도를 얻는다.
    """
    corp = corp_codes().get(symbol)
    if not corp:
        return pd.DataFrame()

    # 사업보고서는 이듬해 3월경 공시되므로, 연초에는 재작년 보고서를 본다
    if year is None:
        today = date.today()
        year = today.year - 1 if today.month >= 4 else today.year - 2

    for division in STATEMENT_ORDER:
        payload = _get(
            "fnlttSinglAcntAll.json", corp_code=corp, bsns_year=str(year),
            reprt_code=ANNUAL_REPORT, fs_div=division,
        ).json()
        if payload.get("status") != "000":
            continue
        frame = parse_cash_flow(payload.get("list") or [], year)
        if not frame.empty:
            return frame
    return pd.DataFrame()


def parse_cash_flow(rows: list[dict], year: int) -> pd.DataFrame:
    """재무제표 응답에서 영업활동현금흐름 세 해치를 뽑는다."""
    for row in rows:
        if row.get("sj_div") != CASH_FLOW_DIV:
            continue
        name = str(row.get("account_nm", "")).replace(" ", "")
        if not any(keyword in name for keyword in OPERATING_KEYWORDS):
            continue

        values = {
            year: _amount(row.get("thstrm_amount")),
            year - 1: _amount(row.get("frmtrm_amount")),
            year - 2: _amount(row.get("bfefrmtrm_amount")),
        }
        values = {period: value for period, value in values.items() if value is not None}
        if values:
            return pd.DataFrame(
                {"operating_cash_flow": values.values()}, index=list(values)
            ).sort_index()
    return pd.DataFrame()


def _amount(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None
