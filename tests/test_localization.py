from concurrent.futures import ThreadPoolExecutor
import io

import openpyxl
from openpyxl.styles import Font

from app.core.localization import bilingual_aliases, import_value, request_locale
from app.core.workbook_localization import localize_workbook
from app.services.contract_spreadsheets import _match_header
from app.services.import_export import CUSTOMER_IMPORT_ALIASES, FOLLOW_UP_IMPORT_ALIASES, DEPOSIT_IMPORT_ALIASES, _read_import_rows
from app.services.rooms import ROOM_IMPORT_HEADERS


def workbook_bytes(headers, row):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.append(row)
    return wb


def test_english_workbook_preserves_business_names_amounts_and_dates():
    wb = workbook_bytes(["客户名称", "备注", "合同编号", "签约类型", "月租金", "起租日"], ["保存", "在租", "CN-01", "续签", 1234.56, "2026-11-11"])
    token = request_locale.set("en")
    try:
        localize_workbook(wb)
    finally:
        request_locale.reset(token)
    assert list(wb.active.values)[0] == ("Customer name", "Notes", "Lease number", "Lease type", "Monthly rent", "Lease start")
    assert list(wb.active.values)[1] == ("保存", "在租", "CN-01", "Renewal", 1234.56, "2026-11-11")
    assert _match_header("Lease number") == ("contract_no", True)
    assert _match_header("Lease type") == ("lease_type", True)
    assert ROOM_IMPORT_HEADERS["unittype"] == "room_type"
    assert import_value("lease_type", "Renewal") == "续签"
    assert import_value("room_type", "Office") == "办公"
    assert import_value("name", "Renewal") == "Renewal"


def test_customer_followup_and_deposit_english_templates_roundtrip():
    cases = [
        (CUSTOMER_IMPORT_ALIASES, ["客户名称", "联系人", "状态"], ["保存", "新签", "active"], {"name": "保存", "contact_name": "新签", "status": "active"}),
        (FOLLOW_UP_IMPORT_ALIASES, ["客户名称", "跟进类型", "跟进内容"], ["Tenant A", "电话", "保存"], {"customer_name": "Tenant A", "follow_up_type": "电话", "content": "保存"}),
        (DEPOSIT_IMPORT_ALIASES, ["合同编号", "押金类型", "应收金额"], ["L-001", "租赁押金", 1000], {"contract_no": "L-001", "deposit_type": "租赁押金", "expected_amount": 1000}),
    ]
    for aliases, headers, values, expected in cases:
        for language in ["en", "zh-CN"]:
            wb = workbook_bytes(headers, values)
            token = request_locale.set(language)
            try:
                localize_workbook(wb)
            finally:
                request_locale.reset(token)
            data = io.BytesIO()
            wb.save(data)
            rows = _read_import_rows(data.getvalue(), aliases)
            assert rows == [(2, expected)]


def test_simultaneous_exports_have_isolated_languages():
    def export(language):
        token = request_locale.set(language)
        try:
            wb = workbook_bytes(["合同编号", "签约类型"], ["L-001", "新签"])
            localize_workbook(wb)
            return wb.active["A1"].value
        finally:
            request_locale.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(export, ["en", "zh-CN"] * 5)) == ["Lease number", "合同编号"] * 5


def test_http_templates_and_real_english_unit_import():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import SessionLocal
    from app.models import Room
    with TestClient(app) as client:
        client.post('/api/auth/register', json={'username': 'locale_test', 'password': 'LocaleTest123', 'display_name': 'Locale test'})
        assert client.post('/api/auth/login', json={'username': 'locale_test', 'password': 'LocaleTest123'}).status_code == 200
        for path, label in [
            ('/api/rooms/export/template', 'Tower'),
            ('/api/contracts/export/template', 'Lease number'),
            ('/api/customers/import/template', 'Customer name'),
            ('/api/customers/follow-ups/import/template', 'Customer name'),
            ('/api/contracts/deposits/import/template', 'Lease number'),
        ]:
            response = client.get(path, params={'building': 'DEMO_A', 'lang': 'en'})
            assert response.status_code == 200, response.text
            assert response.headers['Content-Language'] == 'en'
            workbook = openpyxl.load_workbook(io.BytesIO(response.content))
            assert workbook.active['A1'].value == label
        # Fill the actual downloaded English template and import through the API.
        workbook = openpyxl.load_workbook(io.BytesIO(client.get('/api/rooms/export/template?building=DEMO_A&lang=en').content))
        worksheet = workbook.active
        for row in worksheet.iter_rows(min_row=2):
            for cell in row:
                cell.value = None
        for col, value in enumerate(['South Tower', '18', 'I18N-1801', 'Office', 123.45, '保存'], 1):
            worksheet.cell(2, col, value)
        content = io.BytesIO()
        workbook.save(content)
        response = client.post('/api/rooms/import?building=DEMO_A', files={'file': ('units.xlsx', content.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        assert response.status_code == 200, response.text
        assert response.json()['added'] == 1
        with SessionLocal() as db:
            room = db.query(Room).filter(Room.room_no == 'I18N-1801').one()
            assert room.room_type == '办公'
            assert room.area == 123.45
            assert room.notes == '保存'
        chinese = client.get('/api/rooms/export/template?building=DEMO_A&lang=zh-CN')
        assert openpyxl.load_workbook(io.BytesIO(chinese.content)).active['A1'].value == '楼塔'
