import io
from datetime import date

import httpx
import openpyxl
import pytest

from app.core import presentation_units as units
from app.core.currency_unit import request_currency
from app.core.localization import request_locale
from app.core.workbook_localization import localize_workbook
from app.core.workbook_units import normalize_import_workbook
from app.database import SessionLocal
from app.models import Contract
from test_financial_planning_api import admin, year, _seed_contract


def test_reference_rate_validates_pair_and_caches_without_sending_records(monkeypatch):
    units._rates.clear()
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={'base': 'CNY', 'quote': 'USD', 'rate': 0.15, 'date': date.today().isoformat()}, request=httpx.Request('GET', url))
    monkeypatch.setattr(units.httpx, 'get', get)
    for _ in range(2):
        assert units.reference_rate('CNY', 'USD')['rate'] == 0.15
    assert len(calls) == 1
    assert calls[0][0] == 'https://api.frankfurter.dev/v2/rate/cny/usd'
    assert calls[0][1]['params'] == {'providers': 'ecb'}
    units._rates.clear()


def test_view_currency_is_reference_only_and_failure_does_not_mislabel_money(admin, year, monkeypatch):
    cid = _seed_contract(year)
    with SessionLocal() as db:
        before = db.get(Contract, cid).monthly_rent
    monkeypatch.setattr(units, 'reference_rate', lambda base, quote: {'base': base, 'quote': quote, 'rate': 0.15, 'date': '2026-01-01', 'source': 'test'})
    shown = admin.get('/api/auth/status', params={'view_currency': 'USD'}).json()['presentation']
    assert shown['base'] == 'CNY' and shown['quote'] == 'USD' and shown['rate'] == 0.15
    with SessionLocal() as db:
        assert db.get(Contract, cid).monthly_rent == before
    def unavailable(*args):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(units, 'reference_rate', unavailable)
    shown = admin.get('/api/auth/status', params={'view_currency': 'USD'}).json()['presentation']
    assert shown['available'] is False and shown['quote'] == 'CNY' and shown['rate'] == 1


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_square_foot_workbook_import_roundtrip_keeps_area_price_and_rent(language):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '租赁合同'
    ws.append(['合同编号', '出租面积', '租金单价', '月租金', '备注'])
    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    ws.append(['CNY 100', 100, 5, 15208.33, '100平方米 CNY'])
    area = units.request_area.set('ft2')
    locale = request_locale.set(language)
    try:
        localize_workbook(wb)
    finally:
        units.request_area.reset(area)
        request_locale.reset(locale)
    assert ws['B2'].value == pytest.approx(100 / 0.09290304)
    assert ws['C2'].value == pytest.approx(5 * 0.09290304)
    assert ws['B2'].value * ws['C2'].value == pytest.approx(500)
    assert ws['D2'].value == 15208.33 and ws['E2'].value == '100平方米 CNY'
    buffer = io.BytesIO()
    wb.save(buffer)
    restored = normalize_import_workbook(openpyxl.load_workbook(io.BytesIO(buffer.getvalue())))
    assert restored.active['B2'].value == pytest.approx(100)
    assert restored.active['C2'].value == pytest.approx(5)
    assert restored.active['D2'].value == 15208.33


def test_fx_export_marks_date_and_cannot_be_imported_as_original_records():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['金额（元）', '税率', '数量'])
    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    ws.append([1000, 5, 10])
    tokens = [(request_currency, request_currency.set('USD')), (units.request_fx, units.request_fx.set(0.15)), (units.request_fx_date, units.request_fx_date.set('2026-01-01'))]
    try:
        localize_workbook(wb)
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
    assert [cell.value for cell in ws[2]] == [150, 5, 10]
    assert wb['单位']['B5'].value == '2026-01-01'
    with pytest.raises(Exception, match='Import currency must match|Reference FX exports'):
        normalize_import_workbook(wb)


def test_reports_do_not_convert_counts_or_percentages():
    from app.services.reports import _money, _number, _percent
    token = units.request_fx.set(0.15)
    try:
        assert '150.00' in _money(1000)
        assert _number(10) == '10'
        assert _percent(60) == '60.0%'
    finally:
        units.request_fx.reset(token)


def test_http_reference_export_includes_rate_date_and_original_book_currency(admin, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'reference_rate', lambda base, quote: {'base': base, 'quote': quote, 'rate': 0.15, 'date': '2026-01-01'})
    response = admin.get('/api/contracts/export/template', params={'building': 'DEMO_A', 'lang': 'en', 'area_unit': 'ft2', 'view_currency': 'USD', 'unit_export': '1'})
    assert response.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(response.content))
    metadata = dict(workbook['Units'].values)
    assert metadata['Book currency'] == 'CNY'
    assert metadata['View currency'] == 'USD'
    assert metadata['Exchange rate'] == 0.15
    assert metadata['Rate date'] == '2026-01-01'


def test_non_cny_ledger_rejects_cny_only_legacy_snapshot_schema(admin):
    assert admin.put('/api/settings/currency', json={'code': 'USD'}).status_code == 200
    with SessionLocal() as db:
        with pytest.raises(Exception, match='requires CNY'):
            units.require_snapshot_book_currency(db)
