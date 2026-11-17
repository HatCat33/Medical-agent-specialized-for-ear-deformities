"""Create downloadable workbooks for filtered patient interactions."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

HEADERS = (
    "分类",
    "标准问题",
    "其他问法",
    "答案内容",
    "有效日期",
    "关联知识1",
    "关联知识2",
    "关联知识3",
    "关联知识4",
    "关联知识5",
    "关联知识6",
    "关联知识7",
    "关联知识8",
    "关联知识9",
    "关联知识10",
)
CATEGORY = "用户交互"


def _clean_text(value: object) -> str:
    return ILLEGAL_CHARACTERS_RE.sub("", str(value or "").strip())


def build_interaction_workbook(items: list[dict[str, Any]], sheet_name: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    if sheet is None:
        raise RuntimeError("workbook_has_no_active_sheet")
    sheet.title = sheet_name
    sheet.sheet_view.zoomScale = 130
    sheet.freeze_panes = "A2"

    widths = (21, 32.453125, 15.36328125, 67.26953125, 28.1796875) + (13,) * 10
    thin = Side(style="thin", color="FF000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for column, (header, width) in enumerate(zip(HEADERS, widths, strict=True), start=1):
        cell = sheet.cell(1, column, header)
        cell.font = Font(name="宋体", size=11)
        cell.alignment = Alignment(vertical="center")
        cell.border = border
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.row_dimensions[1].height = 42

    for row_number, item in enumerate(items, start=2):
        created_at = item["created_at"]
        values = (
            CATEGORY,
            _clean_text(item["input"]),
            "",
            _clean_text(item["output"]),
            f"2088-01-01 {created_at:%H:%M}:{created_at.microsecond // 1000:03d}",
            *("" for _ in range(10)),
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row_number, column, value)
            cell.font = Font(name="宋体", size=11)
            cell.alignment = Alignment(
                vertical="center",
                wrap_text=column in {2, 4},
            )
            cell.border = border
            cell.number_format = "@"

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
