"""네이버 금융에서 외국인·기관 순매매량을 읽어온다.

KRX 일별추이 API(pykrx)가 막히는 경우가 잦아 대체 경로로 둔다.
표 구조가 바뀔 수 있으므로 컬럼은 이름 키워드로 찾는다.
"""

from __future__ import annotations

from datetime import date
from io import StringIO

import pandas as pd

URL = "https://finance.naver.com/item/frgn.naver"
#: 네이버는 기본 User-Agent 로는 응답하지 않는다
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}
#: 한 페이지에 20 영업일 (약 한 달)
ROWS_PER_PAGE = 20
MAX_PAGES = 20


#: 표를 제대로 읽었는지 확인할 때 쓰는 머리글
MARKER = "날짜"


def request_page(symbol: str, page: int, timeout: float = 10.0):
    import requests

    response = requests.get(
        URL, params={"code": symbol, "page": page}, headers=HEADERS, timeout=timeout
    )
    response.raise_for_status()
    return response


def decode(content: bytes) -> tuple[str, str]:
    """인코딩을 판별해 (본문, 사용한 인코딩)을 돌려준다.

    네이버 금융은 예전부터 EUC-KR 이지만 페이지마다 다를 수 있어 확인 후 고른다.
    잘못 디코딩하면 한글 머리글이 깨져 표를 못 찾는다.
    """
    for encoding in ("euc-kr", "cp949", "utf-8"):
        try:
            text = content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if MARKER in text:
            return text, encoding
    return content.decode("utf-8", errors="replace"), "utf-8(대체)"


def fetch_page(symbol: str, page: int, timeout: float = 10.0) -> str:
    return decode(request_page(symbol, page, timeout).content)[0]


def diagnose(symbol: str, page: int = 1) -> dict:
    """왜 비었는지 보기 위한 진단 정보. doctor 명령에서 쓴다."""
    info: dict[str, object] = {}
    response = request_page(symbol, page)
    info["status"] = response.status_code
    info["bytes"] = len(response.content)
    info["declared_encoding"] = response.encoding

    text, used = decode(response.content)
    info["decoded_with"] = used
    info["has_marker"] = MARKER in text
    info["snippet"] = " ".join(text[:400].split())

    try:
        tables = pd.read_html(StringIO(text))
    except Exception as exc:
        info["tables"] = f"{type(exc).__name__}: {exc}"
        return info

    info["tables"] = [
        [" ".join(str(part) for part in col) if isinstance(col, tuple) else str(col)
         for col in table.columns]
        for table in tables
    ]
    info["parsed_rows"] = len(parse_page(text))
    return info


def parse_page(html: str) -> pd.DataFrame:
    """페이지 HTML에서 (date, foreign_net, inst_net) 프레임을 뽑는다."""
    tables = pd.read_html(StringIO(html))
    for table in tables:
        parsed = _parse_table(table)
        if parsed is not None and not parsed.empty:
            return parsed
    return pd.DataFrame()


def _parse_table(table: pd.DataFrame) -> pd.DataFrame | None:
    # 헤더가 2단이면 합쳐서 '외국인 순매매량' 같은 이름으로 만든다
    if isinstance(table.columns, pd.MultiIndex):
        names = [" ".join(str(part) for part in col) for col in table.columns]
    else:
        names = [str(col) for col in table.columns]

    def find(*keywords: str) -> int | None:
        for i, name in enumerate(names):
            if all(keyword in name for keyword in keywords):
                return i
        return None

    date_idx = find("날짜")
    foreign_idx = find("외국인", "순매매")
    inst_idx = find("기관", "순매매")
    if date_idx is None or (foreign_idx is None and inst_idx is None):
        return None

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(table.iloc[:, date_idx], errors="coerce")
    if foreign_idx is not None:
        out["foreign_net"] = pd.to_numeric(table.iloc[:, foreign_idx], errors="coerce")
    if inst_idx is not None:
        out["inst_net"] = pd.to_numeric(table.iloc[:, inst_idx], errors="coerce")

    out = out.dropna(subset=["date"]).set_index("date")
    out.index = out.index.normalize()
    out.index.name = "date"
    return out.sort_index()


def fetch(
    symbol: str,
    start: date,
    end: date,
    max_pages: int = MAX_PAGES,
    page_fetcher=fetch_page,
) -> pd.DataFrame:
    """start~end 구간을 덮을 때까지 페이지를 넘겨가며 받는다."""
    frames: list[pd.DataFrame] = []
    for page in range(1, max_pages + 1):
        try:
            parsed = parse_page(page_fetcher(symbol, page))
        except Exception:
            if page == 1:
                raise  # 첫 페이지 실패는 조용히 넘기지 않는다
            break
        if parsed.empty:
            break
        frames.append(parsed)
        if parsed.index[0].date() <= start:  # 이 페이지가 시작일을 넘어섰다
            break

    if not frames:
        return pd.DataFrame()

    merged = pd.concat(frames)
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    return merged.loc[str(start) : str(end)]
