"""엑셀(.xlsx) 저장 — 숫자는 숫자로 두고 단위만 표시한다.

셀에 "1.2조" 같은 글자를 넣으면 읽기는 쉬워도 정렬·합계·필터가 모두
깨진다. 그래서 값은 숫자로 저장하고 엑셀 표시 형식으로 단위를 붙인다.
셀을 클릭하면 원래 숫자가 보이고, 화면에는 `3,500억` 으로 나온다.

단위를 섞지 않는 이유: 한 칸에 억과 조를 섞으면 정렬이 뒤섞인다.
금액은 전부 억으로 통일한다 (1조 = 10,000억).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .screener import EXPORT_LABELS, export_frame

#: 컬럼별 엑셀 표시 형식. 키는 내부 컬럼명.
#: 엑셀 표시 형식은 1000 단위로만 나눌 수 있어(쉼표 하나당 ÷1000) 억(10^8)과
#: 만(10^4)은 쉼표로 만들 수 없다. 그래서 값을 억 단위로 저장해 두고
#: 접미사만 붙인다.
NUMBER_FORMATS = {
    "close": '#,##0"원"',
    "chg_pct": '[Red]-0.00"%";[Blue]+0.00"%";0.00"%"',
    "score": "0.0%",
    "overall": "0.0%",
    "matched_ratio": "0.0%",
    "gap": "0.000",
    "turnover_20d": '#,##0"억"',
    "marcap": '#,##0"억"',
    "foreign_value_5d": '[Red]-#,##0"억";[Blue]+#,##0"억";0"억"',
    "foreign_value_20d": '[Red]-#,##0"억";[Blue]+#,##0"억";0"억"',
    "inst_value_5d": '[Red]-#,##0"억";[Blue]+#,##0"억";0"억"',
    "inst_value_20d": '[Red]-#,##0"억";[Blue]+#,##0"억";0"억"',
    "foreign_net_5d": '#,##0"주"',
    "foreign_net_20d": '#,##0"주"',
    "inst_net_5d": '#,##0"주"',
    "inst_net_20d": '#,##0"주"',
    "both_buy_days_20d": '0"일"',
    "matched": "0",
    "of": "0",
}

#: 조건·전략 점수 컬럼은 접두사로 알아본다
SCORE_PREFIXES = ("s_", "p_")
SCORE_FORMAT = "0.0%"

#: 너무 좁으면 ### 으로 보인다
WIDTHS = {"종목명": 22, "맞는 전략": 26, "기준일": 12}
DEFAULT_WIDTH = 15


def available() -> bool:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return False
    return True


def save(result: pd.DataFrame, path: str | Path, sheet: str = "찾은종목") -> Path:
    """결과를 엑셀로 저장한다. 머리글은 한글, 값은 숫자 + 단위 서식."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    frame = export_frame(result)
    path = Path(path)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name=sheet)
        ws = writer.sheets[sheet]

        # 내부 컬럼명 → 저장 머리글 → 엑셀 열 번호
        label_of = {col: EXPORT_LABELS.get(col, col) for col in result.columns}
        column_at = {str(cell.value): cell.column for cell in ws[1]}

        for internal, label in label_of.items():
            index = column_at.get(label)
            if index is None:
                continue
            fmt = NUMBER_FORMATS.get(internal)
            if fmt is None and internal.startswith(SCORE_PREFIXES):
                fmt = SCORE_FORMAT
            if fmt is None:
                continue
            for row in range(2, ws.max_row + 1):
                ws.cell(row=row, column=index).number_format = fmt

        # 머리글을 굵게, 첫 행 고정, 자동 필터
        header_fill = PatternFill("solid", start_color="FFEFEFEF")
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for cell in ws[1]:
            letter = get_column_letter(cell.column)
            ws.column_dimensions[letter].width = WIDTHS.get(
                str(cell.value), DEFAULT_WIDTH
            )
    return path
