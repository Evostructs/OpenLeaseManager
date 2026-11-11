from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.access import get_current_user, require_editor
from app.core.config import get_settings
from app.database import get_db
from app.models import User
from app.schemas import AiAssistResponse, AiCustomerRequest, AiRiskExplanationRequest
from app.services.ai_assist import (
    AI_TASK_CUSTOMER_SUMMARY,
    AI_TASK_FOLLOW_UP_SUGGESTION,
    AI_TASK_RISK_EXPLANATION,
    AiAssistConfigurationError,
    AiAssistProvider,
    AiAssistProviderError,
    build_customer_ai_context,
    build_customer_summary_ai_context,
    build_risk_ai_context,
    get_configured_provider,
    run_ai_assist,
)
from app.services.detail_operations_access import detail_scope_dependency
from app.services.property_scope import SelectedPropertyScope

get_selected_view_scope = detail_scope_dependency("ai-assist", for_write=False)


# AI Assist 会把业务数据发送到外部模型服务并产生调用费用，
# 因此权限收紧到 editor 及以上，viewer 不可触发。
router = APIRouter(prefix="/api/ai", tags=["ai"], dependencies=[Depends(require_editor)])


def get_ai_provider() -> AiAssistProvider:
    return get_configured_provider(get_settings())


@router.post("/customer-summary", response_model=AiAssistResponse)
def customer_summary(
    payload: AiCustomerRequest,
    user: User = Depends(get_current_user),
    provider: AiAssistProvider = Depends(get_ai_provider),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    context = build_customer_summary_ai_context(db, payload.customer_id, scope=scope)
    return _run(
        db,
        task=AI_TASK_CUSTOMER_SUMMARY,
        context=context,
        provider=provider,
        operator=user.username or "system",
        target_type="customer",
        target_id=payload.customer_id,
        scope=scope,
    )


@router.post("/follow-up-suggestion", response_model=AiAssistResponse)
def follow_up_suggestion(
    payload: AiCustomerRequest,
    user: User = Depends(get_current_user),
    provider: AiAssistProvider = Depends(get_ai_provider),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    context = build_customer_ai_context(db, payload.customer_id, scope=scope)
    return _run(
        db,
        task=AI_TASK_FOLLOW_UP_SUGGESTION,
        context=context,
        provider=provider,
        operator=user.username or "system",
        target_type="customer",
        target_id=payload.customer_id,
        scope=scope,
    )


@router.post("/risk-explanation", response_model=AiAssistResponse)
def risk_explanation(
    payload: AiRiskExplanationRequest,
    user: User = Depends(get_current_user),
    provider: AiAssistProvider = Depends(get_ai_provider),
    scope: SelectedPropertyScope = Depends(get_selected_view_scope),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else payload.dict(exclude_none=True)
    context = build_risk_ai_context(db, data, scope=scope)
    risk = context.get("risk") or {}
    target_id = risk.get("entity_id") if isinstance(risk.get("entity_id"), int) else None
    return _run(
        db,
        task=AI_TASK_RISK_EXPLANATION,
        context=context,
        provider=provider,
        operator=user.username or "system",
        target_type=risk.get("entity_type") or "risk",
        target_id=target_id,
        scope=scope,
    )


def _run(
    db: Session,
    *,
    task: str,
    context: dict,
    provider: AiAssistProvider,
    operator: str,
    target_type: str,
    target_id: int | None,
    scope: SelectedPropertyScope,
) -> dict:
    try:
        return run_ai_assist(
            db,
            task=task,
            context=context,
            provider=provider,
            operator=operator,
            target_type=target_type,
            target_id=target_id,
            scope=scope,
        )
    except AiAssistConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc) or "AI Assist 未配置") from exc
    except AiAssistProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc) or "AI Assist 调用失败") from exc
