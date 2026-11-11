from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Contract, OperationLog, ReceivablePayment, ReceivablePaymentOperation, ReceivableRecord
from app.schemas import ReceivablePaymentPayload
from app.services import contract_receivables as service
from tests.test_contract_financial_scope_guards import _login_admin, _contract_payload, _seed_other_building_financials


@pytest.fixture
def payment_case():
    client = TestClient(app)
    _login_admin(client)
    created = client.post('/api/contracts', json=_contract_payload('R1A-001', 'R1A-01'))
    assert created.status_code == 200, created.text
    contract_id = created.json()['id']
    body = dict(operation_id=str(uuid4()), month='2026-09', amount=100,
                payment_date='2026-09-10', allocation_type='收款', payment_method='转账',
                reference_no='R1A-TEST', notes='synthetic',
                metadata=dict(month='2026-09', invoice_status='待开票', dunning_status='已提醒', notes='saved together'))
    return client, contract_id, f'/api/contracts/receivables/monthly/{contract_id}', body


def counts():
    with SessionLocal() as db:
        return tuple(db.query(model).count() for model in (ReceivablePayment, ReceivablePaymentOperation, ReceivableRecord))


def test_replay_has_one_payment_one_audit_and_identical_receipt(payment_case):
    client, contract_id, url, body = payment_case
    first = client.post(url + '/payment-operations', json=body)
    second = client.post(url + '/payment-operations', json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == dict(operation_id=body['operation_id'], contract_id=contract_id,
                                payment_id=second.json()['payment_id'], status='recorded', replayed=False)
    assert second.json()['replayed'] is True
    assert counts() == (1, 1, 1)
    with SessionLocal() as db:
        assert db.query(OperationLog).filter_by(action='receivable_payment').count() == 1
        assert db.query(OperationLog).filter_by(action='receivable_update').count() == 1
        assert db.query(ReceivableRecord).one().notes == 'saved together'
    assert client.get(url + '/payment-operations/' + body['operation_id']).json() == second.json()


@pytest.mark.parametrize('change', [dict(amount=101), dict(payment_date='2026-09-11'),
                                    dict(metadata=dict(month='2026-09', notes='different'))])
def test_same_key_different_content_conflicts(payment_case, change):
    client, _, url, body = payment_case
    assert client.post(url + '/payment-operations', json=body).status_code == 200
    assert client.post(url + '/payment-operations', json={**body, **change}).status_code == 409
    assert counts() == (1, 1, 1)


def test_equal_amount_and_date_are_legitimate_separate_payments(payment_case):
    client, _, url, body = payment_case
    assert client.post(url + '/payment-operations', json=body).status_code == 200
    assert client.post(url + '/payment-operations', json={**body, 'operation_id': str(uuid4())}).status_code == 200
    assert counts() == (2, 2, 1)


def test_deleted_payment_keeps_receipt_and_cannot_be_resurrected(payment_case):
    client, _, url, body = payment_case
    payment_id = client.post(url + '/payment-operations', json=body).json()['payment_id']
    assert client.delete(f'/api/contracts/receivables/payments/{payment_id}').status_code == 200
    replay = client.post(url + '/payment-operations', json=body)
    assert replay.status_code == 200
    assert replay.json()['status'] == 'deleted'
    assert replay.json()['payment_id'] is None
    assert counts() == (0, 1, 1)


def test_metadata_payment_receipt_and_audit_roll_back_together(payment_case, monkeypatch):
    _, contract_id, _, body = payment_case
    original_log = service.log_action
    def fail_payment_audit(*args, **kwargs):
        if args[2] == 'receivable_payment':
            raise RuntimeError('synthetic audit failure')
        return original_log(*args, **kwargs)
    monkeypatch.setattr(service, 'log_action', fail_payment_audit)
    with SessionLocal() as db:
        with pytest.raises(RuntimeError, match='synthetic'):
            service.add_receivable_payment(db, db.get(Contract, contract_id), ReceivablePaymentPayload(**body), 'admin')
    assert counts() == (0, 0, 0)
    with SessionLocal() as db:
        assert db.query(OperationLog).filter(OperationLog.action.in_(['receivable_update', 'receivable_payment'])).count() == 0


def test_concurrent_same_key_is_reserved_before_other_writes(payment_case, monkeypatch):
    _, contract_id, _, body = payment_case
    # Synchronize both reads to force both callers to observe an absent operation.
    barrier = Barrier(2)
    original_get = SessionLocal.class_.get
    def synchronized_get(db, model, identity, *args, **kwargs):
        result = original_get(db, model, identity, *args, **kwargs)
        if model is ReceivablePaymentOperation and result is None:
            barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(SessionLocal.class_, 'get', synchronized_get)
    def submit(_):
        with SessionLocal() as db:
            return service.add_receivable_payment(db, db.get(Contract, contract_id), ReceivablePaymentPayload(**body), 'admin')
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(submit, range(2)))
    assert sorted(row['replayed'] for row in receipts) == [False, True]
    assert receipts[0]['payment_id'] == receipts[1]['payment_id']
    assert counts() == (1, 1, 1)


def test_receipt_does_not_depend_on_monthly_report_or_later_lease_dates(payment_case, monkeypatch):
    client, contract_id, url, body = payment_case
    def fail_report(*args, **kwargs):
        raise RuntimeError('synthetic report failure')
    monkeypatch.setattr(service, 'get_monthly_receivables', fail_report)
    first = client.post(url + '/payment-operations', json=body)
    assert first.status_code == 200
    with SessionLocal() as db:
        db.get(Contract, contract_id).end_date = date(2026, 8, 31)
        db.commit()
    replay = client.post(url + '/payment-operations', json=body)
    assert replay.status_code == 200
    assert replay.json()['payment_id'] == first.json()['payment_id']


def test_receipts_and_retries_obey_current_building_permissions(payment_case):
    client, _, url, body = payment_case
    assert client.post(url + '/payment-operations', json=body).status_code == 200
    _seed_other_building_financials()
    suffix = '/payment-operations/' + body['operation_id']
    assert client.get(url + suffix, params={'building': 'FINANCE_B'}).status_code in (403, 404)
    assert client.post(url + '/payment-operations', params={'building': 'FINANCE_B'}, json=body).status_code in (403, 404)
    assert client.get(url + '/payment-operations/' + str(uuid4())).status_code == 404
    client.post('/api/auth/logout')
    assert client.get(url + suffix).status_code == 401


def test_legacy_clients_retain_the_monthly_response(payment_case):
    client, _, url, body = payment_case
    body.pop('operation_id')
    body.pop('metadata')
    response = client.post(url + '/payments', json=body)
    assert response.status_code == 200
    assert response.json()['month'] == '2026-09'
    assert response.json()['rows'][0]['received_amount'] == 100
    assert counts() == (1, 0, 1)


def test_mismatched_month_and_invalid_operation_id_do_not_write(payment_case):
    client, _, url, body = payment_case
    assert client.post(url + '/payment-operations', json={**body, 'operation_id': 'invalid'}).status_code == 422
    body['metadata']['month'] = '2026-10'
    assert client.post(url + '/payment-operations', json=body).status_code == 400
    assert counts() == (0, 0, 0)
