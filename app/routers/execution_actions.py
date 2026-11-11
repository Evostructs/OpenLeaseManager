from typing import Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.core.access import get_current_user
from app.database import get_db
from app.execution_schemas import ActionPayload, CreateActionPayload, RevisionPayload, BenefitPayload
from app.models import Contract, CustomerFollowUp, FinancialExpense, ExecutionAction, User
from app.core.time import today_in_timezone
from app.services import execution_actions as store
from app.services.detail_operations_access import detail_scope_dependency
from app.services.property_scope import SelectedPropertyScope, require_single_selected_building, resolve_user_scope

router=APIRouter(prefix='/api/execution-actions',tags=['execution-actions'],dependencies=[Depends(get_current_user)])
view_scope=detail_scope_dependency('contracts',for_write=False)
edit_scope=detail_scope_dependency('contracts',for_write=True)

@router.get('')
def listing(contract_id: int | None=Query(default=None,gt=0), version_id: int | None=Query(default=None,gt=0),
            filter: Literal['all','mine','overdue','waiting','follow-up','done']='all',page: int=Query(default=1,ge=1),
            scope: SelectedPropertyScope=Depends(view_scope),user: User=Depends(get_current_user),db: Session=Depends(get_db)):
    query=db.query(ExecutionAction).filter(ExecutionAction.building_id.in_(scope.building_ids))
    live=db.query(Contract.id).filter(Contract.is_deleted.is_(False))
    query=query.filter(or_(ExecutionAction.contract_id.is_(None),ExecutionAction.contract_id.in_(live)))
    if contract_id: query=query.filter(ExecutionAction.contract_id==contract_id)
    if version_id: query=query.filter(ExecutionAction.version_id==version_id)
    today=today_in_timezone()
    if filter=='mine': query=query.filter(ExecutionAction.owner==user.username,ExecutionAction.status!='done')
    elif filter=='overdue': query=query.filter(ExecutionAction.due_on<today,ExecutionAction.status!='done')
    elif filter=='waiting': query=query.filter(ExecutionAction.status=='waiting')
    elif filter=='follow-up': query=query.filter(ExecutionAction.next_follow_up_on<=today,ExecutionAction.status!='done')
    elif filter=='done': query=query.filter(ExecutionAction.status=='done')
    total=query.count()
    rows=query.order_by(ExecutionAction.status=='done',ExecutionAction.due_on,ExecutionAction.id).offset((page-1)*25).limit(25).all()
    can_write=scope.scope_type=='building' and scope.building_ids[0] in resolve_user_scope(db,user).edit_building_ids
    return {'items':[store.action_dict(db,row) for row in rows],'total':total,'page':page,'page_size':25,'can_write':can_write,'current_user':user.username}

@router.post('')
def create(payload: CreateActionPayload,scope: SelectedPropertyScope=Depends(edit_scope),user: User=Depends(get_current_user),db: Session=Depends(get_db)):
    return store.create_action(db,require_single_selected_building(scope),user,payload)

@router.get('/options')
def options(contract_id: int | None=Query(default=None,gt=0), version_id: int | None=Query(default=None,gt=0), keyword: str=Query(default='',max_length=100),
            scope: SelectedPropertyScope=Depends(view_scope),db: Session=Depends(get_db)):
    contracts=db.query(Contract).filter(Contract.building_id.in_(scope.building_ids),Contract.is_deleted.is_(False))
    follow_ups=[];expenses=[]
    if contract_id:
        original=contracts.filter(Contract.id==contract_id).one_or_none()
        if original is None:
            from fastapi import HTTPException
            raise HTTPException(404,'合同不存在或无权访问')
        contracts=contracts.filter(Contract.prev_contract_no==original.contract_no)
        follow_ups=[{'id':row.id,'label':f'{row.occurred_at.date()} · {row.owner} · {row.content[:80]}'} for row in db.query(CustomerFollowUp).filter_by(contract_id=original.id,building_id=original.building_id).order_by(CustomerFollowUp.occurred_at.desc()).limit(100)]
    if keyword:
        contracts=contracts.filter(or_(Contract.contract_no.contains(keyword,autoescape=True),Contract.tenant_name.contains(keyword,autoescape=True),Contract.room_no.contains(keyword,autoescape=True)))
    if version_id:
        from app.services.financial_planning_store import get_version
        bid=require_single_selected_building(scope)
        version=get_version(db,version_id,bid)
        expenses=[{'id':row.id,'label':f'{row.name} · {row.occurrence_month} · {row.amount_yuan} 元'} for row in db.query(FinancialExpense).filter_by(building_id=bid,is_active=True).filter(FinancialExpense.occurrence_month.startswith(str(version.year))).order_by(FinancialExpense.occurrence_month,FinancialExpense.id).limit(200)]
    return {'contracts':[{'id':row.id,'label':f'{row.contract_no} · {row.tenant_name} · {row.room_no}'} for row in contracts.order_by(Contract.id.desc()).limit(30)],'follow_ups':follow_ups,'expenses':expenses}

@router.get('/{action_id}')
def detail(action_id:int,scope: SelectedPropertyScope=Depends(view_scope),db: Session=Depends(get_db)):
    return store.action_dict(db,store.scoped_action(db,action_id,scope.building_ids))

@router.put('/{action_id}')
def update(action_id:int,payload:ActionPayload,scope: SelectedPropertyScope=Depends(edit_scope),user: User=Depends(get_current_user),db: Session=Depends(get_db)):
    return store.update_action(db,store.scoped_action(db,action_id,scope.building_ids),user,payload)

@router.post('/{action_id}/verify')
def verify(action_id:int,payload:RevisionPayload,scope: SelectedPropertyScope=Depends(edit_scope),user: User=Depends(get_current_user),db: Session=Depends(get_db)):
    return store.verify_expiry(db,store.scoped_action(db,action_id,scope.building_ids),user,payload.expected_revision)

@router.post('/{action_id}/benefit')
def benefit(action_id:int,payload:BenefitPayload,scope: SelectedPropertyScope=Depends(edit_scope),user: User=Depends(get_current_user),db: Session=Depends(get_db)):
    return store.verify_benefit(db,store.scoped_action(db,action_id,scope.building_ids),user,payload)
