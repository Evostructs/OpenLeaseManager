from datetime import date
from uuid import uuid4
import json
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.database import SessionLocal
from app.models import Building, Contract, ContractDeposit, FinancialExpense, ReceivablePayment, ReceivableRecord, User
from app.finance_models import FinanceCash, FinanceAllocation, FinancePeriod, FinancePeriodEvent, FinanceBankLine, FinanceBankMatch
from app.finance_schemas import PeriodPayload, AllocationPayload
from app.services import finance_operations as store
from tests.test_financial_planning_api import admin, _seed_contract, _source

PREFIX='/api/finance-operations'
SCOPE={'building':'DEMO_A'}
YEAR=2026
MONTH='2026-01'


def post(client,path,payload,code=200):
    from urllib.parse import parse_qsl
    route,_,query=path.partition("?")
    response=client.post(PREFIX+route,params={**SCOPE,**dict(parse_qsl(query))},json=payload)
    assert response.status_code==code,response.text
    return response.json()


def cash(client,amount=100,**values):
    return post(client,'/cash',{'entry_key':str(uuid4()),'amount':amount,'direction':'in','occurred_on':MONTH+'-10','counterparty':'模拟往来方',**values})


def allocate(client,row,cid,amount=100,**changes):
    return post(client,f'/cash/{row["id"]}/allocate',{'entry_key':str(uuid4()),'expected_revision':row['revision'],'kind':'rent','amount':amount,'period_month':MONTH,'contract_id':cid,**changes})


def test_rent_atomic_idempotency_and_reversal(admin):
    cid=_seed_contract(YEAR);c=cash(admin,500)
    payload={'entry_key':str(uuid4()),'expected_revision':1,'kind':'rent','amount':300,'period_month':MONTH,'contract_id':cid}
    a=post(admin,f'/cash/{c["id"]}/allocate',payload)
    again=post(admin,f'/cash/{c["id"]}/allocate',payload)
    assert a['id']==again['id']
    post(admin,f'/cash/{c["id"]}/allocate',{**payload,'amount':200},409)
    post(admin,f'/cash/{c["id"]}/allocate',{**payload,'entry_key':str(uuid4()),'expected_revision':2,'amount':201},422)
    with SessionLocal() as db:
        assert db.query(ReceivablePayment).filter_by(contract_id=cid).count()==1
        assert db.get(FinanceCash,c['id']).revision==2
    post(admin,f'/allocations/{a["id"]}/reverse',{'expected_revision':2,'reason':'错误分配更正'})
    assert _source(admin,YEAR,1)['forecast']['months'][0]['actual_cash_received']==0
    with SessionLocal() as db:
        assert sorted(p.amount for p in db.query(ReceivablePayment).filter_by(contract_id=cid))==[-300,300]
        with pytest.raises(IntegrityError):
            db.query(ReceivablePayment).filter_by(id=a['payment_id']).delete();db.flush()
        db.rollback()


def test_legacy_summary_not_lost_and_existing_receipt_adoption(admin):
    cid=_seed_contract(YEAR)
    with SessionLocal() as db:
        db.add(ReceivableRecord(contract_id=cid,period_month=MONTH,received_amount=200,received_date=date(YEAR,1,10)));db.commit()
    a=allocate(admin,cash(admin,100),cid)
    assert _source(admin,YEAR,1)['forecast']['months'][0]['actual_cash_received']==.03
    with SessionLocal() as db: legacy=db.query(ReceivablePayment).filter_by(contract_id=cid,source='legacy_summary').one().id
    adopted=allocate(admin,cash(admin,200),cid,200,existing_payment_id=legacy)
    assert _source(admin,YEAR,1)['forecast']['months'][0]['actual_cash_received']==.03
    post(admin,f'/allocations/{adopted["id"]}/reverse',{'expected_revision':2,'reason':'仅撤销历史关联'})
    assert _source(admin,YEAR,1)['forecast']['months'][0]['actual_cash_received']==.03


def expense(client,amount=100):
    return post(client,'/expenses',{'entry_key':str(uuid4()),'expense':{'name':'模拟季度维护','category':'opex','amount_yuan':amount,'occurrence_month':MONTH,'payment_month':'2026-02','owner':'模拟经办'},'detail':{'expected_revision':1,'supplier':'模拟供应商','cost_category':'维护','service_from':MONTH,'service_to':'2026-03','evidence':'模拟单据 TEST-001'}})


def test_expense_service_period_partial_payment_forecast_and_overpay(admin):
    e=expense(admin,100)
    c=cash(admin,40,direction='out')
    allocate(admin,c,None,40,kind='expense',expense_id=e['id'])
    forecast=_source(admin,YEAR,1)['forecast']
    assert [m['opex'] for m in forecast['months'][:3]]==[.003334,.003333,.003333]
    assert forecast['months'][0]['actual_opex_paid']==.004
    assert forecast['months'][1]['forecast_opex_paid']==.006
    c2=cash(admin,61,direction='out')
    post(admin,f'/cash/{c2["id"]}/allocate',{'entry_key':str(uuid4()),'expected_revision':1,'kind':'expense','amount':61,'period_month':MONTH,'expense_id':e['id']},422)
    with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            db.query(FinancialExpense).filter_by(id=e['id']).update({'amount_yuan':39});db.commit()
        db.rollback()


def test_bank_matches_never_post_cash_and_overmatch_rejected(admin):
    c=cash(admin,200)
    payload={'rows':[{'import_key':'test-import-1','occurred_on':MONTH+'-10','direction':'in','amount':200,'counterparty':'模拟往来方'}]}
    b=post(admin,'/bank/import',payload)['items'][0]
    assert post(admin,'/bank/import',payload)['items'][0]['id']==b['id']
    match={'entry_key':str(uuid4()),'expected_revision':1,'cash_revision':1,'cash_id':c['id'],'amount':120}
    m=post(admin,f'/bank/{b["id"]}/match',match)
    assert post(admin,f'/bank/{b["id"]}/match',match)['id']==m['id']
    post(admin,f'/bank/{b["id"]}/match',{**match,'entry_key':str(uuid4()),'expected_revision':2,'cash_revision':2,'amount':81},422)
    with SessionLocal() as db: assert db.query(ReceivablePayment).count()==0
    post(admin,f'/matches/{m["id"]}/reverse',{'expected_revision':2,'reason':'银行对应错误'})
    response=admin.get(PREFIX+'/ledger',params={**SCOPE,'month':MONTH}).json()
    assert response['cash'][0]['bank_remaining']==200
    assert response['banks'][0]['remaining']==200


def test_bank_import_atomic_on_conflicting_duplicate(admin):
    row={'import_key':'same','occurred_on':MONTH+'-10','direction':'in','amount':200,'counterparty':'模拟往来方'}
    post(admin,'/bank/import',{'rows':[row,{**row,'amount':201}]},409)
    with SessionLocal() as db: assert db.query(FinanceBankLine).count()==0


def test_cross_building_cannot_allocate(admin):
    cid=_seed_contract(YEAR,code='DEMO_B');c=cash(admin)
    post(admin,f'/cash/{c["id"]}/allocate',{'entry_key':str(uuid4()),'expected_revision':1,'kind':'rent','amount':100,'period_month':MONTH,'contract_id':cid},404)
    with SessionLocal() as db: assert db.get(FinanceCash,c['id']).revision==1


def test_deposit_refund_and_reversal_do_not_count_as_income(admin):
    cid=_seed_contract(YEAR)
    with SessionLocal() as db:
        d=ContractDeposit(contract_id=cid,expected_amount=100);db.add(d);db.commit();did=d.id
    a=allocate(admin,cash(admin),cid,kind='deposit',deposit_id=did)
    assert _source(admin,YEAR,1)['forecast']['totals']['actual_cash_received']==0
    refund=allocate(admin,cash(admin,50,direction='out'),cid,50,kind='deposit',deposit_id=did)
    with SessionLocal() as db:
        d=db.get(ContractDeposit,did);assert (d.received_amount,d.refunded_amount)==(100,50)
        from app.services.deposits import _guard_finance_link
        with pytest.raises(HTTPException): _guard_finance_link(db,d)
    post(admin,f'/allocations/{a["id"]}/reverse',{'expected_revision':2,'reason':'不允许先撤收款'},400)
    post(admin,f'/allocations/{refund["id"]}/reverse',{'expected_revision':2,'reason':'撤销押金退款'})
    post(admin,f'/allocations/{a["id"]}/reverse',{'expected_revision':2,'reason':'撤销押金收款'})
    with SessionLocal() as db:
        d=db.get(ContractDeposit,did);assert (d.received_amount,d.refunded_amount)==(0,0)


def period_post(client,action,revision=1,confirm=None,code=200):
    return post(client,'/period?month='+MONTH,{'action':action,'expected_revision':revision,'reason':'月度核对测试','confirmations':confirm or {k:'zero' for k in ['income','receipts','expenses','payments']}},code)


def test_empty_month_requires_explicit_zero_and_independent_review(admin):
    period_post(admin,'submit',confirm={'income':'missing'},code=422)
    period_post(admin,'submit')
    period_post(admin,'close',2,code=422)
    with SessionLocal() as db:
        bid=db.query(Building.id).filter_by(code='DEMO_A').scalar()
        reviewer=User(username='independent-reviewer',role='admin')
        store.period_action(db,bid,reviewer,MONTH,PeriodPayload(action='close',expected_revision=2,reason='独立复核已完成'))
        assert db.query(FinancePeriod).one().status=='closed'
        with pytest.raises(IntegrityError):
            db.add(FinanceCash(building_id=bid,entry_key='locked-cash',request_hash='x'*64,direction='in',occurred_on=date(YEAR,1,1),amount=10,counterparty='test',created_by='test'));db.flush()
        db.rollback()
        with pytest.raises(IntegrityError): db.query(FinancePeriodEvent).update({'reason':'篡改'});db.flush()
        db.rollback()
        store.period_action(db,bid,reviewer,MONTH,PeriodPayload(action='reopen',expected_revision=3,reason='补录资料重新核对'))
    cash(admin)


def test_changed_source_after_submission_blocks_review(admin):
    period_post(admin,'submit')
    cash(admin)
    with SessionLocal() as db:
        bid=db.query(Building.id).filter_by(code='DEMO_A').scalar()
        with pytest.raises(HTTPException) as exc: store.period_action(db,bid,User(username='reviewer',role='admin'),MONTH,PeriodPayload(action='close',expected_revision=2,reason='独立复核测试'))
        assert exc.value.status_code==409


def test_closed_period_guards_old_receipt_contract_and_expense_writes(admin):
    cid=_seed_contract(YEAR)
    with SessionLocal() as db:
        bid=db.get(Contract,cid).building_id
        db.add(FinancePeriod(building_id=bid,period_month=MONTH,status='closed'));db.commit()
        for statement in [f"UPDATE contracts SET monthly_rent=1 WHERE id={cid}",f"INSERT INTO receivable_payments (contract_id,period_month,amount) VALUES ({cid},'{MONTH}',10)"]:
            with pytest.raises(IntegrityError): db.execute(text(statement));db.commit()
            db.rollback()
    post(admin,'/expenses',{'entry_key':str(uuid4()),'expense':{'name':'locked','category':'opex','amount_yuan':100,'occurrence_month':MONTH,'payment_month':MONTH},'detail':{'expected_revision':1,'supplier':'模拟供应商','cost_category':'维护','service_from':MONTH,'service_to':MONTH,'evidence':'模拟凭据'}},409)


def test_demo_seed_is_idempotent_and_finance_outcomes_available(admin):
    from app.scripts.seed_finance_demo import run
    with SessionLocal() as db:
        before=db.query(Contract).count()
        dry=run(db,False);assert db.query(Contract).count()==before
        result=run(db,True);assert result['changed']['contracts']==2
        result=run(db,True);assert all(v==0 for v in result['changed'].values())
    result=admin.get(PREFIX+'/outcomes',params={**SCOPE,'month':'2026-09'}).json()
    assert len(result['items'])==2
    assert all(row['comparison_available'] for row in result['items'])
    period=admin.get(PREFIX+'/period',params={**SCOPE,'month':'2026-09'}).json()
    assert len(period['facts']['issues'])>=2
    assert period['status']=='open'


def test_closed_month_allows_later_payment_without_changing_snapshot(admin):
    e=expense(admin,100)
    # January accrual, no January cash: fully documented and explicitly zero.
    period_post(admin,'submit',confirm={'income':'zero','receipts':'zero','expenses':'provided','payments':'zero'})
    with SessionLocal() as db:
        bid=db.query(Building.id).filter_by(code='DEMO_A').scalar();reviewer=User(username='reviewer',role='admin')
        store.period_action(db,bid,reviewer,MONTH,PeriodPayload(action='close',expected_revision=2,reason='资料完整独立复核'))
    c=cash(admin,100,direction='out',occurred_on='2026-02-15')
    allocate(admin,c,None,100,kind='expense',expense_id=e['id'])
    period=admin.get(PREFIX+'/period',params={**SCOPE,'month':MONTH}).json()
    assert period['status']=='closed' and period['stale'] is False
    assert period['facts']['metrics']['actual_opex_paid']==0
    assert _source(admin,YEAR,2)['forecast']['months'][1]['actual_opex_paid']==.01


def test_concurrent_allocations_cannot_overspend_same_cash(admin):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from app.main import app
    cid=_seed_contract(YEAR);c=cash(admin,100)
    def attempt(_):
        with TestClient(app) as client:
            client.cookies.update(admin.cookies)
            response=client.post(PREFIX+f'/cash/{c["id"]}/allocate',params=SCOPE,json={'entry_key':str(uuid4()),'expected_revision':1,'kind':'rent','amount':70,'period_month':MONTH,'contract_id':cid})
            return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,range(2)))
    assert sorted(results)==[200,409]
    with SessionLocal() as db: assert sum(a.amount for a in db.query(FinanceAllocation).filter_by(cash_id=c['id']))==70


def test_rent_overallocation_remains_unallocated(admin):
    cid=_seed_contract(YEAR);c=cash(admin,999999)
    post(admin,f'/cash/{c["id"]}/allocate',{'entry_key':str(uuid4()),'expected_revision':1,'kind':'rent','amount':999999,'period_month':MONTH,'contract_id':cid},422)
    with SessionLocal() as db: assert db.query(ReceivablePayment).count()==0


def test_legacy_receipt_cannot_be_declared_reconciled_without_link(admin):
    cid=_seed_contract(YEAR)
    with SessionLocal() as db:
        db.add(ReceivablePayment(contract_id=cid,period_month=MONTH,amount=100,payment_date=date(2026,1,10),allocation_type='收款',source='manual'));db.commit()
    period_post(admin,'submit',confirm={'income':'provided','receipts':'provided','expenses':'zero','payments':'zero'},code=422)
    p=admin.get(PREFIX+'/period',params={**SCOPE,'month':MONTH}).json()
    assert any('历史租金' in i['label'] for i in p['facts']['issues'])


def test_reversed_legacy_adoption_can_be_correctly_relinked(admin):
    cid=_seed_contract(YEAR)
    with SessionLocal() as db:
        p=ReceivablePayment(contract_id=cid,period_month=MONTH,amount=100,payment_date=date(2026,1,10),allocation_type='收款',source='manual');db.add(p);db.commit();pid=p.id
    a=allocate(admin,cash(admin),cid,existing_payment_id=pid)
    post(admin,f'/allocations/{a["id"]}/reverse',{'expected_revision':2,'reason':'关联资金选择错误'})
    allocate(admin,cash(admin),cid,existing_payment_id=pid)
    with SessionLocal() as db: assert db.query(ReceivablePayment).count()==1
