"""Atomic cash allocation, bank reconciliation and reviewed period snapshots."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from fastapi import HTTPException
from sqlalchemy import or_, func
from app.core.time import now_in_timezone
from app.models import FinancialPlanVersion, Contract, ContractDeposit, ReceivablePayment, ReceivableRecord, ReceivableStatement, FinancialExpense, ExecutionAction
from app.finance_models import FinanceCash, FinanceAllocation, FinanceBankLine, FinanceBankMatch, FinanceExpenseDetail, FinancePeriod, FinancePeriodEvent
from app.services.financial_planning_store import canonical_json, _log, all_expenses


def fen(value):
    return int((Decimal(str(value or 0))*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def serialize(row):
    return {c.name: (v.isoformat() if isinstance(v, date) else v) for c in row.__table__.columns
            if c.name not in {'request_hash','creation_hash','creation_key'} for v in [getattr(row, c.name)]}


def scoped(db, model, bid, rid):
    row = db.query(model).filter_by(id=rid, building_id=bid).one_or_none()
    if row is None: raise HTTPException(404, '记录不存在或不属于当前项目')
    return row


def reserve(db, row, revision):
    if db.query(type(row)).filter_by(id=row.id, revision=revision).update({'revision':revision+1}, synchronize_session=False)!=1:
        raise HTTPException(409, '记录已变化，请刷新后核对；本次内容未覆盖他人修改')
    db.refresh(row)


def replay(db, model, bid, key, payload, extra=None):
    value = payload.model_dump(mode='json', exclude={'expected_revision','cash_revision'})
    if 'amount' in value: value['amount']=f'{payload.amount:.2f}'
    hashed = digest({**value, **(extra or {})})
    row = db.query(model).filter_by(building_id=bid, **{('import_key' if model is FinanceBankLine else 'entry_key'):key}).one_or_none()
    if row and row.request_hash != hashed: raise HTTPException(409, '相同操作编号已用于不同内容，请核对原记录')
    return row, hashed


def finish(db, bid, user, row, action):
    db.flush()
    _log(db, bid, user, action, row.__tablename__, getattr(row,'id',getattr(row,'expense_id',0)), action)
    result=serialize(row); db.commit(); return result


def create_cash(db, bid, user, payload):
    row, hashed = replay(db, FinanceCash, bid, payload.entry_key, payload)
    if row: return serialize(row)
    row=FinanceCash(building_id=bid, request_hash=hashed, created_by=user.username, **payload.model_dump())
    db.add(row)
    return finish(db,bid,user,row,'finance_cash_create')


def allocated(db, cash_id):
    return sum(fen(r.amount) for r in db.query(FinanceAllocation).filter_by(cash_id=cash_id,voided=False))


def expense_paid(db, expense_id):
    return sum(fen(r.amount) for r in db.query(FinanceAllocation).filter_by(expense_id=expense_id,voided=False))


def allocation(db, bid, user, cash_id, payload):
    row, hashed = replay(db,FinanceAllocation,bid,payload.entry_key,payload,{'cash_id':cash_id})
    if row: return serialize(row)
    cash=scoped(db,FinanceCash,bid,cash_id)
    reserve(db,cash,payload.expected_revision)
    if cash.voided or fen(payload.amount)>fen(cash.amount)-allocated(db,cash_id): raise HTTPException(422,'分配金额超过该笔资金的待分配余额')
    if payload.kind!='rent' and payload.existing_payment_id: raise HTTPException(422,'只有租金可关联历史收款')
    if payload.kind=='expense':
        if payload.contract_id or payload.deposit_id: raise HTTPException(422,'费用付款不能同时关联合同或押金')
        expense=scoped(db,FinancialExpense,bid,payload.expense_id)
        if cash.direction!='out' or not expense.is_active or expense.paid_on: raise HTTPException(422,'请选择尚未通过旧方式标记全额支付的有效费用及付款流水')
        reserve(db,expense,expense.revision)
        if fen(payload.amount)>fen(expense.amount_yuan)-expense_paid(db,expense.id): raise HTTPException(422,'付款超过费用剩余应付')
        if payload.period_month!=expense.occurrence_month: raise HTTPException(422,'费用所属账期须与费用发生月一致；资金日期可跨期')
    else:
        contract=scoped(db,Contract,bid,payload.contract_id)
        if contract.is_deleted: raise HTTPException(404,'合同已归档')
        if payload.expense_id or (payload.kind!='deposit' and payload.deposit_id): raise HTTPException(422,'关联对象与分配类型不一致')
        if payload.kind!='deposit' and cash.direction!='in': raise HTTPException(422,'租金与物业费请选择收款流水')
        if payload.kind in {'rent','property_fee'}:
            from app.services.contract_receivables import build_rent_plan, _ensure_month_within_contract
            _ensure_month_within_contract(contract,payload.period_month,date.fromisoformat(payload.period_month+'-01'))
            statement=db.query(ReceivableStatement).filter_by(contract_id=contract.id,period_month=payload.period_month).one_or_none()
            if payload.kind=='rent' and not payload.existing_payment_id:
                from app.services.contract_receivables import _payment_summary
                plan={r['month']:r for r in build_rent_plan(contract)['plan']}
                expected=fen(statement.rent if statement else plan.get(payload.period_month,{}).get('rent',0))
                record=db.query(ReceivableRecord).filter_by(contract_id=contract.id,period_month=payload.period_month).one_or_none()
                payments=db.query(ReceivablePayment).filter_by(contract_id=contract.id,period_month=payload.period_month).all()
                summary=_payment_summary(payments,record)
                used=fen(summary['settlement_amount'])
                if fen(payload.amount)>expected-used: raise HTTPException(422,'租金分配超过该账期未结应收，超额部分请保留为待分配')
            if payload.kind=='property_fee':
                plan={r['month']:r for r in build_rent_plan(contract)['plan']}
                expected=fen(statement.property_fee if statement else plan.get(payload.period_month,{}).get('property_fee',0))
                used=sum(fen(r.amount) for r in db.query(FinanceAllocation).filter_by(contract_id=contract.id,period_month=payload.period_month,kind='property_fee',voided=False))
                if fen(payload.amount)>expected-used: raise HTTPException(422,'物业费分配超过该账期应收，超额部分请保留为待分配')
    values=payload.model_dump(exclude={'expected_revision','existing_payment_id'})
    row=FinanceAllocation(building_id=bid,cash_id=cash_id,request_hash=hashed,created_by=user.username,**values)
    if payload.kind=='rent':
        if payload.existing_payment_id:
            payment=db.query(ReceivablePayment).filter_by(id=payload.existing_payment_id,contract_id=payload.contract_id,period_month=payload.period_month,allocation_type='收款').one_or_none()
            if not payment or fen(payment.amount)!=fen(payload.amount) or payment.payment_date!=cash.occurred_on or payment.source.startswith('finance_'):
                raise HTTPException(422,'历史收款的合同、账期、日期和金额必须完全一致，且尚未关联资金分配')
        else:
            # Materialize the old summary before the first detailed receipt, so
            # adding a new receipt never hides already recorded legacy money.
            record=db.query(ReceivableRecord).filter_by(contract_id=payload.contract_id,period_month=payload.period_month).one_or_none()
            if record and fen(record.received_amount) and not db.query(ReceivablePayment.id).filter_by(contract_id=payload.contract_id,period_month=payload.period_month).first():
                db.add(ReceivablePayment(contract_id=payload.contract_id,period_month=payload.period_month,amount=record.received_amount,payment_date=record.received_date,operator=user.username,source='legacy_summary',allocation_type='收款'))
            payment=ReceivablePayment(contract_id=payload.contract_id,period_month=payload.period_month,amount=float(payload.amount),payment_date=cash.occurred_on,operator=user.username,source='finance_cash',allocation_type='收款',reference_no=cash.reference[:100],notes=payload.note)
            db.add(payment);db.flush()
        row.payment_id=payment.id
    if payload.kind=='deposit':
        deposit=db.query(ContractDeposit).filter_by(id=payload.deposit_id,contract_id=payload.contract_id).one_or_none()
        if not deposit: raise HTTPException(404,'请选择该合同已有的押金记录')
        from app.services.deposits import _sync_status
        if cash.direction=='in':
            deposit.received_amount=(fen(deposit.received_amount)+fen(payload.amount))/100
            deposit.received_date=cash.occurred_on
        else:
            deposit.refunded_amount=(fen(deposit.refunded_amount)+fen(payload.amount))/100
            deposit.refund_date=cash.occurred_on
        _sync_status(deposit)
    db.add(row)
    return finish(db,bid,user,row,'finance_allocate')


def reverse_allocation(db,bid,user,rid,payload):
    row=scoped(db,FinanceAllocation,bid,rid);cash=scoped(db,FinanceCash,bid,row.cash_id)
    if row.voided: return serialize(row)
    reserve(db,cash,payload.expected_revision)
    if row.expense_id:
        expense=scoped(db,FinancialExpense,bid,row.expense_id);reserve(db,expense,expense.revision)
    if row.payment_id:
        payment=db.get(ReceivablePayment,row.payment_id)
        if payment.source=='finance_cash':
            reversal=ReceivablePayment(contract_id=row.contract_id,period_month=row.period_month,amount=-row.amount,payment_date=cash.occurred_on,allocation_type='收款',source='finance_reverse',operator=user.username,notes=payload.reason)
            db.add(reversal);db.flush();row.reversal_payment_id=reversal.id
    if row.deposit_id:
        from app.services.deposits import _sync_status
        deposit=db.get(ContractDeposit,row.deposit_id)
        key='received_amount' if cash.direction=='in' else 'refunded_amount'
        setattr(deposit,key,(fen(getattr(deposit,key))-fen(row.amount))/100)
        _sync_status(deposit)
    row.voided=True;row.note+='\n撤销：'+payload.reason
    return finish(db,bid,user,row,'finance_allocation_reverse')


def void_cash(db,bid,user,rid,payload):
    row=scoped(db,FinanceCash,bid,rid)
    if row.voided: return serialize(row)
    reserve(db,row,payload.expected_revision)
    if allocated(db,row.id) or db.query(FinanceBankMatch.id).filter_by(cash_id=row.id,voided=False).first(): raise HTTPException(422,'请先撤销资金分配和银行匹配，再作废流水')
    row.voided=True;row.note+='\n作废：'+payload.reason
    return finish(db,bid,user,row,'finance_cash_void')


def import_bank(db,bid,user,payload):
    items=[]
    for item in payload.rows:
        row, hashed=replay(db,FinanceBankLine,bid,item.import_key,item)
        if not row:
            row=FinanceBankLine(building_id=bid,request_hash=hashed,created_by=user.username,**item.model_dump());db.add(row);db.flush()
        items.append(serialize(row))
    _log(db,bid,user,'finance_bank_import','finance_bank_lines',0,f'导入或复用 {len(items)} 条银行流水')
    db.commit();return {'items':items}


def matched(db,**filters):
    return sum(fen(r.amount) for r in db.query(FinanceBankMatch).filter_by(voided=False,**filters))


def match_bank(db,bid,user,rid,payload):
    row, hashed=replay(db,FinanceBankMatch,bid,payload.entry_key,payload,{'bank_id':rid})
    if row: return serialize(row)
    bank=scoped(db,FinanceBankLine,bid,rid);cash=scoped(db,FinanceCash,bid,payload.cash_id)
    reserve(db,bank,payload.expected_revision);reserve(db,cash,payload.cash_revision)
    if cash.voided or cash.direction!=bank.direction: raise HTTPException(422,'资金与银行流水收付方向不一致或已作废')
    if fen(payload.amount)>min(fen(bank.amount)-matched(db,bank_id=rid),fen(cash.amount)-matched(db,cash_id=cash.id)): raise HTTPException(422,'匹配金额超过银行或资金流水剩余金额')
    if (cash.occurred_on!=bank.occurred_on or cash.counterparty!=bank.counterparty) and len(payload.note)<3: raise HTTPException(422,'日期或往来方不一致，请说明人工匹配依据')
    row=FinanceBankMatch(building_id=bid,bank_id=rid,request_hash=hashed,created_by=user.username,**payload.model_dump(exclude={'expected_revision','cash_revision'}));db.add(row)
    return finish(db,bid,user,row,'finance_bank_match')


def reverse_match(db,bid,user,rid,payload):
    row=scoped(db,FinanceBankMatch,bid,rid)
    if row.voided: return serialize(row)
    bank=scoped(db,FinanceBankLine,bid,row.bank_id);reserve(db,bank,payload.expected_revision)
    cash=scoped(db,FinanceCash,bid,row.cash_id);reserve(db,cash,cash.revision)
    row.voided=True;row.note+='\n撤销：'+payload.reason
    return finish(db,bid,user,row,'finance_bank_reverse')


def expense_details(db,bid,user,rid,payload):
    expense=scoped(db,FinancialExpense,bid,rid);reserve(db,expense,payload.expected_revision)
    detail=db.get(FinanceExpenseDetail,rid)
    if detail is None: detail=FinanceExpenseDetail(expense_id=rid);db.add(detail)
    for key,value in payload.model_dump(exclude={'expected_revision'}).items(): setattr(detail,key,value)
    return finish(db,bid,user,detail,'finance_expense_detail')


def enrich_expenses(db,bid,items):
    details={r.expense_id:r for r in db.query(FinanceExpenseDetail).join(FinancialExpense).filter(FinancialExpense.building_id==bid)}
    payments={}
    for alloc,cash in db.query(FinanceAllocation,FinanceCash).join(FinanceCash,FinanceCash.id==FinanceAllocation.cash_id).filter(FinanceAllocation.building_id==bid,FinanceAllocation.kind=='expense',FinanceAllocation.voided.is_(False)):
        payments.setdefault(alloc.expense_id,[]).append({'id':alloc.id,'cash_id':cash.id,'paid_on':cash.occurred_on.isoformat(),'amount_yuan':alloc.amount})
    for item in items:
        detail=details.get(item['id']);item['detail']=serialize(detail) if detail else None
        item['payments']=payments.get(item['id'],[])
        item['paid_yuan']=round(sum(p['amount_yuan'] for p in item['payments']),2) if item['payments'] else (item['amount_yuan'] if item['paid_on'] else 0)
        item['unpaid_yuan']=round(item['amount_yuan']-item['paid_yuan'],2)
    return items


def ledger(db,bid,month,page=1):
    cashq=db.query(FinanceCash).filter_by(building_id=bid).filter(FinanceCash.occurred_on>=date.fromisoformat(month+'-01'),FinanceCash.occurred_on<date.fromisoformat(month+'-01')+__import__('dateutil.relativedelta',fromlist=['relativedelta']).relativedelta(months=1))
    bankq=db.query(FinanceBankLine).filter_by(building_id=bid).filter(FinanceBankLine.occurred_on>=date.fromisoformat(month+'-01'),FinanceBankLine.occurred_on<date.fromisoformat(month+'-01')+__import__('dateutil.relativedelta',fromlist=['relativedelta']).relativedelta(months=1))
    cash=[];banks=[]
    alloc_totals={rid:fen(amount) for rid,amount in db.query(FinanceAllocation.cash_id,func.sum(FinanceAllocation.amount)).filter_by(building_id=bid,voided=False).group_by(FinanceAllocation.cash_id)}
    bank_totals={rid:fen(amount) for rid,amount in db.query(FinanceBankMatch.bank_id,func.sum(FinanceBankMatch.amount)).filter_by(building_id=bid,voided=False).group_by(FinanceBankMatch.bank_id)}
    cash_totals={rid:fen(amount) for rid,amount in db.query(FinanceBankMatch.cash_id,func.sum(FinanceBankMatch.amount)).filter_by(building_id=bid,voided=False).group_by(FinanceBankMatch.cash_id)}
    for row in cashq.order_by(FinanceCash.id.desc()).offset((page-1)*25).limit(25):
        item=serialize(row);item['remaining']= (fen(row.amount)-alloc_totals.get(row.id,0))/100;item['bank_remaining']=(fen(row.amount)-cash_totals.get(row.id,0))/100
        item['allocations']=[serialize(a) for a in db.query(FinanceAllocation).filter_by(cash_id=row.id).order_by(FinanceAllocation.id)]
        cash.append(item)
    for row in bankq.order_by(FinanceBankLine.id.desc()).offset((page-1)*25).limit(25):
        item=serialize(row);item['remaining']=(fen(row.amount)-bank_totals.get(row.id,0))/100
        item['matches']=[serialize(a) for a in db.query(FinanceBankMatch).filter_by(bank_id=row.id)]
        # Suggestions never post money and remain explicitly reviewable.
        candidates=db.query(FinanceCash).filter_by(building_id=bid,direction=row.direction,voided=False).filter(or_(FinanceCash.reference==row.reference if row.reference else False,FinanceCash.counterparty==row.counterparty,FinanceCash.amount==row.amount)).order_by(FinanceCash.occurred_on.desc()).limit(50)
        item['suggestions']=[{**serialize(c),'bank_remaining':(fen(c.amount)-cash_totals.get(c.id,0))/100} for c in candidates if fen(c.amount)>cash_totals.get(c.id,0)]
        banks.append(item)
    return {'cash':cash,'banks':banks,'cash_total':cashq.count(),'bank_total':bankq.count(),'page':page,'page_size':25}


def period_facts(db,bid,month):
    from app.services.financial_planning_store import build_forecast
    from app.financial_planning_schemas import ForecastRequest
    forecast=build_forecast(db,bid,ForecastRequest(year=int(month[:4]),closed_through_month=int(month[5:])),_period_month=month)
    metrics=next(r for r in forecast['months'] if r['period']==month)
    start=date.fromisoformat(month+'-01')
    from dateutil.relativedelta import relativedelta
    end=start+relativedelta(months=1)
    cash=list(db.query(FinanceCash).filter_by(building_id=bid,voided=False).filter(FinanceCash.occurred_on>=start,FinanceCash.occurred_on<end))
    banks=list(db.query(FinanceBankLine).filter_by(building_id=bid).filter(FinanceBankLine.occurred_on>=start,FinanceBankLine.occurred_on<end))
    unallocated=sum(fen(c.amount)-allocated(db,c.id) for c in cash)/100
    unmatched=sum(fen(b.amount)-matched(db,bank_id=b.id) for b in banks)/100
    unbanked=sum(fen(c.amount)-matched(db,cash_id=c.id) for c in cash)/100
    sources=[{'key':s['source_key'],'label':s['label'],'values':next((m for m in s['months'] if m['period']==month),{}),'evidence':s['source_snapshot']} for s in forecast['sources'] if any(m['period']==month and any(m.get(k,0) for k in ('revenue','operating_cost','actual_cash_received','actual_opex_paid','actual_capex_paid')) for m in s['months'])]
    # Keep only evidence relevant to this cutoff. A later payment against a
    # closed month's payable must not invalidate the earlier closing snapshot.
    for source in sources:
        evidence=source['evidence']
        for key in ('revision','updated_at','paid_yuan','unpaid_yuan','payment_status','effective_payment_month'):
            evidence.pop(key,None)
        if 'payments' in evidence:
            evidence['payments']=[p for p in evidence['payments'] if (p.get('payment_date') or p.get('paid_on') or '')[:7]<=month]
        if 'receivable_statements' in evidence:
            evidence['receivable_statements']=[p for p in evidence['receivable_statements'] if p['month']<=month]
    issues=[]
    for value,label in ((unallocated,'资金尚未分配'),(unmatched,'银行流水尚未匹配'),(unbanked,'资金尚未与银行核对')):
        if value: issues.append({'label':label,'amount_yuan':value,'owner':'资金经办人'})
    for p in forecast['coverage']['unresolved_payments']:
        if p['period_month']==month: issues.append({'label':'收款缺少日期','amount_yuan':p['amount_yuan'],'owner':'收款经办人','contract_id':p['contract_id']})
    linked={r[0] for r in db.query(FinanceAllocation.payment_id).filter_by(building_id=bid,voided=False).filter(FinanceAllocation.payment_id.is_not(None))}
    for source in sources:
        if not source['key'].startswith('contract:'): continue
        for payment in source['evidence'].get('payments',[]):
            if ((payment.get('payment_date') or '')[:7]==month and payment.get('allocation_type','收款')=='收款'
                and payment.get('source') not in {'finance_cash','finance_reverse'} and payment.get('id') not in linked and payment.get('amount_yuan',0)>0):
                issues.append({'label':'历史租金实收尚未关联资金和银行核对','amount_yuan':payment['amount_yuan'],'owner':'收款经办人','contract_id':int(source['key'].split(':')[1])})
    expenses=all_expenses(db,bid)
    for e in expenses:
        if (e.get('paid_on') or '')[:7]==month and not e.get('payments') and e['amount_yuan']:
            issues.append({'label':'旧费用整笔付款尚未关联资金核对：'+e['name'],'amount_yuan':e['amount_yuan'],'owner':e['owner'] or '费用经办人','expense_id':e['id']})
        detail=e.get('detail')
        if (detail and detail['service_from']<=month<=detail['service_to']) or e['occurrence_month']==month:
            if not detail or not detail['supplier'] or not detail['evidence']: issues.append({'label':f'费用缺供应商或凭据：{e["name"]}','amount_yuan':e['amount_yuan'],'owner':e['owner'] or '费用经办人','expense_id':e['id']})
    facts={'month':month,'metrics':metrics,'sources':sources,'cash':[serialize(c) for c in cash],'banks':[serialize(b) for b in banks],
           'allocations':[serialize(a) for a in db.query(FinanceAllocation).filter_by(building_id=bid).filter(FinanceAllocation.cash_id.in_([c.id for c in cash]))],
           'matches':[serialize(m) for m in db.query(FinanceBankMatch).filter_by(building_id=bid).filter(or_(FinanceBankMatch.bank_id.in_([b.id for b in banks]),FinanceBankMatch.cash_id.in_([c.id for c in cash])))],
           'issues':issues,'income_basis':'合同管理口径，金额单位万元；并非总账或法定报表'}
    return facts


def period_view(db,bid,month):
    facts=period_facts(db,bid,month)
    row=db.query(FinancePeriod).filter_by(building_id=bid,period_month=month).one_or_none()
    item=serialize(row) if row else {'status':'open','revision':1,'submitted_by':'','reviewed_by':''}
    item['confirmations']=json.loads(row.confirmations_json) if row else {}
    item['stale']=bool(row and row.source_digest and row.source_digest!=digest(facts))
    item['events']=[serialize(e) for e in db.query(FinancePeriodEvent).filter_by(period_id=row.id).order_by(FinancePeriodEvent.id.desc())] if row else []
    item['facts']=json.loads(row.snapshot_json) if row and row.status=='closed' else facts
    item['live_issues']=facts['issues']
    return item


def period_action(db,bid,user,month,payload):
    row=db.query(FinancePeriod).filter_by(building_id=bid,period_month=month).one_or_none()
    if row is None:
        if payload.expected_revision!=1: raise HTTPException(409,'月份状态已变化')
        row=FinancePeriod(building_id=bid,period_month=month);db.add(row);db.flush()
    reserve(db,row,payload.expected_revision)
    action=payload.action
    if action in {'close','reopen','return'} and user.role!='admin': raise HTTPException(403,'复核、退回和重开须由项目可编辑的管理员执行')
    if action in {'close','return'} and user.username==row.submitted_by: raise HTTPException(422,'须由另一人独立复核')
    allowed={'save':['open'],'submit':['open'],'close':['submitted'],'return':['submitted'],'reopen':['closed']}
    if row.status not in allowed[action]: raise HTTPException(409,'当前月度状态不允许此操作')
    facts=period_facts(db,bid,month);hashed=digest(facts)
    if action in {'save','submit'}: row.confirmations_json=canonical_json(payload.confirmations)
    if action=='submit':
        metrics=facts['metrics']
        amounts={'income':metrics['revenue'],'receipts':max(metrics['actual_cash_received'],sum(c['amount'] for c in facts['cash'] if c['direction']=='in')/10000),'expenses':metrics['opex']+metrics['capex'],'payments':max(metrics['actual_opex_paid']+metrics['actual_capex_paid'],sum(c['amount'] for c in facts['cash'] if c['direction']=='out')/10000)}
        for key,value in amounts.items():
            state=payload.confirmations.get(key,'missing')
            if state=='missing' or (state=='zero' and abs(value)>0.0000001): raise HTTPException(422,'四类资料须逐项确认；存在金额的类别不能确认为零')
        if facts['issues']: raise HTTPException(422,'请先处理本月核对差异，再提交复核')
        row.status='submitted';row.submitted_by=user.username;row.reviewed_by='';row.source_digest=hashed;row.snapshot_json=canonical_json(facts)
    if action=='close':
        if row.source_digest!=hashed: raise HTTPException(409,'提交后的来源数据已变化，请退回重新核对提交')
        row.status='closed';row.reviewed_by=user.username
    if action in {'return','reopen'}:
        row.status='open';row.source_digest='';row.reviewed_by=user.username
    row.updated_at=now_in_timezone()
    db.add(FinancePeriodEvent(period_id=row.id,revision=row.revision,action=action,actor=user.username,reason=payload.reason,snapshot_json=canonical_json({'facts':facts,'confirmations':json.loads(row.confirmations_json),'status':row.status})))
    return finish(db,bid,user,row,'finance_period_'+action)


def outcomes(db,bid,month,page=1):
    from app.services.financial_planning_store import get_version
    from app.services.execution_actions import action_dict
    from app.services.financial_planning import build_financial_forecast
    year=int(month[:4]); through=int(month[5:])
    q=db.query(ExecutionAction).join(FinancialPlanVersion,ExecutionAction.version_id==FinancialPlanVersion.id).filter(ExecutionAction.building_id==bid,ExecutionAction.kind=='measure',FinancialPlanVersion.year==year)
    rows=q.order_by(ExecutionAction.id.desc()).offset((page-1)*25).limit(25).all()
    report=build_financial_forecast(db,building_id=bid,year=year,closed_through_month=through,expenses=all_expenses(db,bid)) if rows else {'sources':[]}
    items=[]
    for row in rows:
        source=json.loads(row.source_json)
        if source.get('year')!=year: continue
        measure=source['measure'];start=int(measure.get('startMonth',1));proof=json.loads(row.benefit_json)
        cid=proof.get('contract_id') or row.result_contract_id;eid=proof.get('expense_id')
        observed=next((s for s in report['sources'] if (cid and s.get('contract_id')==cid) or (eid and s.get('expense_id')==eid)),None)
        baseline=json.loads(get_version(db,row.version_id,bid).snapshot_json)
        baseline_source=next((s for s in baseline['sources'] if eid and s.get('expense_id')==eid),None)
        months=[]
        for m in range(1,through+1):
            expected=0
            if row.measure_key=='newLease':
                term=int(measure.get('termMonths',0));free=int(measure.get('freeMonths',0))
                if start<=m<start+term and term: expected=float(measure.get('monthlyRent',0))*max(term-free,0)/term
            elif m>=start:
                expected=float(measure.get('monthlySaving',0))-(float(measure.get('implementationCost',0)) if m==start else 0)
            actual=next((r for r in (observed or {}).get('months',[]) if r['month']==m),{})
            before=next((r for r in (baseline_source or {}).get('months',[]) if r['month']==m),{})
            comparable=observed is not None and (row.measure_key=='newLease' or baseline_source is not None)
            value=actual.get('rent_income',0) if row.measure_key=='newLease' else before.get('operating_cost',0)-actual.get('operating_cost',0)
            months.append({'month':m,'expected_yuan':round(expected*10000,2),'observed_yuan':round(value*10000,2) if comparable else None,'delta_yuan':round((value-expected)*10000,2) if comparable else None,'cash_yuan':round(actual.get('actual_cash_received',0)*10000,2),'cost_yuan':round(actual.get('operating_cost',0)*10000,2),'paid_yuan':round((actual.get('actual_opex_paid',0)+actual.get('actual_capex_paid',0))*10000,2)})
        items.append({'action':action_dict(db,row),'months':months,'contract_id':cid,'expense_id':eid,'baseline_version_id':row.version_id,'comparison_available':all(m['observed_yuan'] is not None for m in months),'basis':'固定方案的预期与关联业务记录比较；差额不等于措施因果收益。降本仅比较同一费用在固定基准中的发生额，未关联基准时不推定收益。'})
    return {'items':items,'total':q.count(),'page':page,'page_size':25}


def create_expense(db,bid,user,payload):
    key=f'{bid}:{payload.entry_key}'
    hashed=digest(payload.model_dump(mode='json'))
    prior=db.query(FinanceExpenseDetail).filter_by(creation_key=key).one_or_none()
    if prior:
        if prior.creation_hash!=hashed: raise HTTPException(409,'此操作编号已用于其他费用内容')
        return serialize(scoped(db,FinancialExpense,bid,prior.expense_id))
    values=payload.expense.model_dump(exclude={'expected_revision'})
    if values['paid_on']: raise HTTPException(422,'请先录入费用，再通过资金分配登记付款')
    expense=FinancialExpense(building_id=bid,created_by=user.username,updated_by=user.username,**values)
    db.add(expense);db.flush()
    detail=FinanceExpenseDetail(expense_id=expense.id,creation_key=key,creation_hash=hashed,**payload.detail.model_dump(exclude={'expected_revision'}));db.add(detail)
    return finish(db,bid,user,expense,'finance_expense_create')
