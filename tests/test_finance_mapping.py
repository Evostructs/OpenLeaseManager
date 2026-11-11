from copy import deepcopy
import json
from fastapi.testclient import TestClient
import pytest
from app.database import SessionLocal
from app.main import app
from app.models import Building, OperationLog, SystemConfig, User
from app.services import finance_operations as store
from app.finance_schemas import PeriodPayload
from app.core.handoff_integrity import verify_package
from tests.test_financial_planning_api import admin, _login
from tests.test_finance_handoff import PREFIX, preview, download, package, close_expense_month
from tests.test_finance_operations import SCOPE, MONTH, cash, expense, allocate, post, period_post

def mapping(**changes):
    return {'expected_revision':0,'company_name':'模拟园区运营公司','company_code':'PARK01',
        'erp_system':'模拟财务系统','book_code':'BOOK01','tax_basis':'gross',
        'accounts':{'rent':'6001.01','property_fee':'6001.02','deposit':'2241','opex':'6401','capex':'1601'},
        'bank_accounts':{'结算账户':'1002.01'},'confirmed':True,'note':'模拟科目表 V1，财务已核对',**changes}

def save(client, body, status=200):
    response=client.put(PREFIX+'/mapping',params=SCOPE,json=body)
    assert response.status_code==status,response.text
    return response.json()

def mapped_export(client, summary, status=200):
    response=client.get(PREFIX+'/export',params={**SCOPE,'month':MONTH,'mode':summary['mode'],
        'expected_revision':summary['revision'],'expected_digest':summary['handoff_digest'],'require_mapping':True})
    assert response.status_code==status,response.text if status!=200 else response.status_code
    return response

def test_configuration_cas_audit_and_changed_mapping_invalidates_preview(admin):
    empty=admin.get(PREFIX+'/mapping',params=SCOPE).json()
    assert empty['revision']==0 and not empty['ready']
    before=preview(admin)
    saved=save(admin,mapping())
    assert saved['ready'] and saved['revision']==1 and saved['confirmed_by']=='admin'
    download(admin,before,409)
    save(admin,mapping(),409)
    after=preview(admin)
    assert before['source_digest']==after['source_digest'] and before['handoff_digest']!=after['handoff_digest']
    with SessionLocal() as db:
        log=db.query(OperationLog).filter_by(action='finance_mapping_save').one()
        assert log.building_id==db.query(Building.id).filter_by(code='DEMO_A').scalar()

@pytest.mark.parametrize('change',[
    {'accounts':{'rent':'6001'}}, {'bank_accounts':{}}, {'tax_basis':''},
    {'book_code':'=1+1'}, {'bank_accounts':{'结算账户':'1002',' 结算账户 ':'1003'}},
])
def test_invalid_or_incomplete_confirmation_does_not_write(admin,change):
    save(admin,mapping(**change),422)
    assert admin.get(PREFIX+'/mapping',params=SCOPE).json()['revision']==0

def test_mapping_read_write_and_scope_access(admin):
    assert admin.post('/api/auth/create-user',json={'username':'mapping_viewer','password':'Viewer123','role':'viewer'}).status_code==200
    with TestClient(app) as viewer:
        _login(viewer,'mapping_viewer','Viewer123')
        assert viewer.get(PREFIX+'/mapping',params=SCOPE).status_code==200
        save(viewer,mapping(),404)
    with TestClient(app) as anon:
        assert anon.get(PREFIX+'/mapping',params=SCOPE).status_code==401
    assert admin.get(PREFIX+'/mapping',params={'building':'DEMO_B'}).status_code==404
    assert admin.put(PREFIX+'/mapping',params={'building':'DEMO_B'},json=mapping()).status_code==404

def test_mapping_confirmation_gate_and_frozen_profile_in_closed_export(admin):
    close_expense_month(admin)
    closed=preview(admin,'closed')
    mapped_export(admin,closed,409)
    draft_config=save(admin,mapping(confirmed=False))
    assert not draft_config['ready']
    mapped_export(admin,preview(admin,'closed'),409)
    first=save(admin,mapping(expected_revision=1))
    content=mapped_export(admin,preview(admin,'closed')).content
    assert verify_package(content)['status']=='PASS'
    archive,manifest,snapshot,wb=package(mapped_export(admin,preview(admin,'closed')))
    assert manifest['mapping_required'] and snapshot['customer_mapping']['revision']==first['revision']
    assert wb['财务映射']['B2'].value=='模拟园区运营公司'
    old=preview(admin,'closed')
    save(admin,mapping(expected_revision=2,accounts={**mapping()['accounts'],'rent':'6001.99'}))
    mapped_export(admin,old,409)
    assert verify_package(content)['status']=='PASS'
    assert json.loads(archive.read('tables.json'))['customer_mapping']['accounts']['rent']=='6001.01'
    mapped_export(admin,preview(admin),409)

def test_all_bank_labels_in_month_must_have_a_confirmed_mapping(admin):
    e=expense(admin,100)
    c=cash(admin,100,direction='out')
    allocate(admin,c,None,100,kind='expense',expense_id=e['id'])
    b=post(admin,'/bank/import',{'rows':[{'import_key':'mapping-bank','occurred_on':MONTH+'-10',
        'direction':'out','amount':100,'counterparty':'模拟往来方','account_label':'第二账户'}]})
    ledger=admin.get('/api/finance-operations/ledger',params={**SCOPE,'month':MONTH}).json()
    post(admin,f'/bank/{ledger["banks"][0]["id"]}/match',{'entry_key':'mapping-match','expected_revision':1,
        'cash_id':c['id'],'cash_revision':2,'amount':100,'note':'模拟核对一致'})
    period_post(admin,'submit',confirm={'income':'zero','receipts':'zero','expenses':'provided','payments':'provided'})
    with SessionLocal() as db:
        bid=db.query(Building.id).filter_by(code='DEMO_A').scalar()
        store.period_action(db,bid,User(username='reviewer',role='admin'),MONTH,
            PeriodPayload(action='close',expected_revision=2,reason='独立核对模拟单据'))
    save(admin,mapping())
    summary=preview(admin,'closed')
    assert not summary['customer_mapping']['ready']
    assert any('第二账户' in gap for gap in summary['customer_mapping']['missing'])
    mapped_export(admin,summary,409)
    save(admin,mapping(expected_revision=1,bank_accounts={'结算账户':'1002.01','第二账户':'1002.02'}))
    assert verify_package(mapped_export(admin,preview(admin,'closed')).content)['status']=='PASS'

def test_corrupt_mapping_and_oversized_export_fail_without_export_audit(admin,monkeypatch):
    summary=preview(admin)
    from app.services import finance_handoff
    monkeypatch.setattr(finance_handoff,'MAX_BYTES',1)
    download(admin,summary,413)
    with SessionLocal() as db:
        assert db.query(OperationLog).filter_by(action='finance_handoff_export').count()==0
        bid=db.query(Building.id).filter_by(code='DEMO_A').scalar()
        db.add(SystemConfig(key=f'finance_mapping:{bid}',value='broken json'));db.commit()
    assert admin.get(PREFIX+'/preview',params={**SCOPE,'month':MONTH}).status_code==409
