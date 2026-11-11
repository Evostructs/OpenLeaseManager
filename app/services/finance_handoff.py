"""Scoped, reviewable finance handoff; never posts accounting vouchers."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from decimal import Decimal, ROUND_HALF_UP

import openpyxl
from fastapi import HTTPException
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill
from sqlalchemy.exc import OperationalError

from app.core.excel_safety import sanitize_workbook
from app.core.workbook_localization import localize_workbook
from app.core.time import now_in_timezone
from app.finance_models import FinancePeriod, FinancePeriodEvent
from app.models import Building
from app.services import finance_operations as store
from app.services.finance_mapping import read_profile, handoff_mapping, ACCOUNTS
from app.core.handoff_integrity import MAX_BYTES
from app.services.financial_planning_store import _log, canonical_json

SCHEMA = 'FINANCE_HANDOFF_V1'
KIND_LABELS = {'rent': '租金', 'property_fee': '物业费', 'deposit': '押金', 'expense': '费用'}
METRICS = {'revenue': '合同管理收入', 'operating_cost': '经营费用', 'actual_cash_received': '租金与已录物业费实收',
           'actual_opex_paid': '经营费用实付', 'actual_capex_paid': '资本支出实付'}


def snapshot_transaction(db):
    # Python sqlite3 legacy transaction mode does not BEGIN on a SELECT.
    # Explicitly hold one read snapshot across all source queries.
    connection = db.connection()
    raw = connection.connection.driver_connection
    if isinstance(raw, sqlite3.Connection) and not raw.in_transaction:
        connection.exec_driver_sql('BEGIN')


def _context(db, bid, month, mode):
    snapshot_transaction(db)
    building = db.get(Building, bid)
    row = db.query(FinancePeriod).filter_by(building_id=bid, period_month=month).one_or_none()
    events = db.query(FinancePeriodEvent).filter_by(period_id=row.id).order_by(FinancePeriodEvent.id).all() if row else []
    if mode == 'closed':
        if not row or row.status != 'closed':
            raise HTTPException(409, '本月尚未独立复核关账，请先导出核对草稿。')
        try:
            facts = json.loads(row.snapshot_json)
            close = next(e for e in reversed(events) if e.action == 'close' and e.revision == row.revision)
            proof = json.loads(close.snapshot_json)
            confirmations = json.loads(row.confirmations_json)
            valid = (facts['month'] == month and store.digest(facts) == row.source_digest
                     and proof['status'] == 'closed' and proof['facts'] == facts
                     and proof['confirmations'] == confirmations
                     and row.submitted_by and row.reviewed_by and row.submitted_by != row.reviewed_by
                     and close.actor == row.reviewed_by)
            if not valid:
                raise ValueError('invalid closing proof')
        except (ValueError, KeyError, TypeError, StopIteration):
            raise HTTPException(409, '关账快照与复核记录不一致，请核查后再交接。') from None
        confirmations = json.loads(row.confirmations_json)
    else:
        facts = store.period_facts(db, bid, month)
        confirmations = json.loads(row.confirmations_json) if row else {}
    return {'building': {'code': building.code, 'name': building.name}, 'month': month, 'mode': mode, 'period_id': row.id if row else None,
            'revision': row.revision if row else 1, 'status': row.status if row else 'open',
            'submitted_by': row.submitted_by if row else '', 'reviewed_by': row.reviewed_by if row else '',
            'confirmations': confirmations, 'facts': facts, 'customer_mapping': handoff_mapping(read_profile(db, bid), facts), 'events': [
                {k: store.serialize(e)[k] for k in ('revision', 'action', 'actor', 'reason', 'created_at')} for e in events]}


def _summary(context):
    facts = context['facts']
    cash = facts.get('cash', [])
    total = lambda direction: sum(store.fen(c['amount']) for c in cash if c['direction'] == direction and not c.get('voided'))
    return {'schema': SCHEMA, 'building': context['building'], 'month': context['month'], 'mode': context['mode'],
            'revision': context['revision'], 'source_digest': store.digest(facts), 'handoff_digest': store.digest(context), 'customer_mapping': context['customer_mapping'], 'status': context['status'],
            'submitted_by': context['submitted_by'], 'reviewed_by': context['reviewed_by'],
            'issue_count': len(facts.get('issues', [])), 'confirmations': context['confirmations'],
            'counts': {k: len(facts.get(k, [])) for k in ('sources', 'cash', 'allocations', 'banks', 'matches')},
            'cash_in_fen': total('in'), 'cash_out_fen': total('out'),
            'basis': '合同管理口径；资金按实际日期，押金单列；不作为总账凭证或税务申报。'}


def preview(db, bid, month, mode):
    return _summary(_context(db, bid, month, mode))


def _amount(value, wan=False):
    cents = int((Decimal(str(value or 0)) * (1000000 if wan else 100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return f'{Decimal(cents) / 100:.2f}'


def _tables(context):
    facts = context['facts']
    profile = context['customer_mapping']
    return {
        '财务映射': (['项目', '配置'], [[label, profile[key]] for key, label in {'company_name':'法人名称','company_code':'财务组织编码','erp_system':'财务系统','book_code':'账套编码','tax_basis':'金额含税口径','confirmed_by':'确认人'}.items()] +
            [[label+'科目', profile['accounts'].get(key, '')] for key, label in ACCOUNTS.items()] +
            [['银行：'+label, code] for label, code in profile['bank_accounts'].items()] + [['映射修订', profile['revision']], ['状态', '已确认' if profile['ready'] else '待配置或确认']]),
        '管理口径': (['指标', '金额（元）'], [[label, _amount(facts['metrics'].get(key), wan=True)] for key, label in METRICS.items()]),
        '资金流水': (['资金编号', '日期', '方向', '金额（元）', '往来方', '摘要', '备注', '登记人'], [
            [c['id'], c['occurred_on'], '收入' if c['direction'] == 'in' else '支出', _amount(c['amount']), c['counterparty'], c.get('reference', ''), c.get('note', ''), c.get('created_by', '')] for c in facts.get('cash', [])]),
        '分配明细': (['分配编号', '资金编号', '用途', '所属账期', '金额（元）', '合同编号ID', '费用编号ID', '押金编号ID', '收款编号ID', '撤销反向收款ID', '状态', '说明'], [
            [a['id'], a['cash_id'], KIND_LABELS[a['kind']], a['period_month'], _amount(a['amount']), a.get('contract_id'), a.get('expense_id'), a.get('deposit_id'), a.get('payment_id'), a.get('reversal_payment_id'), '已撤销' if a.get('voided') else '有效', a.get('note', '')] for a in facts.get('allocations', [])]),
        '银行流水': (['银行编号', '导入编号', '日期', '方向', '金额（元）', '往来方', '摘要', '账户简称'], [
            [b['id'], b['import_key'], b['occurred_on'], '收入' if b['direction'] == 'in' else '支出', _amount(b['amount']), b['counterparty'], b.get('reference', ''), b.get('account_label', '')] for b in facts.get('banks', [])]),
        '银行匹配': (['匹配编号', '银行编号', '资金编号', '金额（元）', '状态', '人工依据'], [
            [m['id'], m['bank_id'], m['cash_id'], _amount(m['amount']), '已撤销' if m.get('voided') else '有效', m.get('note', '')] for m in facts.get('matches', [])]),
        '来源明细': (['来源编号', '名称', '管理收入（元）', '经营费用（元）', '租金物业费实收（元）', '经营费用实付（元）', '资本支出实付（元）', '合同编号', '负责人', '凭据依据'], [
            [s['key'], s['label'], *[_amount(s.get('values', {}).get(key), wan=True) for key in METRICS], s.get('evidence', {}).get('contract', {}).get('contract_no', ''), s.get('evidence', {}).get('owner', ''), s.get('evidence', {}).get('detail', {}).get('evidence', '') if s.get('evidence', {}).get('detail') else ''] for s in facts.get('sources', [])]),
        '待处理差异': (['差异', '金额（元）', '责任人', '合同编号ID', '费用编号ID'], [
            [i['label'], _amount(i.get('amount_yuan')), i.get('owner', ''), i.get('contract_id'), i.get('expense_id')] for i in facts.get('issues', [])]),
        '复核记录': (['修订', '动作', '操作人', '原因', '时间'], [
            [e['revision'], e['action'], e['actor'], e['reason'], e['created_at']] for e in context['events']]),
    }


def _workbook(context, summary, tables):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '交接摘要'
    for row in [
        ['财务交接', '已独立复核关账' if context['mode'] == 'closed' else '核对草稿 · 未完成财务交接'],
        ['楼宇', context['building']['name']], ['楼宇代码', context['building']['code']], ['月份', context['month']],
        ['修订', context['revision']], ['来源摘要', summary['source_digest']], ['提交人', context['submitted_by']],
        ['复核人', context['reviewed_by']], ['待处理差异', summary['issue_count']], ['金额格式', '十进制文本，精确到分；可核对 snapshot.json'],
        ['口径', summary['basis']], ['跨期链接', '银行匹配可能引用其他月份的流水编号；不得将本月未列出的编号视为缺失或重复入账。']]:
        ws.append(row)
    for title, (headers, rows) in tables.items():
        sheet = wb.create_sheet(title)
        sheet.append(headers)
        for row in rows:
            sheet.append([ILLEGAL_CHARACTERS_RE.sub('\ufffd', v) if isinstance(v, str) else v for v in row])
    for sheet in wb:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='245DA8')
        for cells in sheet.columns:
            sheet.column_dimensions[cells[0].column_letter].width = min(55, max(16, len(str(cells[0].value or '')) * 2))
    localize_workbook(wb)
    sanitize_workbook(wb)
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def export_package(db, bid, user, month, mode, expected_revision, expected_digest, *, require_mapping=False):
    try:
        context = _context(db, bid, month, mode)
        summary = _summary(context)
        if summary['revision'] != expected_revision or summary['handoff_digest'] != expected_digest:
            raise HTTPException(409, '预览后来源或月度状态已变化，请刷新交接预览后重新导出。')
        if require_mapping and (mode != 'closed' or not context['customer_mapping']['ready']):
            raise HTTPException(409, '附客户映射的交接须完成独立关账，并补齐和确认本月所用的财务及银行映射。')
        tables = _tables(context)
        files = {
            'finance-handoff.xlsx': _workbook(context, summary, tables),
            'snapshot.json': (canonical_json({'schema': SCHEMA, **context}) + '\n').encode('utf-8'),
            'tables.json': (canonical_json({'schema': SCHEMA, 'money_unit': 'CNY_yuan_decimal_string', 'customer_mapping': context['customer_mapping'], 'tables': {
                key: {'columns': columns, 'rows': rows} for key, (columns, rows) in tables.items()}}) + '\n').encode('utf-8'),
            'README.txt': ('财务交接包\n' + ('已独立复核关账。金额和依据取自保存的关账快照。' if mode == 'closed' else '核对草稿，尚未作为已复核成果交接。') +
                '\n' + summary['basis'] + '\nExcel 金额为十进制文本，避免金额位数较大时损失分位。\n'
                '客户映射及其修订随包保存；当前仅登记金额含税口径，不计算税额、不生成财务软件凭证。\n'
                '包含已撤销的分配和匹配；汇总时只使用有效记录，不把银行匹配当第二笔资金。\n'
                '跨期匹配保留双方编号；完整来源在 snapshot.json。\n'
                'manifest.json 记录各文件 SHA-256，用于传输完整性核验，不等同电子签名或真实性认证。\n').encode('utf-8'),
        }
        if sum(len(data) for data in files.values()) > MAX_BYTES:
            raise HTTPException(413, '本月交接包超过50MB，请联系管理员分批处理。')
        manifest = {**summary, 'mapping_required': require_mapping, 'generated_at': now_in_timezone().isoformat(), 'generated_by': user.username,
                    'files': {name: {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()} for name, content in files.items()}}
        files['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if sum(len(data) for data in files.values()) > MAX_BYTES:
            raise HTTPException(413, '本月交接包超过50MB，请联系管理员分批处理。')
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        content = out.getvalue()
        if len(content) > MAX_BYTES:
            raise HTTPException(413, '本月交接包超过50MB，请联系管理员分批处理。')
        _log(db, bid, user, 'finance_handoff_export', 'finance_period', context['period_id'] or 0,
             f'{month} {mode} revision={expected_revision} source={summary["source_digest"]} handoff={expected_digest} package={hashlib.sha256(content).hexdigest()}')
        db.commit()
        return content, summary
    except OperationalError as exc:
        db.rollback()
        if isinstance(exc.orig, sqlite3.OperationalError) and 'locked' in str(exc.orig).lower():
            raise HTTPException(409, '导出期间账目已被其他操作更新，请刷新交接预览后重试。') from None
        raise
