from datetime import date, timedelta
import json
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.main import app
from app.models import Building, Contract, ExecutionAction, FinancialPlanVersion, FinancialExpense, OperationLog
from test_financial_planning_api import admin, year, _seed_contract, _save, _expense

PREFIX='/api/execution-actions'
SCOPE={'building':'DEMO_A'}
def payload(**kw):
    return {'kind':'expiry','expected_revision':0,'owner':'admin','due_on':str(date.today()-timedelta(days=1)),'next_action':'联系承租人确认到期安排',**kw}
def create(client, body):
    r=client.post(PREFIX,params=SCOPE,json=body);assert r.status_code==200,r.text;return r.json()
def update_body(row,**kw):
    fields=('owner','due_on','next_follow_up_on','status','decision','next_action','result_note','result_contract_id','follow_up_id')
    return {**{key:row[key] for key in fields},'expected_revision':row['revision'],**kw}

def test_canonical_record_revision_filters_and_audit(admin,year):
    cid=_seed_contract(year)
    row=create(admin,payload(contract_id=cid))
    again=create(admin,payload(contract_id=cid,owner='another',next_action='duplicate attempt'))
    assert again['id']==row['id'] and again['owner']=='admin'
    for filter in ('mine','overdue'):
        assert admin.get(PREFIX,params={**SCOPE,'filter':filter}).json()['items'][0]['id']==row['id']
    body=update_body(row,status='waiting',next_follow_up_on=str(date.today()))
    response=admin.put(f'{PREFIX}/{row["id"]}',params=SCOPE,json=body);assert response.status_code==200
    assert response.json()['revision']==2
    assert admin.put(f'{PREFIX}/{row["id"]}',params=SCOPE,json=body).status_code==409
    for filter in ('waiting','follow-up'):
        assert admin.get(PREFIX,params={**SCOPE,'filter':filter}).json()['total']==1
    with SessionLocal() as db:
        assert db.query(ExecutionAction).count()==1
        assert db.query(OperationLog).filter(OperationLog.target_type=='execution_action').count()==2

def test_done_is_not_verified_and_actual_renewal_is_required(admin,year):
    original=_seed_contract(year,suffix='OLD');successor=_seed_contract(year,suffix='NEW')
    row=create(admin,payload(contract_id=original,status='done',decision='renew',result_note='续租手续已处理',result_contract_id=successor))
    assert row['verification_state']=='pending'
    path=f'{PREFIX}/{row["id"]}/verify'
    assert admin.post(path,params=SCOPE,json={'expected_revision':1}).status_code==422
    with SessionLocal() as db:
        old=db.get(Contract,original);new=db.get(Contract,successor);old.disposal='已续租';new.prev_contract_no=old.contract_no;db.commit()
    r=admin.post(path,params=SCOPE,json={'expected_revision':1});assert r.status_code==200,r.text
    assert r.json()['verification_state']=='verified'
    options=admin.get(PREFIX+'/options',params={**SCOPE,'contract_id':original}).json()
    assert options['contracts'][0]['id']==successor
    with SessionLocal() as db:
        db.get(Contract,original).disposal='';db.commit()
    assert admin.get(f'{PREFIX}/{row["id"]}',params=SCOPE).json()['verification_state']=='stale'

def test_vacate_and_reopen_invalidate_verification(admin,year):
    cid=_seed_contract(year)
    with SessionLocal() as db: db.get(Contract,cid).disposal='已退租';db.commit()
    row=create(admin,payload(contract_id=cid,status='done',decision='vacate',result_note='交接完成'))
    r=admin.post(f'{PREFIX}/{row["id"]}/verify',params=SCOPE,json={'expected_revision':1});assert r.status_code==200
    row=r.json();assert row['verification_state']=='verified'
    r=admin.put(f'{PREFIX}/{row["id"]}',params=SCOPE,json=update_body(row,status='open'));assert r.json()['verification_state']=='pending'

def test_cross_scope_deleted_objects_and_anonymous_denied(admin,year):
    cid=_seed_contract(year,code='DEMO_C',suffix='FOREIGN')
    assert admin.post(PREFIX,params=SCOPE,json=payload(contract_id=cid)).status_code==404
    local=_seed_contract(year)
    row=create(admin,payload(contract_id=local))
    r=admin.put(f'{PREFIX}/{row["id"]}',params=SCOPE,json=update_body(row,result_contract_id=cid));assert r.status_code==404
    with SessionLocal() as db: db.get(Contract,local).is_deleted=True;db.commit()
    assert admin.get(f'{PREFIX}/{row["id"]}',params=SCOPE).status_code==404
    assert admin.get(PREFIX,params=SCOPE).json()['total']==0
    with TestClient(app) as anonymous: assert anonymous.get(PREFIX,params=SCOPE).status_code==401

def test_measure_execution_does_not_change_snapshot_or_realize_benefit(admin,year):
    state={'scenario':{'baselineExcludesMeasures':True,'costSaving':{'enabled':True,'startMonth':10,'monthlySaving':2,'implementationCost':1}}}
    expense=_expense(admin,year,amount_yuan=50000,occurrence_month=f'{year}-10',payment_month=f'{year}-10')
    for month in ('11','12'): _expense(admin,year,amount_yuan=50000,occurrence_month=f'{year}-{month}',payment_month=f'{year}-{month}')
    version=_save(admin,year,kind='scenario',client_state=state)['version']
    with SessionLocal() as db: before=db.get(FinancialPlanVersion,version['id']).snapshot_json
    body=payload(kind='measure',version_id=version['id'],measure_key='costSaving')
    row=create(admin,body)
    assert row['source']['measure']==state['scenario']['costSaving']
    assert create(admin,body)['id']==row['id']
    assert admin.post(f'{PREFIX}/{row["id"]}/benefit',params=SCOPE,json={'expected_revision':1,'amount_yuan':123,'evidence':'比较核验','expense_id':expense['id']}).status_code==422
    row=admin.put(f'{PREFIX}/{row["id"]}',params=SCOPE,json=update_body(row,status='done',result_note='措施执行完成')).json()
    assert row['benefit_state']=='pending'
    proof={'expected_revision':row['revision'],'amount_yuan':123.45,'evidence':'对比本月与合同基准费用，按凭证人工核验','expense_id':expense['id']}
    verified=admin.post(f'{PREFIX}/{row["id"]}/benefit',params=SCOPE,json=proof)
    assert verified.status_code==200,verified.text
    assert verified.json()['benefit_state']=='verified'
    assert verified.json()['benefit']['method']=='manual_attestation'
    with SessionLocal() as db: assert db.get(FinancialPlanVersion,version['id']).snapshot_json==before
    assert admin.get(PREFIX+'/options',params={**SCOPE,'version_id':version['id']}).json()['expenses'][0]['id']==expense['id']
    with SessionLocal() as db:
        db.get(FinancialExpense,expense['id']).revision+=1;db.commit()
    assert admin.get(f'{PREFIX}/{row["id"]}',params=SCOPE).json()['benefit_state']=='stale'

def test_inactive_or_foreign_saved_measures_cannot_create_actions(admin,year):
    version=_save(admin,year,kind='scenario')['version']
    assert admin.post(PREFIX,params=SCOPE,json=payload(kind='measure',version_id=version['id'],measure_key='costSaving')).status_code==422
    with SessionLocal() as db:
        db.get(FinancialPlanVersion,version['id']).building_id=db.query(Building.id).filter_by(code='DEMO_B').scalar();db.commit()
    assert admin.post(PREFIX,params=SCOPE,json=payload(kind='measure',version_id=version['id'],measure_key='costSaving')).status_code==404

def test_readonly_user_cannot_write_or_verify(admin,year):
    import secrets
    cid=_seed_contract(year)
    row=create(admin,payload(contract_id=cid))
    secret=secrets.token_urlsafe(24)
    created=admin.post('/api/auth/create-user',json={'username':'execution-viewer','password':secret,'display_name':'Read only','role':'viewer'})
    assert created.status_code==200
    with TestClient(app) as viewer:
        assert viewer.post('/api/auth/login',json={'username':'execution-viewer','password':secret}).status_code==200
        r=viewer.get(PREFIX,params=SCOPE);assert r.status_code==200,r.text
        assert r.json()['can_write'] is False
        assert viewer.put(f'{PREFIX}/{row["id"]}',params=SCOPE,json=update_body(row)).status_code in (403,404)
        assert viewer.post(f'{PREFIX}/{row["id"]}/verify',params=SCOPE,json={'expected_revision':1}).status_code in (403,404)


def test_invalid_saved_measure_cannot_become_execution(admin,year):
    state={'scenario':{'baselineExcludesMeasures':True,'costSaving':{'enabled':True,'startMonth':10,'monthlySaving':200,'implementationCost':1}}}
    version=_save(admin,year,kind='scenario',client_state=state)['version']
    response=admin.post(PREFIX,params=SCOPE,json=payload(kind='measure',version_id=version['id'],measure_key='costSaving'))
    assert response.status_code==422 and '超过基准' in response.json()['detail']
    with SessionLocal() as db: assert db.query(ExecutionAction).count()==0
