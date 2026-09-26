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


# --------------------------------------------------------------------------- 지분·물량
#
# 최대주주 지분율과 CB/BW 발행 물량은 공시에만 있다. 시세 API 로는 못 받는다.

#: CB/BW 를 몇 년 전까지 거슬러 훑을지. 이미 전환됐을 수도 있어 너무 길게
#: 보면 실제보다 부풀려진다.
DILUTION_LOOKBACK_YEARS = 3


def fetch_major_holder_pct(symbol: str, year: int | None = None) -> float | None:
    """최대주주 및 특수관계인의 기말 지분율 (%). 못 받으면 None.

    사업보고서 '최대주주 현황' 의 합계(계) 행을 쓴다.
    """
    corp = corp_codes().get(symbol)
    if not corp:
        return None
    year = year or date.today().year - 1
    for bsns_year in (year, year - 1):
        try:
            payload = _get(
                "hyslrSttus.json",
                corp_code=corp,
                bsns_year=str(bsns_year),
                reprt_code=ANNUAL_REPORT,
            ).json()
        except Exception:
            continue
        if payload.get("status") != "000":
            continue
        pct = _major_pct_from_rows(payload.get("list") or [])
        if pct is not None:
            return pct
    return None


def _major_pct_from_rows(rows: list[dict]) -> float | None:
    """합계 행이 있으면 그걸, 없으면 개별 행을 더한다."""
    individual = 0.0
    for row in rows:
        name = str(row.get("nm", "")).strip()
        pct = _ratio(row.get("trmend_posesn_stock_qota_rt"))
        if pct is None:
            continue
        if name in ("계", "합계", "소계"):
            return pct
        individual += pct
    return individual if individual > 0 else None


def fetch_dilution_shares(symbol: str, years: int = DILUTION_LOOKBACK_YEARS) -> float | None:
    """CB/BW 로 새로 생길 수 있는 주식 수. 발행 공시가 없으면 0.0, 못 받으면 None.

    권면총액 ÷ 전환(행사)가액 으로 센다. 이미 전환·상환된 물량은 공시만으로는
    알 수 없어, 이 값은 상한으로 봐야 한다.
    """
    corp = corp_codes().get(symbol)
    if not corp:
        return None
    end = date.today()
    start = date(end.year - years, end.month, 1)
    total = 0.0
    seen_any = False
    for path, price_key in (("cvbdIsDecsn.json", "cv_prc"), ("bdwtIsDecsn.json", "ex_prc")):
        try:
            payload = _get(
                path,
                corp_code=corp,
                bgn_de=start.strftime("%Y%m%d"),
                end_de=end.strftime("%Y%m%d"),
            ).json()
        except Exception:
            continue
        status = payload.get("status")
        if status not in ("000", "013"):  # 013 = 조회된 데이터 없음
            continue
        seen_any = True
        for row in payload.get("list") or []:
            face = _amount(row.get("bd_fta"))
            price = _amount(row.get(price_key))
            if face and price and price > 0:
                total += face / price
    return total if seen_any else None


def _ratio(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None
