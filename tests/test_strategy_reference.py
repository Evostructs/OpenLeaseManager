from copy import deepcopy
from app.database import SessionLocal
from app.models import Building, FinancialPlanVersion
from test_financial_planning_api import admin, year, _save, _version_payload, _seed_contract, _expense


def state_for(version,**changes):
    base=version['snapshot']['totals']['net_cash_flow']
    reference={'version_id':version['id'],'hold_adjustment_wan':1,'renovate_adjustment_wan':2,'reason':'补入单独核算的资产经营现金','version_name':'client supplied text','net_cash_flow':999999,**changes}
    return {'strategy':{'reference':reference,'hold':{'annualNetCashFlow':base+1},'renovate':{'annualNetCashFlow':base+2}}}

def test_reference_is_resolved_from_immutable_same_building_forecast(admin,year):
    _seed_contract(year)
    source=_save(admin,year)['version'];state=state_for(source)
    saved=_save(admin,year,kind='scenario',client_state=state)['version']
    reference=saved['client_state']['strategy']['reference']
    assert reference['net_cash_flow']==source['snapshot']['totals']['net_cash_flow']
    assert reference['version_name']==source['name'] and reference['source_digest']==source['source_digest']
    assert reference['property_fee_cash_included'] is False
    _expense(admin,year,amount_yuan=50000)
    latest=_save(admin,year)['version']
    assert latest['snapshot']['totals']['net_cash_flow']!=source['snapshot']['totals']['net_cash_flow']
    restored=admin.get(f'/api/financial-planning/versions/{saved["id"]}',params={'building':'DEMO_A'}).json()
    assert restored['client_state']['strategy']['reference']==reference

def test_reference_rejects_unexplained_or_inconsistent_cash_inputs(admin,year):
    source=_save(admin,year)['version']
    for state in (state_for(source,reason=''),state_for(source)):
        if state['strategy']['reference']['reason']:state['strategy']['hold']['annualNetCashFlow']=999
        payload=_version_payload(admin,year,kind='scenario',client_state=state)
        r=admin.post('/api/financial-planning/versions',params={'building':'DEMO_A'},json=payload)
        assert r.status_code==422,r.text

def test_budget_or_foreign_reference_is_rejected(admin,year):
    budget=_save(admin,year,kind='budget')['version']
    r=admin.post('/api/financial-planning/versions',params={'building':'DEMO_A'},json=_version_payload(admin,year,kind='scenario',client_state=state_for(budget)))
    assert r.status_code==422
    source=_save(admin,year)['version']
    foreign_payload=_version_payload(admin,year,kind='scenario',client_state=state_for(source))
    with SessionLocal() as db:
        db.get(FinancialPlanVersion,source['id']).building_id=db.query(Building.id).filter_by(code='DEMO_B').scalar();db.commit()
    r=admin.post('/api/financial-planning/versions',params={'building':'DEMO_A'},json=foreign_payload)
    assert r.status_code==404
