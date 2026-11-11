from __future__ import annotations

import openpyxl
from openpyxl.cell.cell import MergedCell

# Excel/WPS 会把以这些字符开头的单元格按公式执行（CSV/XLSX 公式注入），
# 客户名称、备注等用户可控文本导出前必须中和。
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell_text(value: str) -> str:
    if value and value.startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value


def sanitize_workbook(workbook: openpyxl.Workbook) -> openpyxl.Workbook:
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                if isinstance(cell.value, str):
                    cell.value = sanitize_cell_text(cell.value)
    return workbook
