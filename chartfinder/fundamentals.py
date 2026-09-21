"""재무 데이터 스키마와 정규화.

연간 재무 지표를 한 종목당 한 프레임으로 다룬다.
  index : period (연도, 오름차순)
  cols  : revenue, operating_income, net_income, operating_margin,
          debt_ratio, roe, operating_cash_flow
값의 단위는 소스를 따른다 (증가·비율만 보므로 통화 단위는 상관없다).
비율(operating_margin / debt_ratio / roe)은 %.
"""

from __future__ import annotations

import pandas as pd

FUNDAMENTAL_COLUMNS = [
    "revenue",            # 매출액
    "operating_income",   # 영업이익
    "net_income",         # 당기순이익
    "operating_margin",   # 영업이익률 (%)
    "debt_ratio",         # 부채비율 (%)
    "roe",                # 자기자본이익률 (%)
    "operating_cash_flow",  # 영업활동현금흐름
]

#: 소스마다 다른 이름을 찾기 위한 키워드. 순서대로 먼저 맞는 것을 쓴다.
ALIASES = {
    "revenue": ("매출액", "수익(매출액)", "영업수익", "revenue", "totalrevenue"),
    "operating_income": ("영업이익", "operatingincome", "ebit"),
    "net_income": ("당기순이익", "순이익", "netincome"),
    "operating_margin": ("영업이익률", "operatingmargin"),
    "debt_ratio": ("부채비율", "debtratio"),
    "roe": ("roe", "자기자본이익률"),
    "operating_cash_flow": (
        "영업활동현금흐름", "영업활동으로인한현금흐름", "operatingcashflow",
        "cashflowfromcontinuingoperatingactivities", "totalcashfromoperatingactivities",
    ),
}


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """소스별 재무 프레임을 표준 스키마로 정리.

    행이 기간, 열이 항목인 형태를 기대한다 (전치된 형태는 소스 쪽에서 맞춘다).
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)

    df = df.copy()
    lookup = {_key(c): c for c in df.columns}

    out = pd.DataFrame(index=df.index)
    for target, keywords in ALIASES.items():
        column = _match(lookup, keywords)
        if column is not None:
            out[target] = pd.to_numeric(df[column], errors="coerce")

    out = _fill_derived(out)
    for col in FUNDAMENTAL_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[FUNDAMENTAL_COLUMNS]
    # 연도를 못 읽는 행('합계' 등)은 인덱스를 바꾸기 전에 걸러낸다.
    # 먼저 바꾸면 None 이 NaN 이 되면서 연도까지 float 로 딸려 바뀐다.
    periods = [_period(idx) for idx in out.index]
    out = out[[p is not None for p in periods]]
    out.index = [p for p in periods if p is not None]
    out.index.name = "period"
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(how="all")


def _fill_derived(df: pd.DataFrame) -> pd.DataFrame:
    """매출과 영업이익이 있으면 영업이익률은 계산할 수 있다."""
    if "operating_margin" not in df.columns and {"revenue", "operating_income"} <= set(df.columns):
        revenue = df["revenue"].replace(0, pd.NA)
        df["operating_margin"] = df["operating_income"] / revenue * 100.0
    return df


def _key(name: object) -> str:
    return str(name).strip().lower().replace(" ", "").replace("_", "").replace("(", "").replace(")", "")


def _match(lookup: dict[str, object], keywords: tuple[str, ...]) -> object | None:
    for keyword in keywords:
        key = _key(keyword)
        if key in lookup:
            return lookup[key]
        for candidate, original in lookup.items():
            if key in candidate:
                return original
    return None


def _period(value: object) -> int | None:
    """인덱스에서 연도를 뽑는다. '2024/12', Timestamp, 2024 모두 허용."""
    if isinstance(value, (int,)) and 1900 < value < 2200:
        return int(value)
    if isinstance(value, pd.Timestamp):
        return int(value.year)
    text = str(value).strip()
    for chunk in (text[:4], text[-4:]):
        if chunk.isdigit() and 1900 < int(chunk) < 2200:
            return int(chunk)
    return None
