from datetime import date
from dateutil.relativedelta import relativedelta
from app.finance_models import FinanceCash, FinanceAllocation
from app.services.financial_planning import _empty_rows, _public_rows, _yuan_fen


def expense_source(item,months,cutoff,closed,coverage):
    rows=_empty_rows(months);amount=_yuan_fen(item['amount_yuan']);category=item['category']
    detail=item.get('detail') or {};start=detail.get('service_from',item['occurrence_month']);end=detail.get('service_to',item['occurrence_month'])
    cursor=date.fromisoformat(start+'-01');periods=[]
    while cursor.strftime('%Y-%m')<=end:
        periods.append(cursor.strftime('%Y-%m'));cursor+=relativedelta(months=1)
    part,residual=divmod(amount,len(periods))
    for index,period in enumerate(periods):
        if period in rows: rows[period][category]=part+(1 if index<residual else 0)
    payments=item.get('payments',[])
    if not payments and item.get('paid_on'): payments=[{'paid_on':item['paid_on'],'amount_yuan':item['amount_yuan']}]
    paid=0
    for payment in payments:
        if payment['paid_on']>cutoff.isoformat(): continue
        value=_yuan_fen(payment['amount_yuan']);paid+=value;period=payment['paid_on'][:7]
        if period in rows: rows[period]['actual_'+category+'_paid']+=value
    remaining=amount-paid;effective=item['payment_month']
    if remaining and effective<=cutoff.strftime('%Y-%m'):
        coverage['expense_payable_yuan']+=remaining/100;effective=months[closed] if closed<12 else None
        coverage['carried_expenses'].append({'expense_id':item['id'],'amount_yuan':remaining/100,'planned_payment_month':item['payment_month'],'forecast_payment_month':effective})
    if effective in rows: rows[effective]['forecast_'+category+'_paid']+=remaining
    return {'source_key':f'expense:{item["id"]}','source_type':'expense','source_id':item['id'],'expense_id':item['id'],'label':item['name'],'months':_public_rows(rows,closed),'source_snapshot':{**item,'payment_status':'paid' if remaining==0 else 'partial' if paid else 'unpaid','effective_payment_month':effective}}


def fee_sources(db,bid,months,cutoff,closed,coverage):
    grouped={}
    for alloc,cash in db.query(FinanceAllocation,FinanceCash).join(FinanceCash,FinanceCash.id==FinanceAllocation.cash_id).filter(FinanceAllocation.building_id==bid,FinanceAllocation.kind=='property_fee',FinanceAllocation.voided.is_(False)):
        period=cash.occurred_on.strftime('%Y-%m')
        if cash.occurred_on>cutoff or period not in months: continue
        source=grouped.setdefault(alloc.contract_id,{'rows':_empty_rows(months),'payments':[]})
        source['rows'][period]['actual_cash_received']+=_yuan_fen(alloc.amount)
        source['payments'].append({'allocation_id':alloc.id,'cash_id':cash.id,'period_month':alloc.period_month,'paid_on':cash.occurred_on.isoformat(),'amount_yuan':alloc.amount})
    if grouped:
        coverage['property_fee_cash_included']=True
        coverage['property_fee_cash_basis']='包含资金分配中已核对的物业费实收；未来物业费回款尚未假定，押金不计入经营现金流'
    coverage['expense_basis']='费用按服务期间分摊；付款按逐笔实际日期；未付余额按计划月'
    return [{'source_key':f'property_fee_cash:{cid}','source_type':'property_fee_cash','source_id':cid,'contract_id':cid,'label':f'合同 #{cid} 物业费实收','months':_public_rows(item['rows'],closed),'source_snapshot':{'payments':item['payments']}} for cid,item in grouped.items()]
