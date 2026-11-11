from datetime import date
import pytest
from app.database import SessionLocal
from app.models import Contract, FinancialPlanVersion, FinancialPlanningState
from app.finance_models import FinanceCash, FinanceAllocation, FinanceBankLine
from app.scripts.seed_october_demo import run, TAG
from app.scripts.seed_finance_demo import run as seed_finance
from app.services import finance_operations as finance
from tests.test_financial_planning_api import admin, _seed_contract


def test_october_extension_is_idempotent_preserves_history_and_reconciles(admin):
    with SessionLocal() as db:
        seed_finance(db, True)
        originals = {c.id: (c.contract_no, c.notes, c.start_date, c.end_date) for c in db.query(Contract)}
        old_versions = {v.id: v.snapshot_json for v in db.query(FinancialPlanVersion)}
        state = db.query(FinancialPlanningState).one()
        active = (state.active_budget_id, state.active_forecast_id)
        dry = run(db)
        assert dry['before'] == __import__('app.scripts.seed_october_demo', fromlist=['counts']).counts(db)
        first = run(db, True)
        assert first['changed']['finance_cash'] > 0
        again = run(db, True)
        assert all(v == 0 for v in again['changed'].values())
        assert originals == {c.id: (c.contract_no, c.notes, c.start_date, c.end_date) for c in db.query(Contract)}
        assert all(db.get(FinancialPlanVersion, vid).snapshot_json == snapshot for vid, snapshot in old_versions.items())
        db.refresh(state)
        assert active == (state.active_budget_id, state.active_forecast_id)
        new_cash = db.query(FinanceCash).filter(FinanceCash.entry_key.like(TAG+'-%')).all()
        assert max(c.occurred_on for c in new_cash) <= date(2026, 10, 31)
        assert all('[模拟]' in c.note for c in new_cash)
        advance = next(c for c in new_cash if c.entry_key == TAG+'-advance')
        assert finance.allocated(db, advance.id) == 0
        assert finance.matched(db, cash_id=advance.id) == 800000
        unmatched = db.query(FinanceBankLine).filter_by(import_key=TAG+'-unmatched').one()
        assert finance.matched(db, bank_id=unmatched.id) == 0
        assert not any(a.voided for a in db.query(FinanceAllocation).filter(FinanceAllocation.entry_key.like(TAG+'-%')))


def test_october_seed_rejects_unlabelled_contract_before_writing(admin):
    _seed_contract(2026)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match='模拟'):
            run(db, True)
        assert db.query(FinanceCash).count() == 0


def test_simulated_snapshot_date_is_explicit_and_bounded():
    from app.scripts.seed_portfolio_operating_demo import _validate_request_date
    _validate_request_date(date(2026, 10, 31), simulation_today=date(2026, 10, 31))
    with pytest.raises(ValueError):
        _validate_request_date(date(2026, 11, 1), simulation_today=date(2026, 10, 31))


def test_snapshot_trajectories_keep_ratios_consistent_and_show_different_cases():
    from app.scripts.seed_portfolio_operating_demo import _PROFILES
    from app.scripts.seed_october_demo import portfolio_profiles
    end = portfolio_profiles(_PROFILES, date(2026, 10, 31))
    start = portfolio_profiles(_PROFILES, date(2026, 10, 1))
    for code, p in end.items():
        assert 0 <= p.received_month <= p.receivable_month
        assert 0 <= p.leased_sqm <= p.rentable_sqm
        assert start[code].received_month < p.received_month
    assert end['DEMO_B'].arrears_balance < _PROFILES['DEMO_B'].arrears_balance
    assert end['DEMO_E'].arrears_balance > _PROFILES['DEMO_E'].arrears_balance


def test_october_future_demo_snapshot_has_full_evidence_and_safe_replay(monkeypatch):
    from app.scripts import seed_portfolio_operating_demo as module
    from app.scripts.seed_october_demo import portfolio_profiles
    from tests.test_portfolio_operating_demo import _actors
    from app.models import BuildingOperatingSnapshot, BuildingMetricFact
    day = date(2026, 10, 31)
    monkeypatch.setattr(module, '_PROFILES', portfolio_profiles(module._PROFILES, day))
    with SessionLocal() as db:
        submitter, reviewer, publisher = _actors(db)
        kwargs = dict(as_of_date=day, simulation_today=day, submitter_id=submitter.id,
                      reviewer_id=reviewer.id, publisher_id=publisher.id, open_gates=False)
        first = module.seed_portfolio_operating_demo(db, **kwargs)
        assert all(r['status'] == 'created' for r in first['buildings'])
        assert db.query(BuildingMetricFact).count() == 60
        second = module.seed_portfolio_operating_demo(db, **kwargs)
        assert all(r['status'] == 'skipped' for r in second['buildings'])
        assert db.query(BuildingOperatingSnapshot).count() == 4
