from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.database import SessionLocal
from app.finance_models import FinanceAllocation, FinanceCash, FinanceExpenseDetail
from app.models import FinancialExpense, ReceivableStatement, RentTerm
from app.services import contract_rent as rent
from app.services import financial_planning as finance
from app.services import financial_planning_store as planning_store
from app.services import finance_operations as operations
from app.services.contract_receivables import build_rent_plan
from tests.test_financial_planning import _contract, _payment
from tests.test_rent_constant_periods import example


@pytest.mark.parametrize('month', [1, 2, 6, 12])
def test_period_facts_and_digest_equal_full_year_with_complete_evidence(month, monkeypatch):
    with SessionLocal() as db:
        contract = _contract(db, 'PERIOD', start_date=date(2025, 1, 25),
                             end_date=date(2027, 8, 19), free_period=1, property_fee_rate=2.5)
        db.add_all([
            RentTerm(contract_id=contract.id, rule_type='fixed_monthly', monthly_amount=3333.33,
                     start_date=contract.start_date, end_date=date(2026, 5, 13)),
            RentTerm(contract_id=contract.id, rule_type='stepped_rent', monthly_amount=4444.44,
                     start_date=date(2026, 5, 14), end_date=contract.end_date),
            RentTerm(contract_id=contract.id, rule_type='temporary_adjustment',
                     adjustment_type='fixed_amount', adjustment_value=123.45,
                     start_date=date(2026, 6, 7), end_date=date(2026, 7, 22)),
            ReceivableStatement(contract_id=contract.id, period_month='2026-02',
                                rent=987.65, property_fee=210.05, total_amount=1197.70),
        ])
        # Payments in several months must remain in source evidence, including
        # fee receipts outside the projected month but before its cutoff.
        for period in (1, 2, 6, 12):
            key = f'2026-{period:02d}'
            _payment(db, contract, key, date(2026, period, 15), 321.01)
            row = FinanceCash(building_id=contract.building_id, entry_key=str(uuid4()),
                              request_hash='a'*64, direction='in', occurred_on=date(2026, period, 16),
                              amount=100.01, counterparty='Synthetic', created_by='test')
            db.add(row); db.flush()
            db.add(FinanceAllocation(building_id=contract.building_id, cash_id=row.id,
                                    entry_key=str(uuid4()), request_hash='b'*64, kind='property_fee',
                                    amount=100.01, period_month=key, contract_id=contract.id, created_by='test'))
        _payment(db, contract, '2026-02', None, 22.01)
        _payment(db, contract, '2026-01', date(2026, 6, 20), 31.05, '减免')
        old = _contract(db, 'OLD', start_date=date(2024, 1, 1), end_date=date(2025, 12, 31))
        _payment(db, old, '2025-12', date(2026, 2, 18), 456.78)
        _contract(db, 'INVALID', start_date=None, end_date=None)
        expense = FinancialExpense(building_id=contract.building_id, name='Service',
                                   category='opex', amount_yuan=100.01, occurrence_month='2026-01',
                                   payment_month='2026-01', created_by='test', updated_by='test')
        db.add(expense); db.flush()
        db.add(FinanceExpenseDetail(expense_id=expense.id, service_from='2025-11',
                                   service_to='2026-06', supplier='Synthetic', evidence='TEST'))
        db.flush()
        period = f'2026-{month:02d}'
        actual = operations.period_facts(db, contract.building_id, period)
        full_forecast, full_plan = planning_store.build_forecast, finance.build_rent_plan

        def annual(*args, **kwargs):
            kwargs.pop('_period_month', None)
            return full_forecast(*args, **kwargs)

        def display_plan(*args, **kwargs):
            kwargs['financial_only'] = False
            return full_plan(*args, **kwargs)

        with monkeypatch.context() as context:
            context.setattr(planning_store, 'build_forecast', annual)
            context.setattr(finance, 'build_rent_plan', display_plan)
            reference = operations.period_facts(db, contract.building_id, period)
        assert actual == reference
        assert operations.digest(actual) == operations.digest(reference)


@pytest.mark.parametrize('seed', range(4))
def test_financial_plan_preserves_complex_full_lease_snapshot_and_income(seed):
    contract = example(seed)
    full = build_rent_plan(contract)['plan']
    compact = build_rent_plan(contract, financial_only=True)['plan']
    assert finance._contract_snapshot(contract, compact, {}) == finance._contract_snapshot(contract, full, {})
    assert planning_store.canonical_json(finance._contract_snapshot(contract, compact, {})) == planning_store.canonical_json(finance._contract_snapshot(contract, full, {}))
    assert finance._straight_income(compact) == finance._straight_income(full)


@pytest.mark.parametrize('amount', [0, .01, 3333.33, 99999999.99])
@pytest.mark.parametrize('free', [0, 1, 3])
def test_fixed_lease_preparation_matches_original_daily_engine(amount, free, monkeypatch):
    from app.models import Contract

    contract = Contract(start_date=date(2024, 1, 25), end_date=date(2026, 5, 19),
                        free_period=free, monthly_rent=1234.56, room_no='101', area=100)
    contract.rent_terms = [RentTerm(rule_type='fixed_monthly', monthly_amount=amount,
                                  start_date=contract.start_date, end_date=contract.end_date)]
    prepared = list(rent.rent_plan_month_details(contract))
    monkeypatch.setattr(rent, '_simple_fixed_month_detail', lambda *args: None)
    for anchor, _, actual in prepared:
        assert actual == rent._month_rent_detail(contract, anchor)


def test_month_projection_rejects_mismatched_cutoff_and_scenario():
    with SessionLocal() as db:
        contract = _contract(db)
        for options in ({'_period_month': '2026-02'},
                        {'_period_month': '2026-01', 'frozen_snapshot': {}},
                        {'_period_month': '2026-01', 'contract_adjustments': [{'contract_id': contract.id, 'rent_change_percent': 1}]}):
            with pytest.raises(HTTPException) as error:
                finance.build_financial_forecast(db, building_id=contract.building_id,
                                                year=2026, closed_through_month=1, **options)
            assert error.value.status_code == 422
