import hashlib
import io
import json
from datetime import date
from uuid import uuid4
import zipfile

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.core.handoff_integrity import verify_package
from app.finance_models import FinanceCash, FinancePeriod
from app.finance_schemas import PeriodPayload
from app.main import app
from app.models import Building, Contract, OperationLog, User
from app.services import finance_operations as store
from tests.test_financial_planning_api import admin, _seed_contract, _login
from tests.test_finance_operations import cash, allocate, expense, post, period_post, MONTH, YEAR, SCOPE

PREFIX = '/api/finance-operations/handoff'


def preview(client, mode='draft'):
    response = client.get(PREFIX+'/preview', params={**SCOPE, 'month': MONTH, 'mode': mode})
    assert response.status_code == 200, response.text
    return response.json()


def download(client, summary, code=200):
    response = client.get(PREFIX+'/export', params={**SCOPE, 'month': MONTH, 'mode': summary['mode'],
        'expected_revision': summary['revision'], 'expected_digest': summary['handoff_digest']})
    assert response.status_code == code, response.text if code != 200 else response.status_code
    return response


def package(response):
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-type'] == 'application/zip'
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None
    manifest = json.loads(archive.read('manifest.json'))
    assert manifest['schema'] == 'FINANCE_HANDOFF_V1'
    for name, evidence in manifest['files'].items():
        content = archive.read(name)
        assert len(content) == evidence['bytes']
        assert hashlib.sha256(content).hexdigest() == evidence['sha256']
    snapshot = json.loads(archive.read('snapshot.json'))
    assert store.digest(snapshot['facts']) == manifest['source_digest']
    wb = openpyxl.load_workbook(io.BytesIO(archive.read('finance-handoff.xlsx')))
    return archive, manifest, snapshot, wb


def close_expense_month(client):
    item = expense(client, 100)
    period_post(client, 'submit', confirm={'income': 'zero', 'receipts': 'zero', 'expenses': 'provided', 'payments': 'zero'})
    with SessionLocal() as db:
        bid = db.query(Building.id).filter_by(code='DEMO_A').scalar()
        store.period_action(db, bid, User(username='independent-reviewer', role='admin'), MONTH,
                           PeriodPayload(action='close', expected_revision=2, reason='独立核对完整资料'))
    return item


def test_draft_exports_all_rows_exact_cents_sanitized_cells_and_scoped_audit(admin):
    cid = _seed_contract(YEAR)
    row = cash(admin, 100.01, counterparty='=1+1')
    allocation = allocate(admin, row, cid, 100.01)
    post(admin, f'/allocations/{allocation["id"]}/reverse', {'expected_revision': 2, 'reason': '错误分配保留更正依据'})
    summary = preview(admin)
    archive, manifest, snapshot, wb = package(download(admin, summary))
    assert manifest['mode'] == 'draft' and manifest['cash_in_fen'] == 10001
    assert snapshot['facts']['allocations'][0]['voided'] is True
    assert wb['资金流水']['D2'].value == '100.01'
    assert wb['资金流水']['E2'].value == "'=1+1"
    assert wb['资金流水']['E2'].data_type != 'f'
    assert wb['分配明细']['K2'].value == '已撤销'
    assert '核对草稿' in wb['交接摘要']['B1'].value
    assert json.loads(archive.read('tables.json'))['tables']['资金流水']['rows'][0][3] == '100.01'
    with SessionLocal() as db:
        log = db.query(OperationLog).filter_by(action='finance_handoff_export').one()
        assert log.scope_type == 'building' and log.building_id == db.get(Contract, cid).building_id
        assert summary['source_digest'] in log.changes


def test_large_amount_preserves_decimal_text(admin):
    cash(admin, '999999999999.99')
    _, manifest, _, wb = package(download(admin, preview(admin)))
    assert manifest['cash_in_fen'] == 99999999999999
    assert wb['资金流水']['D2'].value == '999999999999.99'


def test_changed_sources_and_period_revision_reject_stale_preview_without_export_log(admin):
    first = preview(admin)
    cash(admin, 10)
    download(admin, first, 409)
    second = preview(admin)
    period_post(admin, 'save')
    download(admin, second, 409)
    with SessionLocal() as db:
        assert db.query(OperationLog).filter_by(action='finance_handoff_export').count() == 0


def test_closed_export_uses_frozen_sources_after_later_payment(admin, monkeypatch):
    e = close_expense_month(admin)
    before = preview(admin, 'closed')
    allocate(admin, cash(admin, 100, direction='out', occurred_on='2026-02-10'), None, 100,
             kind='expense', expense_id=e['id'])
    monkeypatch.setattr(store, 'period_facts', lambda *args: pytest.fail('closed handoff must never rebuild live facts'))
    after = preview(admin, 'closed')
    assert before['source_digest'] == after['source_digest']
    _, manifest, snapshot, wb = package(download(admin, after))
    assert manifest['submitted_by'] != manifest['reviewed_by']
    assert snapshot['facts']['metrics']['actual_opex_paid'] == 0
    assert wb['管理口径']['B3'].value == '33.34'
    assert wb['管理口径']['B5'].value == '0.00'
    assert snapshot['events'][-1]['action'] == 'close'


def test_closed_export_requires_real_closing_proof_and_reopen_invalidates_it(admin):
    assert admin.get(PREFIX+'/preview', params={**SCOPE, 'month': MONTH, 'mode': 'closed'}).status_code == 409
    close_expense_month(admin)
    old = preview(admin, 'closed')
    with SessionLocal() as db:
        bid = db.query(Building.id).filter_by(code='DEMO_A').scalar()
        store.period_action(db, bid, User(username='reviewer', role='admin'), MONTH,
                           PeriodPayload(action='reopen', expected_revision=3, reason='补录后重新复核'))
    download(admin, old, 409)
    assert preview(admin)['status'] == 'open'


def test_incomplete_closed_snapshot_fails_closed(admin):
    with SessionLocal() as db:
        bid = db.query(Building.id).filter_by(code='DEMO_A').scalar()
        db.add(FinancePeriod(building_id=bid, period_month=MONTH, status='closed'))
        db.commit()
    result = admin.get(PREFIX+'/preview', params={**SCOPE, 'month': MONTH, 'mode': 'closed'})
    assert result.status_code == 409


def test_permissions_authentication_and_query_validation(admin):
    summary = preview(admin)
    created = admin.post('/api/auth/create-user', json={'username': 'viewer', 'password': 'Viewer123', 'role': 'viewer'})
    assert created.status_code == 200
    with TestClient(app) as viewer:
        _login(viewer, 'viewer', 'Viewer123')
        assert preview(viewer)['month'] == MONTH
        download(viewer, summary, 404)
    with TestClient(app) as anonymous:
        assert anonymous.get(PREFIX+'/preview', params={**SCOPE, 'month': MONTH}).status_code == 401
    assert admin.get(PREFIX+'/preview', params={'building': 'DEMO_B', 'month': MONTH}).status_code == 404
    assert admin.get(PREFIX+'/preview', params={**SCOPE, 'month': MONTH, 'mode': 'anything'}).status_code == 422
    assert admin.get(PREFIX+'/export', params={**SCOPE, 'month': MONTH}).status_code == 422


def test_read_snapshot_does_not_mix_concurrent_new_cash(admin, monkeypatch):
    real = store.period_facts
    def concurrent(db, bid, month):
        with SessionLocal() as other:
            other.add(FinanceCash(building_id=bid, entry_key=str(uuid4()), request_hash='f'*64,
                                 direction='in', occurred_on=date(2026, 1, 10), amount=50,
                                 counterparty='并发来源', created_by='admin'))
            other.commit()
        return real(db, bid, month)
    monkeypatch.setattr(store, 'period_facts', concurrent)
    assert preview(admin)['cash_in_fen'] == 0
    monkeypatch.setattr(store, 'period_facts', real)
    assert preview(admin)['cash_in_fen'] == 5000


def test_offline_verifier_accepts_package_and_rejects_changed_payload_and_unknown_path(admin):
    content = download(admin, preview(admin)).content
    assert verify_package(content)['status'] == 'PASS'
    original = zipfile.ZipFile(io.BytesIO(content))
    changed = io.BytesIO()
    with zipfile.ZipFile(changed, 'w') as altered:
        for name in original.namelist():
            altered.writestr(name, original.read(name) + (b' changed' if name == 'README.txt' else b''))
    with pytest.raises(ValueError, match='文件校验失败'):
        verify_package(changed.getvalue())
    extra = io.BytesIO()
    with zipfile.ZipFile(extra, 'w') as altered:
        for name in original.namelist():
            altered.writestr(name, original.read(name))
        altered.writestr('../outside.txt', 'not allowed')
    with pytest.raises(ValueError, match='未知路径'):
        verify_package(extra.getvalue())


def test_finance_connections_sync_each_commit_and_enforce_foreign_keys():
    with engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA synchronous').scalar() == 2
        assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
