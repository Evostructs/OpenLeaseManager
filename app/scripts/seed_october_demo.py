"""Append simulated operating receipts through 2026-10-31; never reset data.

All amounts come from current contract statements. Stable operation keys and
the finance service's replay checks make retries safe, including partial runs.
Future October transactions are explicitly labelled simulated scenarios.
"""
import argparse
import calendar
from dataclasses import replace
from decimal import Decimal
from datetime import date
import json
from types import SimpleNamespace

from app.database import SessionLocal
from app.models import (Building, Contract, ExecutionAction, FinancialExpense,
                        FinancialPlanVersion, ReceivablePayment, ReceivableRecord)
from app.finance_models import (FinanceCash, FinanceAllocation, FinanceBankLine,
                               FinanceBankMatch, FinanceExpenseDetail, FinancePeriod)
from app.finance_schemas import (CashPayload, AllocationPayload, BankLinePayload,
                                BankImportPayload, MatchPayload, ExpenseCreatePayload)
from app.financial_planning_schemas import SaveVersionRequest
from app.execution_schemas import CreateActionPayload
from app.services import finance_operations as finance, financial_planning_store as planning
from app.services import execution_actions as actions
from app.services.contract_receivables import get_monthly_receivables

TAG = 'DEMO-OCTOBER-20261031'
ACTOR = SimpleNamespace(username='[模拟]10月数据生成器', role='admin')
NOTE = '[模拟]截至2026-10-31的演示情景；未来日期非真实到账；' + TAG
MONTHS = tuple(f'2026-{m:02d}' for m in range(6, 11))
MODELS = (FinanceCash, FinanceAllocation, FinanceBankLine, FinanceBankMatch,
          FinancialExpense, FinancialPlanVersion, ReceivablePayment,
          ReceivableRecord, ExecutionAction)


def portfolio_profiles(base_profiles, as_of_date):
    """Independent labelled demo trajectories; never change the old generator defaults."""
    result = dict(base_profiles)
    if as_of_date.year != 2026 or as_of_date.month not in (9, 10):
        return result
    step = as_of_date.month - 8
    for code, (delta, arrears, collection) in {
        'DEMO_B': (600, '0.80', '1.015'), 'DEMO_C': (300, '0.90', '1.008'),
        'DEMO_D': (0, '1.00', '1.000'), 'DEMO_E': (-400, '1.15', '0.985'),
    }.items():
        p = base_profiles[code]
        leased = p.leased_sqm + Decimal(delta * step)
        receivable = p.receivable_month * leased / p.leased_sqm
        progress = Decimal(as_of_date.day) / Decimal(calendar.monthrange(as_of_date.year, as_of_date.month)[1])
        received = min(receivable, p.received_month * leased / p.leased_sqm * Decimal(collection) ** step) * progress
        result[code] = replace(p, leased_sqm=leased, receivable_month=receivable,
                               received_month=received, arrears_balance=p.arrears_balance * Decimal(arrears) ** step)
    return result


def counts(db):
    return {m.__tablename__: db.query(m).count() for m in MODELS}


def receipt_plan(db, bid):
    contracts = db.query(Contract).filter_by(building_id=bid, is_deleted=False).order_by(Contract.contract_no, Contract.id).all()
    # Reject a mixed/real dataset before any write, rather than guessing by name.
    if not contracts or any('模拟' not in ((c.notes or '') + (c.tenant_name or '')) for c in contracts):
        raise ValueError('只允许已有合同全部明确标注为模拟的 DEMO_A 数据集')
    closed = db.query(FinancePeriod).filter_by(building_id=bid, status='closed').filter(FinancePeriod.period_month.in_(MONTHS)).first()
    if closed:
        raise ValueError('模拟补充范围包含已关闭期间，请先人工核对；不自动重开')
    ids = [c.id for c in contracts]
    batch_payments = {}
    for p in db.query(ReceivablePayment).filter(ReceivablePayment.contract_id.in_(ids), ReceivablePayment.reference_no.like(TAG + '-%')):
        batch_payments[(p.contract_id, p.period_month)] = batch_payments.get((p.contract_id, p.period_month), 0) + finance.fen(p.amount)
    fee_used = {}
    for a in db.query(FinanceAllocation).filter_by(building_id=bid, kind='property_fee', voided=False).filter(~FinanceAllocation.entry_key.like(TAG + '-%')):
        fee_used[(a.contract_id, a.period_month)] = fee_used.get((a.contract_id, a.period_month), 0) + finance.fen(a.amount)
    rows = []
    for month in MONTHS:
        report = get_monthly_receivables(db, month, contract_ids=ids)
        eligible = sorted((r for r in report['rows'] if finance.fen(r['rent']) > 0), key=lambda r: (r['contract_no'], r['contract_id']))
        for index, r in enumerate(eligible):
            # Four September debtors; October retains unpaid and partial cases.
            ratio = 100
            if month == '2026-09' and index < 4:
                ratio = 0 if index < 2 else 50
            if month == '2026-10':
                ratio = 0 if index % 10 == 0 else 50 if index % 10 == 1 else 100
            cid = r['contract_id']
            previous = finance.fen(r['settlement_amount']) - batch_payments.get((cid, month), 0)
            rent = max(0, finance.fen(r['rent']) * ratio // 100 - previous)
            fee = max(0, finance.fen(r['property_fee']) * ratio // 100 - fee_used.get((cid, month), 0))
            if not rent and not fee:
                continue
            day = date(2026, int(month[5:]), min(28, 5 + index % 24))
            rows.append({'key': f'{month}-{cid}', 'cid': cid, 'month': month,
                         'day': day, 'rent': rent, 'fee': fee,
                         'party': '[模拟]' + r['tenant_name'][:130]})
    return contracts, rows


def post_cash(db, bid, key, amount_fen, day, party, direction='in', allocations=(), match=True):
    if amount_fen <= 0:
        return
    payload = CashPayload(entry_key=TAG+'-'+key, direction=direction,
                          occurred_on=day, amount=amount_fen / 100,
                          counterparty=party, reference=TAG+'-'+key, note=NOTE)
    value = finance.create_cash(db, bid, ACTOR, payload)
    cash = db.get(FinanceCash, value['id'])
    for i, allocation in enumerate(allocations):
        finance.allocation(db, bid, ACTOR, cash.id, AllocationPayload(
            entry_key=TAG+f'-alloc-{key}-{i}', expected_revision=cash.revision,
            note=NOTE, **allocation))
        db.refresh(cash)
    if match:
        bank = finance.import_bank(db, bid, ACTOR, BankImportPayload(rows=[BankLinePayload(
            import_key=TAG+'-bank-'+key, occurred_on=day, direction=direction,
            amount=amount_fen / 100, counterparty=party, reference=cash.reference,
            account_label='[模拟]10月演示结算账户')]))['items'][0]
        finance.match_bank(db, bid, ACTOR, bank['id'], MatchPayload(
            entry_key=TAG+'-match-'+key, expected_revision=bank['revision'],
            cash_revision=cash.revision, cash_id=cash.id, amount=amount_fen / 100, note=NOTE))


def run(db, apply=False):
    bid = db.query(Building).filter_by(code='DEMO_A').one().id
    before = counts(db)
    contracts, receipts = receipt_plan(db, bid)
    result = {'batch': TAG, 'classification': 'simulated', 'through': '2026-10-31',
              'mode': 'apply' if apply else 'dry-run', 'before': before,
              'receipt_operations': len(receipts), 'periods_closed': 0,
              'scope': 'DEMO_A', 'preserves_active_plans': True}
    # Check replay conflicts for every planned receipt before committing any.
    for r in receipts:
        finance.replay(db, FinanceCash, bid, TAG+'-'+r['key'], CashPayload(
            entry_key=TAG+'-'+r['key'], direction='in', occurred_on=r['day'],
            amount=(r['rent']+r['fee'])/100, counterparty=r['party'],
            reference=TAG+'-'+r['key'], note=NOTE))
    if not apply:
        result['planned'] = '补齐6—10月合同租金/物业费与银行核对；保留少量欠款；补付8—9月运维和季度检修，10月分期付款、新增费用及待核对差异；另存10月末预测，不采用、不关账。'
        return result
    for r in receipts:
        allocations = [{'kind': kind, 'amount': r[field]/100,
                        'period_month': r['month'], 'contract_id': r['cid']}
                       for kind, field in (('rent', 'rent'), ('property_fee', 'fee')) if r[field]]
        post_cash(db, bid, r['key'], r['rent']+r['fee'], r['day'], r['party'], allocations=allocations)
    # Continue the September finance demo without rerunning or editing it.
    old_tag = 'DEMO-FINANCE-20260928'
    for month, day in ((8, 8), (9, 12), (10, 20)):
        detail = db.query(FinanceExpenseDetail).filter_by(creation_key=f'{bid}:'+old_tag+f'-expense-{month}').one_or_none()
        if not detail:
            continue
        expense = db.get(FinancialExpense, detail.expense_id)
        existing = db.query(FinanceCash).filter_by(building_id=bid, entry_key=TAG+f'-opex-{month}').one_or_none()
        amount = finance.fen(existing.amount) if existing else max(0, finance.fen(expense.amount_yuan)-finance.expense_paid(db, expense.id))
        if month == 10 and not existing:
            amount //= 2
        post_cash(db, bid, f'opex-{month}', amount, date(2026, 10, day), '[模拟]物业运维供应商', 'out',
                  [{'kind': 'expense', 'amount': amount/100, 'period_month': expense.occurrence_month, 'expense_id': expense.id}])
    detail = db.query(FinanceExpenseDetail).filter_by(creation_key=f'{bid}:'+old_tag+'-quarterly-expense').one_or_none()
    if detail:
        expense = db.get(FinancialExpense, detail.expense_id)
        existing = db.query(FinanceCash).filter_by(building_id=bid, entry_key=TAG+'-quarterly-payment').one_or_none()
        amount = finance.fen(existing.amount) if existing else max(0, finance.fen(expense.amount_yuan)-finance.expense_paid(db, expense.id))
        post_cash(db, bid, 'quarterly-payment', amount, date(2026, 10, 16), '[模拟]检修供应商', 'out',
                  [{'kind': 'expense', 'amount': amount/100, 'period_month': expense.occurrence_month, 'expense_id': expense.id}])
    for key, name, amount, category, day, fraction in (
        ('energy', '10月能源与公共区域运维', 128000, 'opex', 22, 100),
        ('leasing', '10月招商推广服务', 36000, 'opex', 25, 50),
        ('renovation', '10月公共区域改造', 180000, 'capex', 28, 30),
    ):
        expense = finance.create_expense(db, bid, ACTOR, ExpenseCreatePayload(
            entry_key=TAG+'-expense-'+key,
            expense={'name': '[模拟]'+name, 'category': category, 'amount_yuan': amount,
                     'occurrence_month': '2026-10', 'payment_month': '2026-10',
                     'owner': '[模拟]10月运营经办', 'notes': NOTE},
            detail={'expected_revision': 1, 'supplier': '[模拟]'+name+'供应商',
                    'cost_category': name, 'cost_center': '[模拟]DEMO_A运营',
                    'service_from': '2026-10', 'service_to': '2026-10',
                    'evidence': TAG+'-'+key+'-模拟凭据'}))
        paid = amount * fraction
        post_cash(db, bid, 'pay-'+key, paid, date(2026, 10, day), '[模拟]'+name+'供应商', 'out',
                  [{'kind': 'expense', 'amount': paid/100, 'period_month': '2026-10', 'expense_id': expense['id']}])
    post_cash(db, bid, 'advance', 800000, date(2026, 10, 29), '[模拟]待确认预收客户')
    finance.import_bank(db, bid, ACTOR, BankImportPayload(rows=[BankLinePayload(
        import_key=TAG+'-unmatched', occurred_on=date(2026, 10, 30), direction='out',
        amount=680, counterparty='[模拟]待确认供应商', reference=TAG+'-待查支出',
        account_label='[模拟]10月演示结算账户')]))
    # Never overwrite a user's selected forecast; the labelled version is optional.
    name = '[模拟]10月末经营预测 '+TAG
    if not db.query(FinancialPlanVersion).filter_by(building_id=bid, name=name).first():
        state = planning.planning_state(db, bid, 2026)
        planning.save_version(db, bid, ACTOR, SaveVersionRequest(
            year=2026, closed_through_month=10, kind='forecast', name=name,
            expected_revision=state.revision if state else 0,
            client_state={'simulation': {'through': '2026-10-31', 'batch': TAG,
                                        'note': NOTE, 'periods_actually_closed': False}}), adopt=False)
    expiring = [c for c in contracts if c.end_date and date(2026, 10, 1) <= c.end_date <= date(2026, 12, 31) and c.disposal != '已退租'][:6]
    for i, contract in enumerate(expiring):
        actions.create_action(db, bid, ACTOR, CreateActionPayload(
            kind='expiry', contract_id=contract.id, expected_revision=0,
            owner='[模拟]10月租赁负责人', due_on=date(2026, 10, 31),
            next_follow_up_on=date(2026, 10, 20+i), status='waiting' if i%2 else 'open',
            decision='renew' if i%2 else 'undecided', next_action=NOTE+'；联系客户确认续租条件，合同动作尚未完成'))
    result['after'] = counts(db)
    result['changed'] = {k: result['after'][k]-before[k] for k in before}
    report = get_monthly_receivables(db, '2026-10', contract_ids=[c.id for c in contracts])
    result['october'] = {k: report[k] for k in ('count', 'total_rent', 'total_received', 'total_balance', 'received_rate')}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true')
    group.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with SessionLocal() as db:
        print(json.dumps(run(db, args.apply), ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
