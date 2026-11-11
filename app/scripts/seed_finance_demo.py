"""Add the explicitly labelled 2026 finance demonstration without clearing data.

Run --dry-run before --apply. Stable keys reuse committed rows; no existing
contract, receipt, account or permission is replaced. Does not close periods.
"""
from datetime import date
from types import SimpleNamespace
import argparse
import json
from app.database import SessionLocal
from app.models import Building, Zone, Room, Contract, ContractRoom, ContractDeposit, FinancialExpense, FinancialPlanVersion, FinancialPlanningState
from app.finance_models import FinanceCash, FinanceExpenseDetail
from app.finance_schemas import CashPayload, AllocationPayload, BankLinePayload, BankImportPayload, MatchPayload, ExpenseCreatePayload
from app.financial_planning_schemas import ForecastRequest, SaveVersionRequest
from app.execution_schemas import CreateActionPayload, BenefitPayload
from app.services import finance_operations as finance, financial_planning_store as planning, execution_actions as actions
from app.services.contract_rent import calculate_monthly_rent
from app.services.contract_receivables import build_rent_plan

TAG='DEMO-FINANCE-20260928'
ACTOR=SimpleNamespace(username='模拟数据生成器',role='admin')


def counts(db):
    return {m.__tablename__:db.query(m).count() for m in (Contract,FinancialExpense,FinanceCash,FinancialPlanVersion)}


def run(db,apply=False):
    building=db.query(Building).filter_by(code='DEMO_A').one();bid=building.id
    result={'batch':TAG,'scope':'DEMO_A','mode':'apply' if apply else 'dry-run','before':counts(db),'preserves_existing':True,'periods_closed':0}
    if not apply:
        result['planned']='追加2份明确标注的模拟合同、13笔费用、租金/物业费/押金及分期付款、银行匹配与待处理差异、2个固定版本及措施事项；已存在的本批记录复用。'
        return result
    zone=db.query(Zone).filter_by(building_id=bid).order_by(Zone.id).first()
    def contract(suffix,start,end,area,unit):
        number=TAG+'-'+suffix
        prior=db.query(Contract).filter_by(contract_no=number).one_or_none()
        if prior:return prior
        room=Room(building_id=bid,zone_id=zone.id,tower=zone.name,floor='模拟',room_no='模拟财务-'+suffix,room_type='办公',area=area,is_leasable=True,is_deleted=False)
        db.add(room);db.flush()
        row=Contract(building_id=bid,contract_no=number,tower=zone.name,room_no=room.room_no,tenant_name='[模拟]业财演示客户-'+suffix,area=area,unit_price=unit,monthly_rent=calculate_monthly_rent(area,unit),free_period=0,property_fee_rate=12,property_fee_free=0,start_date=start,end_date=end,internal_contact_person='[模拟]招商主管',notes=TAG+'：功能演示专用，非真实租赁业务',is_deleted=False)
        db.add(row);db.flush()
        db.add(ContractRoom(building_id=bid,contract_id=row.id,room_id=room.id,tower=zone.name,room_no=room.room_no,area=area,unit_price=unit,monthly_rent=row.monthly_rent,start_date=start,end_date=end))
        db.add(ContractDeposit(contract_id=row.id,deposit_type='租赁押金',expected_amount=30000,notes=TAG+' 模拟押金'))
        db.commit();db.refresh(row);return row
    old=contract('A',date(2026,1,1),date(2026,12,31),100,4)
    # The forecast is fixed before creating the new lease, so its observed
    # addition can be compared with a baseline that truly excludes the measure.
    def cash(key,amount,day,direction='in',party='[模拟]业财演示客户-A'):
        data=finance.create_cash(db,bid,ACTOR,CashPayload(entry_key=TAG+'-'+key,direction=direction,occurred_on=day,amount=amount,counterparty=party,reference=TAG+'-'+key,note='[模拟]功能演示资金，非真实银行流水'))
        return db.get(FinanceCash,data['id'])
    def allocate(key,c,kind,amount,month,cid=None,eid=None,did=None):
        finance.allocation(db,bid,ACTOR,c.id,AllocationPayload(entry_key=TAG+'-'+key,expected_revision=c.revision,kind=kind,amount=amount,period_month=month,contract_id=cid,expense_id=eid,deposit_id=did,note='[模拟]功能演示分配'))
        db.refresh(c)
    def bankmatch(key,c,amount=None):
        value=amount if amount is not None else c.amount
        b=finance.import_bank(db,bid,ACTOR,BankImportPayload(rows=[BankLinePayload(import_key=TAG+'-'+key,occurred_on=c.occurred_on,direction=c.direction,amount=value,counterparty=c.counterparty,reference=c.reference,account_label='[模拟]演示结算账户')]))['items'][0]
        finance.match_bank(db,bid,ACTOR,b['id'],MatchPayload(entry_key=TAG+'-match-'+key,expected_revision=b['revision'],cash_revision=c.revision,cash_id=c.id,amount=value,note='[模拟]按日期、往来方及金额核对'))
        db.refresh(c)
    plan={r['month']:r for r in build_rent_plan(old)['plan']}
    for m in range(1,10):
        month=f'2026-{m:02d}';rent=plan[month]['rent'];fee=plan[month]['property_fee']
        # August rent arrives in September, showing cash/accounting period split.
        day=date(2026,9,3) if m==8 else date(2026,m,10)
        c=cash(f'rent-{m}',round(rent+fee,2),day)
        allocate(f'rent-alloc-{m}',c,'rent',rent,month,cid=old.id)
        if fee:allocate(f'fee-alloc-{m}',c,'property_fee',fee,month,cid=old.id)
        bankmatch(f'rent-bank-{m}',c)
    deposit=db.query(ContractDeposit).filter_by(contract_id=old.id).first()
    c=cash('deposit',30000,date(2026,1,5));allocate('deposit-alloc',c,'deposit',30000,'2026-01',cid=old.id,did=deposit.id);bankmatch('deposit-bank',c)
    expenses=[]
    for m in range(1,13):
        month=f'2026-{m:02d}';amount=7600+(m%3)*400
        e=finance.create_expense(db,bid,ACTOR,ExpenseCreatePayload(entry_key=TAG+f'-expense-{m}',expense={'name':f'[模拟]2026年{m}月物业运维','category':'opex','amount_yuan':amount,'occurrence_month':month,'payment_month':month,'owner':'[模拟]运营经办','notes':TAG},detail={'expected_revision':1,'supplier':'[模拟]物业运维供应商','cost_category':'物业运维','cost_center':'[模拟]DEMO_A运营','service_from':month,'service_to':month,'evidence':TAG+f'-凭据-{m:02d}（演示）'}))
        expenses.append(e)
        if m<=9:
            paid=amount if m<8 else amount/2
            c=cash(f'expense-payment-{m}',paid,date(2026,m,20),'out','[模拟]物业运维供应商')
            allocate(f'expense-alloc-{m}',c,'expense',paid,month,eid=e['id']);bankmatch(f'expense-bank-{m}',c)
    # One fee spanning three months tests exact cent allocation in the example.
    finance.create_expense(db,bid,ACTOR,ExpenseCreatePayload(entry_key=TAG+'-quarterly-expense',expense={'name':'[模拟]季度检修服务','category':'opex','amount_yuan':10000,'occurrence_month':'2026-09','payment_month':'2026-10','owner':'[模拟]工程经办','notes':TAG},detail={'expected_revision':1,'supplier':'[模拟]检修供应商','cost_category':'维修','cost_center':'[模拟]工程','service_from':'2026-09','service_to':'2026-11','evidence':TAG+'-季度检修凭据（演示）'}))
    advance=cash('unallocated-advance',5000,date(2026,9,25));bankmatch('advance-bank',advance)
    finance.import_bank(db,bid,ACTOR,BankImportPayload(rows=[BankLinePayload(import_key=TAG+'-unmatched-bank',occurred_on=date(2026,9,26),direction='out',amount=380,counterparty='[模拟]待确认供应商',reference='[模拟]待查款项',account_label='[模拟]演示结算账户')]))
    state=planning.planning_state(db,bid,2026)
    # Keep an existing user's active selections if they already had a plan.
    version=db.query(FinancialPlanVersion).filter_by(building_id=bid,name='[模拟]业财联动基准 '+TAG).first()
    if version is None:
        source=planning.build_forecast(db,bid,ForecastRequest(year=2026,closed_through_month=8))
        budget=db.query(FinancialPlanVersion).filter_by(building_id=bid,name='[模拟]年度经营预算 '+TAG).first()
        if budget is None:
            saved_budget=planning.save_version(db,bid,ACTOR,SaveVersionRequest(year=2026,closed_through_month=8,name='[模拟]年度经营预算 '+TAG,kind='budget',expected_revision=state.revision if state else 0,budget_months=[{'month':r['month'],'revenue':r['revenue'],'operating_cost':r['operating_cost']} for r in source['months']]),adopt=False)
            budget=planning.get_version(db,saved_budget['version']['id'],bid)
        state=planning.planning_state(db,bid,2026)
        saved=planning.save_version(db,bid,ACTOR,SaveVersionRequest(year=2026,closed_through_month=8,name='[模拟]业财联动基准 '+TAG,kind='forecast',expected_revision=state.revision,budget_version_id=budget.id,client_state={'scenario':{'baselineExcludesMeasures':True,'newLease':{'enabled':True,'startMonth':9,'freeMonths':0,'termMonths':4,'monthlyRent':.6,'collectionDelayMonths':0},'costSaving':{'enabled':True,'startMonth':9,'monthlySaving':.1,'implementationCost':.05}}}),adopt=False)
        version=planning.get_version(db,saved['version']['id'],bid)
    new=contract('B',date(2026,9,1),date(2026,12,31),50,4)
    rent=build_rent_plan(new)['plan'][0]['rent'];c=cash('new-lease-partial',3000,date(2026,9,15),party='[模拟]业财演示客户-B');allocate('new-lease-alloc',c,'rent',3000,'2026-09',cid=new.id);bankmatch('new-lease-bank',c)
    for key,cid,eid in [('newLease',new.id,None),('costSaving',None,expenses[8]['id'])]:
        action=actions.create_action(db,bid,ACTOR,CreateActionPayload(kind='measure',version_id=version.id,measure_key=key,expected_revision=0,owner='[模拟]措施负责人',due_on=date(2026,9,30),status='done',next_action='[模拟]查看业务观察值与计划差异',result_note='[模拟]任务办理完成，收益与资金到账分开核验',result_contract_id=cid))
        if not action['benefit']:
            actions.verify_benefit(db,db.query(__import__('app.models',fromlist=['ExecutionAction']).ExecutionAction).get(action['id']),ACTOR,BenefitPayload(expected_revision=action['revision'],amount_yuan=3000 if cid else 0,contract_id=cid,expense_id=eid,evidence='[模拟]人工核验示例；不代表真实经营收益。新租约仅核验模拟已收3000元，降本尚无实际节省。'))
    result['after']=counts(db);result['changed']={k:result['after'][k]-result['before'][k] for k in result['before']}
    return result


def main():
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--dry-run',action='store_true');group.add_argument('--apply',action='store_true');args=parser.parse_args()
    with SessionLocal() as db: print(json.dumps(run(db,args.apply),ensure_ascii=False))

if __name__=='__main__':main()
