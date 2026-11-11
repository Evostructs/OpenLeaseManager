from datetime import date

from app.database import SessionLocal
from app.models import ReceivableStatement, RentTerm
from app.services import financial_planning as finance
from app.services import finance_operations as operations
from tests.test_financial_planning import _contract, _payment


def test_optimized_handoff_facts_and_digest_match_full_billing_path(monkeypatch):
    with SessionLocal() as db:
        first = _contract(db, 'PERF-EQUIV-1', free_period=1,
                          start_date=date(2026,1,25), end_date=date(2027,8,19))
        db.add(RentTerm(contract_id=first.id, rule_type='fixed_monthly',
                       monthly_amount=3333.33, start_date=first.start_date, end_date=first.end_date))
        second = _contract(db, 'PERF-EQUIV-2', property_fee_rate=2.5)
        _payment(db, first, '2026-02', date(2026,2,15), 321.01)
        _payment(db, second, '2026-02', date(2026,2,18), 123.45, '减免')
        db.add(ReceivableStatement(contract_id=second.id, period_month='2026-02',
                                  rent=987.65, property_fee=210.05, total_amount=1197.70))
        db.flush()
        actual = operations.period_facts(db, first.building_id, '2026-02')
        batch, plan, public_rows = finance.get_monthly_receivables_batch, finance.build_rent_plan, finance._public_rows

        def full_batch(*args, **kwargs):
            kwargs['billing_only'] = False
            return batch(*args, **kwargs)

        def full_plan(*args, **kwargs):
            kwargs['include_term_effects'] = True
            kwargs['financial_only'] = False
            return plan(*args, **kwargs)

        with monkeypatch.context() as context:
            context.setattr(finance, 'get_monthly_receivables_batch', full_batch)
            context.setattr(finance, 'build_rent_plan', full_plan)
            context.setattr(finance, '_public_rows', lambda *args, **kwargs: list(public_rows(*args, **kwargs)))
            reference = operations.period_facts(db, first.building_id, '2026-02')
        assert actual == reference
        assert operations.digest(actual) == operations.digest(reference)
