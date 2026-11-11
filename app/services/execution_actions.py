from __future__ import annotations
import json
import math
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from app.core.money import round_money
from app.core.time import now_in_timezone, today_in_timezone
from app.models import Contract, CustomerFollowUp, ExecutionAction, FinancialExpense
from app.services.financial_planning_store import canonical_json, get_version
from app.services.common import log_action
from app.services.legacy_demo_a_scope import ObjectScope


def contract_in_building(db, cid, bid):
    row=db.query(Contract).filter_by(id=cid, building_id=bid, is_deleted=False).one_or_none()
    if row is None: raise HTTPException(404, '关联合同不存在或无权访问')
    return row


def scoped_action(db, aid, ids):
    row=db.query(ExecutionAction).filter(ExecutionAction.id==aid, ExecutionAction.building_id.in_(ids)).one_or_none()
    if row is None: raise HTTPException(404, '处理记录不存在或无权访问')
    if row.contract_id: contract_in_building(db,row.contract_id,row.building_id)
    return row


def expiry_evidence(db, row):
    original=contract_in_building(db,row.contract_id,row.building_id)
    evidence={'decision':row.decision,'original_id':original.id,'disposal':original.disposal,'end_date':str(original.end_date)}
    if row.decision=='renew':
        if not row.result_contract_id: raise HTTPException(422,'请关联已经完成续签的后续合同')
        result=contract_in_building(db,row.result_contract_id,row.building_id)
        if result.id==original.id or result.prev_contract_no!=original.contract_no or original.disposal!='已续租':
            raise HTTPException(422,'尚未找到本合同的实际续签关系，请先通过合同续签操作完成')
        evidence.update(result_id=result.id, previous=result.prev_contract_no, start_date=str(result.start_date), result_end_date=str(result.end_date))
    elif row.decision=='vacate':
        if original.disposal!='已退租': raise HTTPException(422,'合同尚未完成退租，请先办理合同退租')
    else: raise HTTPException(422,'请先明确续租或退租决定')
    return evidence


def action_dict(db,row):
    item={key:getattr(row,key) for key in ('id','building_id','source_key','kind','contract_id','version_id','measure_key','title','owner','status','decision','next_action','result_note','result_contract_id','follow_up_id','revision','created_by')}
    for key in ('due_on','next_follow_up_on','created_at','updated_at'):
        value=getattr(row,key);item[key]=value.isoformat() if value else None
    item['source']=json.loads(row.source_json)
    item['verification']=json.loads(row.verification_json)
    item['benefit']=json.loads(row.benefit_json)
    item['verification_state']='pending'
    if item['verification']:
        try: current=expiry_evidence(db,row)
        except HTTPException: current=None
        item['verification_state']='verified' if current==item['verification'].get('evidence') and row.status=='done' else 'stale'
    item['benefit_state']='verified' if item['benefit'] and row.status=='done' else 'pending'
    if item['benefit']:
        try:
            if benefit_link_state(db,row.building_id,item['benefit'])!=item['benefit'].get('linked_state'):
                item['benefit_state']='stale'
        except HTTPException: item['benefit_state']='stale'
    today=today_in_timezone()
    item['overdue']=row.status!='done' and row.due_on<today
    item['follow_up_due']=row.status!='done' and row.next_follow_up_on is not None and row.next_follow_up_on<=today
    return item


def audit(db,row,user,action):
    # Includes the revision and saved fields so reopening/reverification is traceable.
    detail=canonical_json({'revision':row.revision,'owner':row.owner,'status':row.status,'decision':row.decision,'due_on':str(row.due_on),'next_action':row.next_action,'result_note':row.result_note,'result_contract_id':row.result_contract_id,'follow_up_id':row.follow_up_id,'verification':json.loads(row.verification_json),'benefit':json.loads(row.benefit_json)})
    log_action(db,row.contract_id,action,detail,user.username,commit=False,target_type='execution_action',target_id=row.id,object_scope=ObjectScope(scope_type='building',building_id=row.building_id))


def validate_links(db,bid,payload):
    if payload.result_contract_id: contract_in_building(db,payload.result_contract_id,bid)
    if payload.follow_up_id:
        follow=db.query(CustomerFollowUp).filter_by(id=payload.follow_up_id,building_id=bid).one_or_none()
        if follow is None: raise HTTPException(404,'关联跟进不存在或无权访问')
        return follow
    return None


def validate_saved_measure(snapshot,state,key,measure):
    scenario=state.get('scenario',{})
    def number(field,low,high,integer=False):
        raw=measure.get(field)
        if isinstance(raw,bool) or raw is None or raw=='': raise HTTPException(422,'保存版本的措施参数不完整')
        try: value=float(raw)
        except (TypeError,ValueError): raise HTTPException(422,'保存版本的措施参数无效')
        if not math.isfinite(value) or not low<=value<=high or (integer and value!=int(value)):
            raise HTTPException(422,'保存版本的措施参数超出范围')
        return value
    start=number('startMonth',1,24,True)
    if start<=snapshot.get('closed_through_month',0): raise HTTPException(422,'已发生措施应纳入基准，不能作为新增措施重复登记')
    if key=='costSaving':
        monthly=number('monthlySaving',0,1e8);number('implementationCost',0,1e8)
        if any(monthly>float(row.get('operating_cost',0))+0.000001 for row in snapshot.get('months',[]) if row['month']>=start):
            raise HTTPException(422,'保存版本的降本超过基准经营费用，请核对后另存有效方案')
    else:
        term=number('termMonths',1,600,True);free=number('freeMonths',0,600,True)
        number('monthlyRent',0,1e8);number('collectionDelayMonths',0,120,True)
        if free>term: raise HTTPException(422,'免租期不能超过租期')


def create_action(db,bid,user,payload):
    if payload.kind=='expiry':
        if not payload.contract_id or payload.version_id or payload.measure_key: raise HTTPException(422,'到期事项须关联一份合同')
        contract=contract_in_building(db,payload.contract_id,bid)
        key=f'expiry:{contract.id}'
        title=f'{contract.contract_no} · 到期安排'
        source={'end_date':str(contract.end_date),'contract_no':contract.contract_no}
    else:
        if not payload.version_id or not payload.measure_key or payload.contract_id: raise HTTPException(422,'措施须关联固定方案版本和具体措施')
        version=get_version(db,payload.version_id,bid)
        if version.kind=='budget': raise HTTPException(422,'请从已保存的预测或具名方案建立措施')
        state=json.loads(version.client_state_json)
        measure=state.get('scenario',{}).get(payload.measure_key,{})
        if not isinstance(measure,dict) or measure.get('enabled') is not True: raise HTTPException(422,'此保存版本没有启用该措施')
        if state.get('scenario',{}).get('baselineExcludesMeasures') is not True: raise HTTPException(422,'保存版本尚未确认措施不在基准中')
        validate_saved_measure(json.loads(version.snapshot_json),state,payload.measure_key,measure)
        key=f'measure:{version.id}:{payload.measure_key}'
        title=f'{version.name} · '+('新增租约' if payload.measure_key=='newLease' else '经营降本')
        source={'version_id':version.id,'version_name':version.name,'year':version.year,'revision':version.revision,'source_digest':version.source_digest,'measure':measure}
    existing=db.query(ExecutionAction).filter_by(building_id=bid,source_key=key).one_or_none()
    if existing: return action_dict(db,existing)
    if payload.expected_revision!=0: raise HTTPException(409,'记录尚未建立，请刷新')
    follow=validate_links(db,bid,payload)
    if follow and payload.kind=='expiry' and follow.contract_id!=payload.contract_id: raise HTTPException(422,'请选择同一合同的跟进记录')
    fields=payload.model_dump(exclude={'expected_revision','kind','contract_id','version_id','measure_key'})
    row=ExecutionAction(building_id=bid,source_key=key,kind=payload.kind,contract_id=payload.contract_id,version_id=payload.version_id,measure_key=payload.measure_key,title=title,source_json=canonical_json(source),created_by=user.username,**fields)
    db.add(row)
    try: db.flush()
    except IntegrityError:
        db.rollback()
        existing=db.query(ExecutionAction).filter_by(building_id=bid,source_key=key).one_or_none()
        if existing: return action_dict(db,existing)
        raise
    audit(db,row,user,'execution_create');db.commit()
    return action_dict(db,row)


def revision_update(db,row,expected,values):
    changed=db.query(ExecutionAction).filter_by(id=row.id,revision=expected).update({**values,'revision':expected+1,'updated_at':now_in_timezone()},synchronize_session=False)
    if changed!=1:
        db.rollback();raise HTTPException(409,'事项已被更新，请重新读取后保存；你的内容尚未覆盖他人修改')
    db.refresh(row)


def update_action(db,row,user,payload):
    follow=validate_links(db,row.building_id,payload)
    if follow and row.kind=='expiry' and follow.contract_id!=row.contract_id: raise HTTPException(422,'请选择同一合同的跟进记录')
    values=payload.model_dump(exclude={'expected_revision'})
    values.update(verification_json='{}',benefit_json='{}')
    revision_update(db,row,payload.expected_revision,values)
    audit(db,row,user,'execution_update');db.commit();return action_dict(db,row)


def verify_expiry(db,row,user,revision):
    if row.kind!='expiry' or row.status!='done': raise HTTPException(422,'请先完成到期事项并填写结果')
    evidence=expiry_evidence(db,row)
    proof={'by':user.username,'at':now_in_timezone().isoformat(),'evidence':evidence}
    revision_update(db,row,revision,{'verification_json':canonical_json(proof)})
    audit(db,row,user,'execution_verify');db.commit();return action_dict(db,row)


def benefit_link_state(db,bid,proof):
    state={}
    if proof.get('contract_id'):
        contract=contract_in_building(db,proof['contract_id'],bid)
        state['contract']={'id':contract.id,'updated_at':str(contract.updated_at),'disposal':contract.disposal,'end_date':str(contract.end_date)}
    if proof.get('expense_id'):
        expense=db.query(FinancialExpense).filter_by(id=proof['expense_id'],building_id=bid,is_active=True).one_or_none()
        if expense is None: raise HTTPException(404,'费用记录不存在或无权访问')
        state['expense']={'id':expense.id,'revision':expense.revision}
    return state


def verify_benefit(db,row,user,payload):
    if row.kind!='measure' or row.status!='done': raise HTTPException(422,'任务完成后再单独登记收益核验')
    if not payload.evidence.strip(): raise HTTPException(422,'请填写核验依据')
    if row.measure_key=='newLease' and not payload.contract_id: raise HTTPException(422,'新增租约收益须关联实际合同')
    if row.measure_key=='costSaving' and not payload.expense_id: raise HTTPException(422,'降本核验须关联费用记录，并说明比较基准')
    proof=payload.model_dump(exclude={'expected_revision'});proof['amount_yuan']=round_money(payload.amount_yuan)
    if payload.contract_id: contract_in_building(db,payload.contract_id,row.building_id)
    if payload.expense_id and not db.query(FinancialExpense.id).filter_by(id=payload.expense_id,building_id=row.building_id,is_active=True).scalar(): raise HTTPException(404,'费用记录不存在或无权访问')
    proof.update(by=user.username,at=now_in_timezone().isoformat(),method='manual_attestation',linked_state=benefit_link_state(db,row.building_id,proof))
    revision_update(db,row,payload.expected_revision,{'benefit_json':canonical_json(proof)})
    audit(db,row,user,'execution_benefit_verify');db.commit();return action_dict(db,row)
