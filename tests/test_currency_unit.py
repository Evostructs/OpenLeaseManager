from datetime import date
import io

import openpyxl
from openpyxl.styles import Font
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import Contract, SystemConfig
from app.core.currency_unit import request_currency, currency_label
from app.core.localization import request_locale, display, bilingual_aliases, header_key
from app.core.workbook_localization import localize_workbook
from app.services.documents import _render_html
from test_financial_planning_api import admin, year, _seed_contract


def test_unit_setting_changes_labels_not_persisted_amounts(admin, year):
    cid = _seed_contract(year)
    with SessionLocal() as db:
        before = db.get(Contract, cid).monthly_rent
    assert admin.get('/api/settings/currency').json()['code'] == 'CNY'
    for code in ['USD', 'EUR', 'CNY']:
        result = admin.put('/api/settings/currency', json={'code': code})
        assert result.status_code == 200
        assert result.json()['code'] == code
        with SessionLocal() as db:
            assert db.get(Contract, cid).monthly_rent == before
        assert admin.get('/api/auth/status').headers['X-OpenLease-Currency'] == code
    assert admin.put('/api/settings/currency', json={'code': 'BAD'}).status_code == 422
    assert admin.get('/api/settings/currency').json()['code'] == 'CNY'


def test_unit_setting_requires_administrator(admin):
    secret = 'CurrencyViewer123'
    created = admin.post('/api/auth/create-user', json={'username': 'currency-viewer', 'password': secret, 'display_name': 'Viewer', 'role': 'viewer'})
    assert created.status_code == 200
    with TestClient(app) as viewer:
        assert viewer.post('/api/auth/login', json={'username': 'currency-viewer', 'password': secret}).status_code == 200
        assert viewer.put('/api/settings/currency', json={'code': 'USD'}).status_code == 403
        assert viewer.get('/api/auth/status').headers['X-OpenLease-Currency'] == 'CNY'
    with TestClient(app) as anonymous:
        assert anonymous.put('/api/settings/currency', json={'code': 'USD'}).status_code == 401


def test_workbook_unit_is_explicit_and_business_values_are_untouched():
    for language in ['en', 'zh-CN']:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['客户名称', '金额（元）', '备注'])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.append(['CNY Research', 1000.25, '人民币备注'])
        currency = request_currency.set('USD')
        locale = request_locale.set(language)
        try:
            localize_workbook(wb)
        finally:
            request_currency.reset(currency)
            request_locale.reset(locale)
        assert 'USD' in ws['B1'].value
        assert [cell.value for cell in ws[2]] == ['CNY Research', 1000.25, '人民币备注']
        assert wb.worksheets[-1]['B1'].value == 'USD'
        aliases = bilingual_aliases({'金额（元）': 'amount'})
        assert aliases[header_key(ws['B1'].value)] == 'amount'
        buffer = io.BytesIO()
        wb.save(buffer)
        assert openpyxl.load_workbook(io.BytesIO(buffer.getvalue())).active['B2'].value == 1000.25


def test_currency_labels_preserve_scale_and_do_not_replace_unrelated_words():
    assert currency_label('1,000.25 元', 'USD') == '1,000.25 USD'
    assert currency_label('¥1,000.25', 'USD') == 'USD 1,000.25'
    assert currency_label('万元', 'USD') == '万 USD'
    assert currency_label('CNY 10k', 'USD') == 'USD 10k'
    assert currency_label('单元 / 多元 / 美元 / 元素', 'USD') == '单元 / 多元 / 美元 / 元素'
    token = request_currency.set('USD')
    try:
        html = _render_html(document_type='receipt', title='收据', issuer='CNY Research', notice_date=date(2026, 11, 11), lead='收款记录', rows=[('金额（元）', '1,000.25'), ('客户名称', 'CNY Research')], payment_instruction='', notes='人民币备注')
        from app.services.reports import _money
        assert _money(1000.25) == 'USD 1,000.25'
        assert '金额（USD）' in html
        assert '1,000.25' in html and 'CNY Research' in html and '人民币备注' in html
    finally:
        request_currency.reset(token)


def test_actual_english_templates_use_saved_unit_without_conversion(admin):
    assert admin.put('/api/settings/currency', json={'code': 'USD'}).status_code == 200
    for endpoint in ['/api/rooms/export/template', '/api/contracts/export/template', '/api/customers/import/template', '/api/customers/follow-ups/import/template', '/api/contracts/deposits/import/template']:
        response = admin.get(endpoint, params={'building': 'DEMO_A', 'lang': 'en'})
        assert response.status_code == 200
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        assert wb['Currency unit']['B1'].value == 'USD'
        assert wb.active != wb['Currency unit']
