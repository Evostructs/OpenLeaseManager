from datetime import date, timedelta
from random import Random

import pytest

from app.models import Contract, ContractRoom, RentTerm
from app.services import contract_rent as rent
from app.services import rent_analytics as analytics
from app.services.building_operations_profile import DEMO_A_PROFILE


def daily_periods(_contract, start, end):
    return [(start + timedelta(days=i), start + timedelta(days=i))
            for i in range((end - start).days + 1)]


def example(seed):
    rng = Random(seed)
    contract = Contract(id=seed + 1, building_id=1, tower='南塔', room_no='101',
        start_date=date(2020, 2, 29), end_date=date(2026, 11, 19),
        area=150, unit_price=3, monthly_rent=10000.05, free_period=1)
    contract.room_items = [
        ContractRoom(id=1, building_id=1, room_id=1, room_no='101', tower='南塔',
            area=100, unit_price=3, monthly_rent=8000.05,
            start_date=contract.start_date, end_date=date(2024, 8, 17)),
        ContractRoom(id=2, building_id=1, room_id=2, room_no='102', tower='北塔',
            area=50, unit_price=2, monthly_rent=3000.17,
            start_date=date(2024, 3, 9), end_date=contract.end_date),
    ]
    contract.rent_terms = [
        RentTerm(id=1, rule_type='fixed_monthly', monthly_amount=rng.choice([0, 10000.05, 3333.33]),
            start_date=date(2020, 2, 29), end_date=date(2024, 7, 13)),
        RentTerm(id=2, rule_type='area_unit_price', unit_price=2.13, room_no='101',
            start_date=date(2024, 7, 14), end_date=date(2025, 9, 21)),
        RentTerm(id=3, rule_type='escalation', adjustment_type=rng.choice(['percent', 'fixed_amount']),
            adjustment_value=rng.choice([7.5, 123.45]), frequency='annual',
            start_date=date(2020, 2, 29), end_date=date(2026, 10, 3)),
        RentTerm(id=4, rule_type='rent_free', contract_room_id=2,
            start_date=date(2024, 4, 15), end_date=date(2024, 5, 19)),
        RentTerm(id=5, rule_type='temporary_adjustment', room_no='101',
            adjustment_type=rng.choice(['percent', 'fixed_amount']), adjustment_value=123.45,
            start_date=date(2024, 6, 7), end_date=date(2024, 7, 22)),
        RentTerm(id=6, rule_type='stepped_rent', monthly_amount=4321.03, contract_room_id=2,
            start_date=date(2024, 5, 20), end_date=date(2026, 9, 8)),
    ]
    return contract


@pytest.mark.parametrize('seed', range(8))
def test_spans_are_identical_to_every_original_day_and_month(seed, monkeypatch):
    contract = example(seed)
    start, end = date(2023, 12, 17), date(2025, 3, 11)
    spans = rent.rent_constant_periods(contract, start, end)
    assert spans[0][0] == start and spans[-1][1] == end
    cursor = start
    for left, right in spans:
        assert left == cursor
        reference = rent.rent_day_breakdown(contract, left)
        for _, day in daily_periods(contract, left, right):
            assert rent.rent_day_breakdown(contract, day) == reference
        cursor = right + timedelta(days=1)
    for year, month in [(2020,2), (2020,3), (2023,2), (2024,2), (2024,3),
                         (2024,4), (2024,5), (2024,6), (2024,7), (2024,8),
                         (2025,2), (2026,11), (2026,12)]:
        anchor = date(year, month, 1)
        actual = rent._rent_terms_month_detail(contract, anchor)
        with monkeypatch.context() as context:
            context.setattr(rent, 'rent_constant_periods', daily_periods)
            assert rent._rent_terms_month_detail(contract, anchor) == actual


def test_analytics_matches_daily_reference_with_rooms_and_complex_terms(monkeypatch):
    contracts = [example(seed) for seed in range(8)]
    start, end = date(2024, 1, 1), date(2024, 12, 31)
    grouping = analytics._rent_grouping(DEMO_A_PROFILE)
    actual = analytics._calculate_daily(contracts, start, end, {}, {}, {}, grouping)
    monkeypatch.setattr(analytics, 'rent_constant_periods', daily_periods)
    reference = analytics._calculate_daily(contracts, start, end, {}, {}, {}, grouping)
    for day, segments in actual.items():
        for segment, values in segments.items():
            expected = reference[day][segment]
            assert values['area'] == expected['area']
            assert values['contract_count'] == expected['contract_count']
            assert values['rent'] == pytest.approx(expected['rent'], abs=1e-7)
            assert rent.round_money(values['rent']) == rent.round_money(expected['rent'])


def test_fixed_monthly_rule_is_evaluated_once_per_month(monkeypatch):
    contract = example(1)
    contract.free_period = 0
    contract.room_items = []
    contract.rent_terms = [RentTerm(rule_type='fixed_monthly', monthly_amount=10000,
        start_date=contract.start_date, end_date=contract.end_date)]
    original = rent._rent_terms_day_breakdown
    days = []

    def counted(contract, target):
        days.append(target)
        return original(contract, target)

    monkeypatch.setattr(rent, '_rent_terms_day_breakdown', counted)
    for month in range(1, 13):
        assert rent._rent_terms_month_detail(contract, date(2024, month, 1))['rent'] == 10000
    assert len(days) <= 12


@pytest.mark.parametrize('base', [0, .01, .05, 3333.33, 10000.05, 99999999.99])
@pytest.mark.parametrize('free', [0, 1, 3])
def test_simple_month_matches_unoptimized_daily_engine(base, free, monkeypatch):
    contract = example(2)
    contract.start_date = date(2024, 1, 25)
    contract.end_date = date(2024, 5, 19)
    contract.free_period = free
    contract.rent_terms = [RentTerm(rule_type='fixed_monthly', monthly_amount=base,
        start_date=contract.start_date, end_date=contract.end_date)]
    for month in range(1, 7):
        anchor = date(2024, month, 1)
        actual = rent._rent_terms_month_detail(contract, anchor)
        with monkeypatch.context() as context:
            context.setattr(rent, '_simple_fixed_month_detail', lambda *_: None)
            context.setattr(rent, 'rent_constant_periods', daily_periods)
            assert rent._rent_terms_month_detail(contract, anchor) == actual


def test_financial_plan_omits_only_unused_display_fields():
    from app.services.contract_receivables import build_rent_plan
    contract = example(3)
    full = build_rent_plan(contract)['plan']
    compact = build_rent_plan(contract, include_term_effects=False)['plan']
    assert len(full) == len(compact)
    for original, reduced in zip(full, compact):
        assert {k: v for k, v in original.items() if k not in ('term_effects', 'term_summary')} == {
            k: v for k, v in reduced.items() if k not in ('term_effects', 'term_summary')}


def test_exact_generated_totals_match_saved_snapshot_parsing():
    import json
    from app.services import financial_planning as finance
    rng = Random(1729)
    months = ['2026-01', '2026-02']
    sources = [{'months': finance._public_rows({month: {
        key: rng.randrange(0, 10**14) for key in finance.BASE_METRICS}
        for month in months}, 1)} for _ in range(20)]
    assert finance._aggregate_sources(sources, months, 1) == finance._aggregate_sources(
        json.loads(json.dumps(sources)), months, 1)
    normalized = finance._json_safe(sources)
    assert type(normalized[0]['months']) is list
