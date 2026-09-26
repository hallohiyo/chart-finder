"""화면·파일에 숫자를 사람이 읽는 형태로 쓰는 함수들.

tkinter 를 거치지 않아야 어디서나 시험할 수 있으므로 GUI 와 분리해 둔다.
"""

from __future__ import annotations


def _missing(value) -> bool:
    """None 과 NaN 을 한꺼번에 본다."""
    return value is None or (isinstance(value, float) and value != value)


def _shares(value) -> str:
    """순매수 주식 수를 만주 단위로. 부호를 남겨 순매도를 구분한다."""
    if _missing(value):
        return "-"
    value = float(value)
    if abs(value) >= 10_000:
        return f"{value / 10_000:+,.1f}만"
    return f"{value:+,.0f}"


def _days(value) -> str:
    if _missing(value):
        return "-"
    return f"{int(value)}일"


def _money(value) -> str:
    """거래대금(억원)."""
    if _missing(value):
        return "-"
    value = float(value)
    return f"{value / 10_000:,.1f}조" if value >= 10_000 else f"{value:,.0f}억"


def _matched(met, total) -> str:
    """이긴 전략에서 충족한 조건 수. '5/9' 처럼 쓴다.

    점수만 보면 왜 뽑혔는지 알 수 없다. 조건 9개 중 5개를 충족한 것과
    2개만 충족하고 나머지가 근접 점수로 채워진 것은 다르다.
    """
    if _missing(met) or _missing(total) or not total:
        return "-"
    return f"{int(met)}/{int(total)}"
