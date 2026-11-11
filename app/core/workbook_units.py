"""Unit-aware spreadsheet boundaries. User text and identifiers are never converted."""
import re

from fastapi import HTTPException

from app.core.presentation_units import (
    SQM_PER_SQFT, area_factor, request_area, request_fx, request_fx_date, request_book_currency,
)
from app.core.currency_unit import request_currency


def column_dimension(label):
    label = str(label or "")
    if any(term in label for term in ("率", "日期", "月份", "年度", "年份", "账期", "期间", "序号", "数量", "个数", "笔数", "份数", "户数", "间数", "记录数", "人数", "天数", "编号", "编码", "代码", "负责人", "联系人", "ID", "类型", "状态", "说明", "来源")) and label != "面积天数":
        return ""
    if any(term in label for term in ("单价", "均价", "平均日租", "日租单价", "物业费标准")):
        return "price"
    if "面积" in label:
        return "area"
    if any(term in label for term in ("金额", "租金", "押金", "佣金", "应收", "已收", "未收", "余额", "退款", "冲抵", "收入", "支出", "费用", "现金流", "成本", "预算", "EBITDA")):
        return "money"
    return ""


def convert_workbook_cells(ws, headers, header_row):
    factors = {"area": area_factor(), "price": request_fx.get() / area_factor(), "money": request_fx.get()}
    for col, label in headers.items():
        factor = factors.get(column_dimension(label), 1)
        if factor == 1:
            continue
        for row in range(header_row + 1, ws.max_row + 1):
            cell = ws.cell(row, col)
            if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                cell.value *= factor
                cell.number_format = "#,##0.000000" if column_dimension(label) == "price" else "#,##0.00"


def presentation_header(original, translated):
    kind = column_dimension(original)
    if request_area.get() == "ft2":
        if kind == "area" and "ft²" not in translated:
            return f"{translated} (ft²)"
        if kind == "price" and "ft²" not in translated:
            period = "month" if "物业" in original else "day"
            return f"{translated} ({request_currency.get()}/ft²/{period})"
    if request_fx_date.get() and kind == "money" and request_currency.get() not in translated:
        return f"{translated} ({request_currency.get()})"
    return translated


def add_workbook_units(workbook, language):
    sheet = workbook.create_sheet("Units" if language == "en" else "单位")
    sheet.append(["Book currency", request_book_currency.get()])
    sheet.append(["View currency", request_currency.get()])
    sheet.append(["Area unit", request_area.get()])
    sheet.append(["Exchange rate", request_fx.get()])
    sheet.append(["Rate date", request_fx_date.get()])
    sheet.append(["Note", "Reference conversion only; original records are unchanged." if request_fx_date.get() else "Area and unit prices are converted together; rent totals are unchanged."])
    sheet.column_dimensions["A"].width = 24
    sheet.column_dimensions["B"].width = 80


def normalize_import_workbook(workbook):
    """Restore canonical square metres before existing import validation runs."""
    from app.core.localization import MESSAGES
    reverse = {value: key for key, value in MESSAGES.items()}
    metadata = {}
    for name in ("Units", "单位", "Currency unit", "货币单位"):
        if name in workbook.sheetnames:
            for row in workbook[name].iter_rows(values_only=True):
                if len(row) >= 2 and row[0] is not None:
                    metadata[str(row[0])] = row[1]
    currency = metadata.get("View currency") or metadata.get("Currency unit") or metadata.get("货币单位")
    if currency and currency != request_book_currency.get():
        raise HTTPException(400, "Import currency must match the book currency; reference FX exports cannot be imported")
    if float(metadata.get("Exchange rate") or 1) != 1:
        raise HTTPException(400, "Reference FX exports cannot be imported")
    declared_area = metadata.get("Area unit")
    if declared_area not in {None, "m2", "ft2"}:
        raise HTTPException(400, "Unsupported area unit in workbook")
    if declared_area != "ft2":
        for ws in workbook.worksheets:
            if ws.title in {"Units", "单位", "Currency unit", "货币单位"}:
                continue
            for row in ws.iter_rows(min_row=1, max_row=min(5, ws.max_row), values_only=True):
                if any(isinstance(v, str) and any(unit in v for unit in ("ft²", "sqft", "平方英尺")) for v in row):
                    raise HTTPException(400, "Square-foot imports must declare Area unit = ft2 in the Units sheet. Use the matching import template.")
        return workbook
    for ws in workbook.worksheets:
        if ws.title in {"Units", "单位", "Currency unit", "货币单位"}:
            continue
        # Imports already use the first recognizable header row; units exports
        # keep the same header and sheet locations as their metric equivalents.
        for row in ws.iter_rows(min_row=1, max_row=min(5, ws.max_row)):
            headers = {}
            for cell in row:
                if not isinstance(cell.value, str):
                    continue
                label = re.sub(r"\s*\([A-Z]{3}/ft²/(?:day|month)\)$", "", cell.value)
                # Suffix added to otherwise unit-less headers by presentation_header.
                stripped = re.sub(r"\s+\(ft²\)$", "", label)
                if stripped in reverse or column_dimension(stripped) in {"area", "price"}:
                    label = stripped
                canonical = label.replace("ft²", "㎡" if re.search(r"[\u3400-\u9fff]", label) else "m²").replace("sqft", "sqm").replace("平方英尺", "平方米")
                original = reverse.get(canonical, canonical)
                kind = column_dimension(original)
                if kind in {"area", "price"}:
                    headers[cell.column] = kind
                cell.value = original
            if not headers:
                continue
            for col, kind in headers.items():
                factor = float(SQM_PER_SQFT) if kind == "area" else 1 / float(SQM_PER_SQFT)
                for i in range(row[0].row + 1, ws.max_row + 1):
                    cell = ws.cell(i, col)
                    if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                        cell.value *= factor
            break
    return workbook
