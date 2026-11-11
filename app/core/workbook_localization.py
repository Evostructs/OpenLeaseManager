"""Localize workbook presentation without translating tenant-owned cell data."""
from copy import copy
import re

from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter

from app.core.localization import display, request_locale
from app.core.currency_unit import request_currency
from app.core.presentation_units import request_area, request_fx, request_fx_date
from app.core.workbook_units import convert_workbook_cells, add_workbook_units, presentation_header

ENUM_HEADERS = {
    "签约类型", "租赁类型", "合同类型", "类型", "房间类型", "物业类型", "用途", "出租状态",
    "合同状态", "到期处置", "押金状态", "跟进类型", "押金类型", "状态", "发票状态", "催收状态",
    "风险等级", "风险类型", "风险事项", "处理状态",
}
RAW_HEADERS = {
    "客户名称", "租户名称", "客户", "租户", "名称", "客户简称", "简称", "备注", "说明",
    "联系人", "客户方对接人", "我方对接人", "楼宇联系人", "楼宇负责人", "楼宇备注", "负责人",
    "跟进内容", "内容", "下一步", "下一步动作", "地址", "楼塔", "塔楼", "楼层", "室号",
    "分区代码", "分区名称", "合同编号", "合同号", "房号", "楼宇名称", "操作人", "变更摘要",
}
NOTE_SHEETS = {"导入说明", "填写说明", "使用说明", "说明", "导出说明"}


def localize_workbook(workbook):
    if request_locale.get() != "en" and request_currency.get() == "CNY" and request_area.get() == "m2" and request_fx.get() == 1:
        return workbook
    for ws in workbook.worksheets:
        original_title = ws.title
        is_template = "模板" in original_title
        is_note = original_title in NOTE_SHEETS
        headers = {}
        header_row = 0
        # Existing exporters mark their actual header row in bold; merged report
        # titles contain one populated cell and do not qualify as table headers.
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 5)):
            filled = [cell for cell in row if cell.value is not None and not isinstance(cell, MergedCell)]
            if len(filled) >= 2 and all(cell.font.bold for cell in filled):
                header_row = row[0].row
                headers = {cell.column: str(cell.value) for cell in filled}
                break
        convert_workbook_cells(ws, headers, header_row)
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell, MergedCell):
                    continue
                if cell.comment:
                    comment = copy(cell.comment)
                    comment.text = display(comment.text)
                    cell.comment = comment
                value = cell.value
                if not isinstance(value, str) or cell.data_type == "f":
                    continue
                label = headers.get(cell.column, "")
                if is_note or cell.row <= header_row or label in ENUM_HEADERS:
                    cell.value = display(value)
                elif is_template and label in {"楼塔", "塔楼"} and getattr(workbook, "_demo_tower_values", False):
                    cell.value = display(value)
                elif is_template and label not in {"楼塔", "塔楼", "分区名称", "分区代码", "合同编号", "合同号", "室号", "房号"}:
                    # Template examples are synthetic. Live export rows never use this branch.
                    cell.value = display(value)
                if cell.row == header_row and cell.column in headers:
                    cell.value = presentation_header(headers[cell.column], cell.value)
                if cell.row <= header_row or is_note:
                    alignment = copy(cell.alignment)
                    alignment.wrap_text = True
                    cell.alignment = alignment
        for validation in ws.data_validations.dataValidation:
            if validation.type == "list" and isinstance(validation.formula1, str) and validation.formula1.startswith('"'):
                options = validation.formula1.strip('"').split(',')
                validation.formula1 = '"' + ','.join(display(item) for item in options) + '"'
            if validation.prompt:
                validation.prompt = display(validation.prompt)
            if validation.error:
                validation.error = display(validation.error)
        for col, label in headers.items():
            dimension = ws.column_dimensions[get_column_letter(col)]
            dimension.width = max(dimension.width or 10, min(38, len(display(label)) + 3), 22 if label in {"楼塔", "塔楼"} else 10)
        title = re.sub(r'[\\/*?:\[\]]', '-', display(original_title))[:31]
        if title and title != original_title:
            ws.title = title
    if request_currency.get() != "CNY" and not request_fx_date.get():
        unit_sheet = workbook.create_sheet("Currency unit" if request_locale.get() == "en" else "货币单位")
        unit_sheet.append(["Currency unit" if request_locale.get() == "en" else "货币单位", request_currency.get()])
        unit_sheet.append(["Note" if request_locale.get() == "en" else "说明", "Amounts use the selected unit. No exchange-rate conversion is applied." if request_locale.get() == "en" else "金额按所选货币单位标示，不进行汇率换算。"])
        unit_sheet.column_dimensions["A"].width = 24
        unit_sheet.column_dimensions["B"].width = 80
    if request_area.get() != "m2" or request_fx_date.get():
        add_workbook_units(workbook, request_locale.get())
    return workbook
