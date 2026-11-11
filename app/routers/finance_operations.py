from fastapi import APIRouter, Depends, Query, Response
from typing import Literal
from urllib.parse import quote
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.core.access import get_current_user
from app.database import get_db
from app.models import User, Contract, ContractDeposit, FinancialExpense, ReceivablePayment
from app.finance_schemas import ExpenseCreatePayload, CashPayload, AllocationPayload, BankImportPayload, MatchPayload, ReasonPayload, PeriodPayload, ExpenseDetailPayload
from app.services import finance_operations as store
from app.services.finance_mapping import MappingPayload
from app.services.detail_operations_access import detail_scope_dependency
from app.services.property_scope import SelectedPropertyScope, require_single_selected_building, resolve_user_scope

router=APIRouter(prefix='/api/finance-operations',tags=['finance-operations'],dependencies=[Depends(get_current_user)])
view=detail_scope_dependency('analytics',for_write=False)
edit=detail_scope_dependency('analytics',for_write=True)
MONTH=Query(pattern=r'^(20\d{2}|2100)-(0[1-9]|1[0-2])$')

@router.get('/handoff/mapping')
def mapping_get(scope:SelectedPropertyScope=Depends(view),db:Session=Depends(get_db)):
    from app.services.finance_mapping import read_profile
    return read_profile(db,require_single_selected_building(scope))

@router.put('/handoff/mapping')
def mapping_save(payload:MappingPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    from app.services.finance_mapping import save_profile
    return save_profile(db,require_single_selected_building(scope),user,payload)

@router.get('/handoff/preview')
def handoff_preview(month:str=MONTH,mode:Literal['draft','closed']='draft',scope:SelectedPropertyScope=Depends(view),db:Session=Depends(get_db)):
    from app.services.finance_handoff import preview
    return preview(db,require_single_selected_building(scope),month,mode)

@router.get('/handoff/export')
def handoff_export(month:str=MONTH,mode:Literal['draft','closed']='draft',expected_revision:int=Query(ge=1),expected_digest:str=Query(pattern=r'^[a-f0-9]{64}$'),require_mapping:bool=False,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    from app.services.finance_handoff import export_package
    content,summary=export_package(db,require_single_selected_building(scope),user,month,mode,expected_revision,expected_digest,require_mapping=require_mapping)
    name=f"财务交接_{summary['building']['code']}_{month}_{'已关账' if mode=='closed' else '核对草稿'}_r{expected_revision}.zip"
    return Response(content,media_type='application/zip',headers={'Content-Disposition':f"attachment; filename*=UTF-8''{quote(name)}",'Cache-Control':'no-store'})

@router.get('/ledger')
def ledger(month:str=MONTH,page:int=Query(default=1,ge=1),scope:SelectedPropertyScope=Depends(view),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    bid=require_single_selected_building(scope)
    return {**store.ledger(db,bid,month,page),'can_write':bid in resolve_user_scope(db,user).edit_building_ids,'can_review':user.role=='admin','current_user':user.username}

@router.get('/period')
def period(month:str=MONTH,scope:SelectedPropertyScope=Depends(view),db:Session=Depends(get_db)):
    return store.period_view(db,require_single_selected_building(scope),month)

@router.post('/period')
def period_save(payload:PeriodPayload,month:str=MONTH,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.period_action(db,require_single_selected_building(scope),user,month,payload)

@router.post('/cash')
def cash(payload:CashPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.create_cash(db,require_single_selected_building(scope),user,payload)

@router.post('/cash/{cash_id}/allocate')
def allocate(cash_id:int,payload:AllocationPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.allocation(db,require_single_selected_building(scope),user,cash_id,payload)

@router.post('/allocations/{rid}/reverse')
def reverse_allocation(rid:int,payload:ReasonPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.reverse_allocation(db,require_single_selected_building(scope),user,rid,payload)

@router.post('/cash/{rid}/void')
def void_cash(rid:int,payload:ReasonPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.void_cash(db,require_single_selected_building(scope),user,rid,payload)

@router.post('/bank/import')
def bank_import(payload:BankImportPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.import_bank(db,require_single_selected_building(scope),user,payload)

@router.post('/bank/{rid}/match')
def bank_match(rid:int,payload:MatchPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.match_bank(db,require_single_selected_building(scope),user,rid,payload)

@router.post('/matches/{rid}/reverse')
def reverse_match(rid:int,payload:ReasonPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.reverse_match(db,require_single_selected_building(scope),user,rid,payload)

@router.put('/expenses/{rid}/detail')
def expense_detail(rid:int,payload:ExpenseDetailPayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.expense_details(db,require_single_selected_building(scope),user,rid,payload)

@router.get('/options')
def options(keyword:str=Query(default='',max_length=100),contract_id:int|None=Query(default=None,gt=0),scope:SelectedPropertyScope=Depends(view),db:Session=Depends(get_db)):
    bid=require_single_selected_building(scope)
    contracts=db.query(Contract).filter_by(building_id=bid,is_deleted=False)
    if keyword: contracts=contracts.filter(or_(Contract.contract_no.contains(keyword,autoescape=True),Contract.tenant_name.contains(keyword,autoescape=True)))
    deposits=[];payments=[]
    if contract_id:
        store.scoped(db,Contract,bid,contract_id)
        deposits=[store.serialize(d) for d in db.query(ContractDeposit).filter_by(contract_id=contract_id)]
        payments=[store.serialize(p) for p in db.query(ReceivablePayment).filter_by(contract_id=contract_id,allocation_type='收款').filter(ReceivablePayment.amount>0).order_by(ReceivablePayment.id.desc()).limit(100)]
    return {'contracts':[{'id':c.id,'label':f'{c.contract_no} · {c.tenant_name}'} for c in contracts.order_by(Contract.id.desc()).limit(30)],'deposits':deposits,'payments':payments}

@router.get('/outcomes')
def outcomes(month:str=MONTH,page:int=Query(default=1,ge=1),scope:SelectedPropertyScope=Depends(view),db:Session=Depends(get_db)):
    return store.outcomes(db,require_single_selected_building(scope),month,page)

@router.post('/expenses')
def create_expense(payload:ExpenseCreatePayload,scope:SelectedPropertyScope=Depends(edit),user:User=Depends(get_current_user),db:Session=Depends(get_db)):
    return store.create_expense(db,require_single_selected_building(scope),user,payload)

@router.get('/cash/{rid}')
def cash_detail(rid:int,scope:SelectedPropertyScope=Depends(view),db:Session=Depends(get_db)):
    from app.finance_models import FinanceCash
    row=store.scoped(db,FinanceCash,require_single_selected_building(scope),rid)
    return {**store.serialize(row),'remaining':(store.fen(row.amount)-store.allocated(db,row.id))/100,'bank_remaining':(store.fen(row.amount)-store.matched(db,cash_id=row.id))/100}

@router.get('/bank/{rid}')
def bank_detail(rid:int,scope:SelectedPropertyScope=Depends(view),db:Session=Depends(get_db)):
    from app.finance_models import FinanceBankLine
    row=store.scoped(db,FinanceBankLine,require_single_selected_building(scope),rid)
    return {**store.serialize(row),'remaining':(store.fen(row.amount)-store.matched(db,bank_id=row.id))/100}

@router.get('/expenses/{rid}')
def expense_read(rid:int,scope:SelectedPropertyScope=Depends(view),db:Session=Depends(get_db)):
    row=store.scoped(db,FinancialExpense,require_single_selected_building(scope),rid)
    return store.serialize(row)
