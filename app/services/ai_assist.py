from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Any, Protocol

from fastapi import HTTPException
import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.time import now_in_timezone, today_in_timezone
from app.models import Contract, Customer, CustomerBuildingProfile, CustomerFollowUp
from app.services.attachments import list_attachments
from app.services.common import (
    attachment_summary,
    contract_room_summary,
    deposit_summary,
    dump_changes,
    log_action_for_buildings,
)
from app.services.contracts import assert_contract_in_scope, get_monthly_receivables
from app.services.customers import (
    build_customer_operating_summary,
    get_customer,
    selected_customer_contracts,
    selected_customer_follow_ups,
)
from app.services.legacy_demo_a_scope import require_legacy_demo_a_base_scope
from app.services.property_scope import (
    LEGACY_DEFAULT_BUILDING_CODE,
    SelectedPropertyScope,
)
from app.services.risks import list_risks


AI_TASK_CUSTOMER_SUMMARY = "customer_summary"
AI_TASK_FOLLOW_UP_SUGGESTION = "follow_up_suggestion"
AI_TASK_RISK_EXPLANATION = "risk_explanation"
AI_TASKS = {
    AI_TASK_CUSTOMER_SUMMARY,
    AI_TASK_FOLLOW_UP_SUGGESTION,
    AI_TASK_RISK_EXPLANATION,
}
TASK_TEXT_FIELD = {
    AI_TASK_CUSTOMER_SUMMARY: "summary",
    AI_TASK_FOLLOW_UP_SUGGESTION: "suggestion",
    AI_TASK_RISK_EXPLANATION: "explanation",
}
TASK_ACTION = {
    AI_TASK_CUSTOMER_SUMMARY: "ai_customer_summary",
    AI_TASK_FOLLOW_UP_SUGGESTION: "ai_follow_suggestion",
    AI_TASK_RISK_EXPLANATION: "ai_risk_explain",
}
TASK_MAX_TOKENS = {
    AI_TASK_CUSTOMER_SUMMARY: 700,
    AI_TASK_FOLLOW_UP_SUGGESTION: 700,
    AI_TASK_RISK_EXPLANATION: 600,
}
CUSTOMER_SUMMARY_CONTRACT_LIMIT = 4
CUSTOMER_SUMMARY_RISK_LIMIT = 5
CUSTOMER_SUMMARY_FOLLOW_UP_LIMIT = 3

SYSTEM_PROMPT = """
你是租赁管理系统的只读 AI Assist。只基于提供的结构化业务上下文回答。
不得生成催收话术草稿，不得要求自动修改合同、应收、押金、风险或跟进记录。
只输出 JSON，字段允许包含 summary、suggestion、explanation、highlights、suggested_actions。
suggested_actions 必须是建议检查、跟进、补充材料、查看页面等人工动作。
如果上下文不足，请说明需要人工补充或核对哪些信息。
""".strip()


class AiAssistConfigurationError(Exception):
    pass


class AiAssistProviderError(Exception):
    pass


class AiAssistProvider(Protocol):
    label: str
    model: str

    def generate(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class DisabledAiAssistProvider:
    reason: str = "AI Assist 未配置"
    label: str = "disabled"
    model: str = ""

    def generate(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        raise AiAssistConfigurationError(self.reason)


@dataclass(frozen=True)
class FakeAiAssistProvider:
    label: str = "fake"
    model: str = "fake-ai-assist"

    def generate(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        customer_name = ((context.get("customer") or {}).get("name") or "客户").strip()
        if task == AI_TASK_CUSTOMER_SUMMARY:
            return {
                "summary": f"{customer_name} 当前经营摘要为内置示例结果，未连接外部模型。",
                "highlights": ["只读摘要", "内置示例"],
                "suggested_actions": ["查看客户详情", "核对本月应收"],
            }
        if task == AI_TASK_FOLLOW_UP_SUGGESTION:
            return {
                "suggestion": f"建议人工核对 {customer_name} 的合同、应收、风险和最近跟进后安排下一步。",
                "highlights": ["不生成催收话术", "不写入跟进记录"],
                "suggested_actions": ["查看最近跟进", "补充必要材料"],
            }
        return {
            "explanation": "该风险解释为内置示例结果，未连接外部模型。",
            "highlights": ["只读解释", "不修改风险状态"],
            "suggested_actions": ["查看风险关联页面", "人工确认处理优先级"],
        }


@dataclass(frozen=True)
class OpenAICompatibleAiAssistProvider:
    api_key: str
    model: str
    base_url: str
    timeout_seconds: float
    label: str = "openai-compatible"

    def generate(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        endpoint = _chat_completions_url(self.base_url)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "context": context,
                        "output_contract": {
                            "text_field": TASK_TEXT_FIELD[task],
                            **_task_output_contract(task),
                            "highlights": "array of short strings",
                            "suggested_actions": "array of human review/check/follow-up actions only",
                        },
                    },
                    ensure_ascii=False,
                    default=_json_default,
                ),
            },
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": TASK_MAX_TOKENS.get(task, 700),
        }
        if task != AI_TASK_CUSTOMER_SUMMARY:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        content = ""
        for attempt in range(2):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    _prepare_retry_payload(payload, messages, task)
                    continue
                raise AiAssistProviderError("AI Assist 调用超时") from exc
            except httpx.HTTPStatusError as exc:
                raise AiAssistProviderError(f"AI Assist 服务返回 {exc.response.status_code}") from exc
            except Exception as exc:
                raise AiAssistProviderError("AI Assist 调用失败") from exc

            content = _extract_chat_content(data)
            if content:
                break
            if attempt == 0:
                payload.pop("response_format", None)
                payload["messages"] = [
                    *messages,
                    {
                        "role": "user",
                        "content": f"上一轮返回为空。请直接输出非空 JSON，并确保 {TASK_TEXT_FIELD[task]} 字段有内容。",
                    },
                ]
        if not content:
            raise AiAssistProviderError("AI Assist 返回为空，请重试")
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {TASK_TEXT_FIELD[task]: str(parsed)}
        except json.JSONDecodeError:
            return {TASK_TEXT_FIELD[task]: content}


def get_configured_provider(settings: Settings) -> AiAssistProvider:
    if not settings.ai_assist_enabled:
        return DisabledAiAssistProvider()
    if not settings.ai_assist_api_key or not settings.ai_assist_model or not settings.ai_assist_base_url:
        return DisabledAiAssistProvider()
    return OpenAICompatibleAiAssistProvider(
        api_key=settings.ai_assist_api_key,
        model=settings.ai_assist_model,
        base_url=settings.ai_assist_base_url,
        timeout_seconds=settings.ai_assist_timeout_seconds,
    )


def _effective_ai_scope(
    db: Session,
    scope: SelectedPropertyScope | None,
) -> SelectedPropertyScope:
    if scope is not None:
        return scope
    legacy = require_legacy_demo_a_base_scope(db)
    return SelectedPropertyScope(
        user_id=0,
        building_ids=(int(legacy.building_id),),
        building_codes=(LEGACY_DEFAULT_BUILDING_CODE,),
        scope_type="building",
        scope_code=LEGACY_DEFAULT_BUILDING_CODE,
        portfolio_id=None,
        access_level="view",
        authorization_version="legacy-demo_a-ai-context",
    )


def build_customer_ai_context(
    db: Session,
    customer_id: int,
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict[str, Any]:
    selected_scope = _effective_ai_scope(db, scope)
    customer = _load_customer_for_ai(db, customer_id, selected_scope)
    contracts = selected_customer_contracts(customer, selected_scope)
    for contract in contracts:
        assert_contract_in_scope(db, contract, selected_scope)
    follow_ups = selected_customer_follow_ups(customer, selected_scope)
    today = today_in_timezone()
    current_month = today.strftime("%Y-%m")
    operating_summary = build_customer_operating_summary(db, customer, contracts, follow_ups=follow_ups)
    receivable_summary = _current_receivable_summary(db, current_month, contracts)
    risks = list_risks(
        db,
        customer_id=customer.id,
        limit=10,
        contracts=contracts,
        building_ids=selected_scope.building_ids,
    )["items"]
    attachments = list_attachments(db, "customer", customer.id, scope=selected_scope)

    return {
        "business_date": today.isoformat(),
        "timezone": "Asia/Shanghai",
        "customer": {
            "id": customer.id,
            "name": customer.name or "",
            "short_name": customer.short_name or "",
            "customer_type": customer.customer_type or "",
            "industry": customer.industry or "",
            "status": customer.status or "active",
        },
        "contracts": [_contract_context(contract) for contract in contracts[:12]],
        "operating_summary": _allowed_keys(
            operating_summary,
            [
                "current_leased_area",
                "current_monthly_rent",
                "current_month",
                "current_month_receivable",
                "unpaid_balance",
                "overdue_balance",
                "contract_count",
                "nearest_expiring_contract",
                "nearest_expiry_days",
                "last_follow_up_time",
                "next_follow_up_date",
                "deposit_expected_amount",
                "deposit_received_amount",
                "deposit_unpaid_amount",
                "deposit_available_amount",
                "deposit_refund_pending_amount",
                "deposit_insufficient_amount",
                "action_cues",
            ],
        ),
        "current_month_receivable": receivable_summary,
        "deposit_summary": deposit_summary([deposit for contract in contracts for deposit in getattr(contract, "deposits", []) or []]),
        "risks": [_risk_context(item) for item in risks],
        "recent_follow_ups": [_follow_up_context(item) for item in _recent_follow_ups(follow_ups)],
        "attachments": {
            "customer_missing_required": attachment_summary(attachments, "customer")["missing_required"],
            "risk_missing_items": [
                _risk_context(item)
                for item in risks
                if item.get("risk_type") == "attachment_missing"
            ][:5],
        },
        "ai_boundaries": {
            "read_only": True,
            "do_not_generate_collection_script": True,
            "do_not_write_business_data": True,
        },
    }


def build_customer_summary_ai_context(
    db: Session,
    customer_id: int,
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict[str, Any]:
    selected_scope = _effective_ai_scope(db, scope)
    customer = _load_customer_for_ai(db, customer_id, selected_scope)
    contracts = selected_customer_contracts(customer, selected_scope)
    for contract in contracts:
        assert_contract_in_scope(db, contract, selected_scope)
    follow_ups = selected_customer_follow_ups(customer, selected_scope)
    today = today_in_timezone()
    current_month = today.strftime("%Y-%m")
    operating_summary = build_customer_operating_summary(db, customer, contracts, follow_ups=follow_ups)
    receivable_summary = _current_receivable_summary(db, current_month, contracts)
    risks = list_risks(
        db,
        customer_id=customer.id,
        limit=8,
        contracts=contracts,
        building_ids=selected_scope.building_ids,
    )["items"]
    attachments = list_attachments(db, "customer", customer.id, scope=selected_scope)
    selected_contracts = _customer_summary_contracts(contracts, risks)

    return {
        "business_date": today.isoformat(),
        "timezone": "Asia/Shanghai",
        "context_scope": {
            "purpose": "short_customer_operating_summary",
            "contract_limit": CUSTOMER_SUMMARY_CONTRACT_LIMIT,
            "risk_limit": CUSTOMER_SUMMARY_RISK_LIMIT,
            "follow_up_limit": CUSTOMER_SUMMARY_FOLLOW_UP_LIMIT,
        },
        "customer": {
            "id": customer.id,
            "name": customer.name or "",
            "short_name": customer.short_name or "",
            "customer_type": customer.customer_type or "",
            "industry": customer.industry or "",
            "status": customer.status or "active",
        },
        "operating_summary": _compact_operating_summary(operating_summary),
        "current_month_receivable": receivable_summary,
        "deposit_summary": deposit_summary([deposit for contract in contracts for deposit in getattr(contract, "deposits", []) or []]),
        "contracts": [_summary_contract_context(contract) for contract in selected_contracts],
        "risks": [_summary_risk_context(item) for item in risks[:CUSTOMER_SUMMARY_RISK_LIMIT]],
        "recent_follow_ups": [
            _summary_follow_up_context(item)
            for item in _recent_follow_ups(follow_ups, limit=CUSTOMER_SUMMARY_FOLLOW_UP_LIMIT)
        ],
        "attachments": {
            "customer_missing_required": attachment_summary(attachments, "customer")["missing_required"][:5],
        },
        "ai_boundaries": {
            "read_only": True,
            "do_not_generate_collection_script": True,
            "do_not_write_business_data": True,
        },
    }


def build_risk_ai_context(
    db: Session,
    payload: dict[str, Any],
    *,
    scope: SelectedPropertyScope | None = None,
) -> dict[str, Any]:
    selected_scope = _effective_ai_scope(db, scope)
    _validate_risk_payload_scope(db, payload, selected_scope)
    risk = _resolve_risk(db, payload, selected_scope)
    _validate_resolved_risk_scope(db, risk, selected_scope)
    context: dict[str, Any] = {
        "business_date": today_in_timezone().isoformat(),
        "timezone": "Asia/Shanghai",
        "risk": _risk_context(risk),
        "ai_boundaries": {
            "read_only": True,
            "do_not_modify_risk_status": True,
            "do_not_generate_collection_script": True,
        },
    }
    customer_id = risk.get("customer_id") or payload.get("customer_id")
    if customer_id:
        customer = get_customer(db, int(customer_id), scope=selected_scope)
        context["customer"] = {
            "id": customer.id,
            "name": customer.name or "",
            "short_name": customer.short_name or "",
            "customer_type": customer.customer_type or "",
            "industry": customer.industry or "",
            "status": customer.status or "active",
        }
    contract_id = risk.get("contract_id") or payload.get("contract_id")
    if contract_id:
        contract = (
            db.query(Contract)
            .options(selectinload(Contract.room_items), selectinload(Contract.deposits))
            .filter(Contract.id == int(contract_id), Contract.is_deleted.is_(False))
            .first()
        )
        if not contract:
            raise HTTPException(404, "合同不存在或无权访问")
        assert_contract_in_scope(db, contract, selected_scope)
        context["contract"] = _minimal_contract_context(contract)
    return context


def run_ai_assist(
    db: Session,
    *,
    task: str,
    context: dict[str, Any],
    provider: AiAssistProvider,
    operator: str,
    target_type: str,
    target_id: int | None,
    scope: SelectedPropertyScope,
) -> dict[str, Any]:
    if task not in AI_TASKS:
        raise ValueError(f"Unsupported AI Assist task: {task}")
    audit_building_ids = _resolve_ai_audit_building_ids(
        db,
        target_type=target_type,
        target_id=target_id,
        context=context,
        scope=scope,
    )
    raw_result = provider.generate(task, context)
    result = normalize_ai_result(task, raw_result, provider)
    log_action_for_buildings(
        db,
        audit_building_ids,
        TASK_ACTION[task],
        dump_changes({"type": task, "provider": provider.label}),
        operator,
        commit=False,
        target_type=target_type,
        target_id=target_id,
    )
    db.commit()
    return result


def _resolve_ai_audit_building_ids(
    db: Session,
    *,
    target_type: str,
    target_id: int | None,
    context: dict[str, Any],
    scope: SelectedPropertyScope,
) -> tuple[int, ...]:
    """Resolve only buildings that truly own or reference the AI target.

    A portfolio selection is an authorization envelope, not object ownership.
    In particular, the shared customer master may be visible through one or two
    buildings without belonging to every building in the selected portfolio.
    """
    allowed_ids = tuple(dict.fromkeys(int(item) for item in scope.building_ids))
    allowed_set = set(allowed_ids)
    risk = context.get("risk") if isinstance(context.get("risk"), dict) else {}

    exact_building_id = _risk_building_id(db, risk, allowed_set)
    contract_id = _safe_int(risk.get("contract_id"))
    if exact_building_id is None and contract_id is not None:
        exact_building_id = _contract_building_id(db, contract_id, allowed_set)

    normalized_target_type = str(target_type or "").strip().lower()
    if normalized_target_type == "contract" and target_id is not None:
        exact_building_id = _contract_building_id(db, int(target_id), allowed_set)

    if normalized_target_type == "customer" and target_id is not None:
        related_ids = _customer_building_ids(db, int(target_id), allowed_ids)
        if exact_building_id is not None:
            related_ids = tuple(item for item in related_ids if item == exact_building_id)
        if related_ids:
            return related_ids
    elif exact_building_id is not None:
        return (exact_building_id,)

    raise HTTPException(409, "AI 操作对象尚未明确楼宇归属")


def _risk_building_id(
    db: Session,
    risk: dict[str, Any],
    allowed_ids: set[int],
) -> int | None:
    direct = _safe_int(risk.get("building_id"))
    meta = risk.get("meta") if isinstance(risk.get("meta"), dict) else {}
    direct = direct if direct is not None else _safe_int(meta.get("building_id"))
    if direct is not None:
        return direct if direct in allowed_ids else None

    stable_key = str(risk.get("stable_key") or risk.get("id") or "")
    if stable_key.startswith("follow_up_overdue:"):
        follow_up_id = _safe_int(stable_key.rsplit(":", 1)[-1])
        if follow_up_id is not None:
            building_id = db.query(CustomerFollowUp.building_id).filter(CustomerFollowUp.id == follow_up_id).scalar()
            normalized = _safe_int(building_id)
            return normalized if normalized in allowed_ids else None
    return None


def _contract_building_id(
    db: Session,
    contract_id: int,
    allowed_ids: set[int],
) -> int | None:
    building_id = (
        db.query(Contract.building_id)
        .filter(Contract.id == int(contract_id), Contract.is_deleted.is_(False))
        .scalar()
    )
    normalized = _safe_int(building_id)
    return normalized if normalized in allowed_ids else None


def _customer_building_ids(
    db: Session,
    customer_id: int,
    allowed_ids: tuple[int, ...],
) -> tuple[int, ...]:
    allowed_set = set(allowed_ids)
    related: set[int] = {
        int(value)
        for (value,) in db.query(Contract.building_id)
        .filter(
            Contract.customer_id == int(customer_id),
            Contract.is_deleted.is_(False),
            Contract.building_id.in_(allowed_ids or (-1,)),
        )
        .all()
        if value is not None
    }
    related.update(
        int(value)
        for (value,) in db.query(CustomerFollowUp.building_id)
        .filter(
            CustomerFollowUp.customer_id == int(customer_id),
            CustomerFollowUp.building_id.in_(allowed_ids or (-1,)),
        )
        .all()
        if value is not None
    )
    today = today_in_timezone()
    related.update(
        int(value)
        for (value,) in db.query(CustomerBuildingProfile.building_id)
        .filter(
            CustomerBuildingProfile.customer_id == int(customer_id),
            CustomerBuildingProfile.building_id.in_(allowed_ids or (-1,)),
            CustomerBuildingProfile.valid_from <= today,
            or_(
                CustomerBuildingProfile.valid_to.is_(None),
                CustomerBuildingProfile.valid_to >= today,
            ),
        )
        .all()
        if value is not None
    )
    return tuple(building_id for building_id in allowed_ids if building_id in related and building_id in allowed_set)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_ai_result(task: str, raw_result: dict[str, Any], provider: AiAssistProvider) -> dict[str, Any]:
    field = TASK_TEXT_FIELD[task]
    source = raw_result if isinstance(raw_result, dict) else {}
    text = _extract_result_text(task, source)
    if not text:
        raise AiAssistProviderError("AI Assist 返回为空，请重试")
    return {
        field: text,
        "highlights": _string_list(source.get("highlights"))[:8],
        "suggested_actions": _string_list(source.get("suggested_actions"))[:8],
        "generated_at": source.get("generated_at") or now_in_timezone().isoformat(timespec="seconds"),
        "model": provider.model or "",
        "provider_label": provider.label,
    }


def _extract_result_text(task: str, source: dict[str, Any]) -> str:
    field = TASK_TEXT_FIELD[task]
    aliases = [
        field,
        "summary",
        "suggestion",
        "explanation",
        "text",
        "result",
        "answer",
    ]
    if task == AI_TASK_RISK_EXPLANATION:
        aliases.extend(["risk_explanation", "risk_analysis", "reason", "impact", "recommendation"])
    for key in aliases:
        text = _clean_text(source.get(key))
        if text:
            return text
    if task == AI_TASK_RISK_EXPLANATION:
        parts = [_clean_text(source.get(key), limit=400) for key in ("risk_reason", "risk_impact", "handling_direction")]
        joined = "\n".join(part for part in parts if part)
        if joined:
            return _clean_text(joined)
    return ""


def _load_customer_for_ai(
    db: Session,
    customer_id: int,
    scope: SelectedPropertyScope,
) -> Customer:
    return get_customer(db, customer_id, scope=scope)


def _current_receivable_summary(db: Session, month: str, contracts: list[Contract]) -> dict[str, Any]:
    contract_ids = [contract.id for contract in contracts if contract.id]
    if not contract_ids:
        return {
            "month": month,
            "total": 0,
            "received_amount": 0,
            "unpaid_balance": 0,
            "overdue_balance": 0,
            "not_due_balance": 0,
            "overdue_count": 0,
        }
    report = get_monthly_receivables(db, month, contract_ids=contract_ids)
    return {
        "month": report.get("month") or month,
        "total": report.get("total") or 0,
        "received_amount": report.get("total_received") or 0,
        "unpaid_balance": report.get("total_unpaid_balance") or 0,
        "overdue_balance": report.get("total_overdue_balance") or 0,
        "not_due_balance": report.get("total_not_due_balance") or 0,
        "overdue_count": report.get("overdue_count") or 0,
    }


def _contract_context(contract: Contract) -> dict[str, Any]:
    summary = contract_room_summary(contract)
    return {
        "id": contract.id,
        "contract_no": contract.contract_no or "",
        "status": contract.status,
        "start_date": _date_text(contract.start_date),
        "end_date": _date_text(contract.end_date),
        "tower": summary.get("tower") or "",
        "room_no": summary.get("room_no") or "",
        "area": round(float(summary.get("area") or 0), 2),
        "unit_price": round(float(summary.get("unit_price") or 0), 2),
        "monthly_rent": round(float(summary.get("monthly_rent") or 0), 2),
        "rooms": [
            {
                "room_no": item.room_no or "",
                "tower": item.tower or "",
                "area": round(float(item.area or 0), 2),
                "unit_price": round(float(item.unit_price or 0), 2),
                "monthly_rent": round(float(item.monthly_rent or 0), 2),
                "start_date": _date_text(item.start_date or contract.start_date),
                "end_date": _date_text(item.end_date or contract.end_date),
            }
            for item in list(getattr(contract, "room_items", []) or [])[:8]
            if item.room_no
        ],
        "deposit_summary": deposit_summary(list(getattr(contract, "deposits", []) or [])),
    }


def _minimal_contract_context(contract: Contract) -> dict[str, Any]:
    row = _contract_context(contract)
    return _allowed_keys(
        row,
        [
            "id",
            "contract_no",
            "status",
            "start_date",
            "end_date",
            "tower",
            "room_no",
            "area",
            "monthly_rent",
            "deposit_summary",
        ],
    )


def _summary_contract_context(contract: Contract) -> dict[str, Any]:
    summary = contract_room_summary(contract)
    return {
        "id": contract.id,
        "contract_no": contract.contract_no or "",
        "status": contract.status,
        "start_date": _date_text(contract.start_date),
        "end_date": _date_text(contract.end_date),
        "days_to_expiry": contract.days_to_expiry,
        "tower": summary.get("tower") or "",
        "room_no": summary.get("room_no") or "",
        "area": round(float(summary.get("area") or 0), 2),
        "monthly_rent": round(float(summary.get("monthly_rent") or 0), 2),
        "deposit_summary": deposit_summary(list(getattr(contract, "deposits", []) or [])),
    }


def _risk_context(risk: dict[str, Any]) -> dict[str, Any]:
    return _allowed_keys(
        risk,
        [
            "id",
            "stable_key",
            "risk_type",
            "severity",
            "title",
            "description",
            "entity_type",
            "entity_id",
            "customer_id",
            "customer_name",
            "contract_id",
            "contract_no",
            "room_no",
            "amount",
            "due_date",
            "occurred_on",
            "action_label",
            "action_target",
            "meta",
        ],
    )


def _summary_risk_context(risk: dict[str, Any]) -> dict[str, Any]:
    return _allowed_keys(
        risk,
        [
            "id",
            "risk_type",
            "severity",
            "title",
            "entity_type",
            "entity_id",
            "contract_id",
            "contract_no",
            "amount",
            "due_date",
            "occurred_on",
        ],
    )


def _follow_up_context(follow_up: CustomerFollowUp) -> dict[str, Any]:
    contract = getattr(follow_up, "contract", None)
    return {
        "id": follow_up.id,
        "contract_no": contract.contract_no if contract else "",
        "follow_up_type": follow_up.follow_up_type or "",
        "occurred_at": _date_text(follow_up.occurred_at),
        "content_summary": _clean_text(follow_up.content, limit=220),
        "next_action": _clean_text(follow_up.next_action, limit=160),
        "next_follow_up_date": _date_text(follow_up.next_follow_up_date),
        "status": follow_up.status or "open",
    }


def _summary_follow_up_context(follow_up: CustomerFollowUp) -> dict[str, Any]:
    contract = getattr(follow_up, "contract", None)
    return {
        "id": follow_up.id,
        "contract_no": contract.contract_no if contract else "",
        "follow_up_type": follow_up.follow_up_type or "",
        "occurred_at": _date_text(follow_up.occurred_at),
        "content_summary": _clean_text(follow_up.content, limit=120),
        "next_action": _clean_text(follow_up.next_action, limit=100),
        "next_follow_up_date": _date_text(follow_up.next_follow_up_date),
        "status": follow_up.status or "open",
    }


def _recent_follow_ups(
    follow_ups: list[CustomerFollowUp],
    limit: int = 5,
) -> list[CustomerFollowUp]:
    rows = list(follow_ups)
    rows.sort(key=lambda item: (item.occurred_at or datetime.min, item.id or 0), reverse=True)
    return rows[:limit]


def _compact_operating_summary(operating_summary: dict[str, Any]) -> dict[str, Any]:
    row = _allowed_keys(
        operating_summary,
        [
            "current_leased_area",
            "current_monthly_rent",
            "current_month",
            "current_month_receivable",
            "unpaid_balance",
            "overdue_balance",
            "contract_count",
            "nearest_expiring_contract",
            "nearest_expiry_days",
            "last_follow_up_time",
            "next_follow_up_date",
            "deposit_unpaid_amount",
            "deposit_refund_pending_amount",
            "deposit_insufficient_amount",
            "action_cues",
        ],
    )
    if "action_cues" in row and isinstance(row["action_cues"], list):
        row["action_cues"] = row["action_cues"][:5]
    return row


def _customer_summary_contracts(contracts: list[Contract], risks: list[dict[str, Any]]) -> list[Contract]:
    today = today_in_timezone()
    risk_contract_ids = {
        contract_id
        for contract_id in (_risk_contract_id(risk) for risk in risks)
        if contract_id is not None
    }

    def priority(contract: Contract) -> tuple[int, int, str]:
        days = contract.days_to_expiry
        if contract.id in risk_contract_ids:
            bucket = 0
        elif days is not None and 0 <= days <= 90:
            bucket = 1
        elif contract.start_date and contract.end_date and contract.start_date <= today <= contract.end_date:
            bucket = 2
        else:
            bucket = 3
        return (bucket, days if days is not None and days >= 0 else 99999, contract.contract_no or "")

    return sorted(contracts, key=priority)[:CUSTOMER_SUMMARY_CONTRACT_LIMIT]


def _risk_contract_id(risk: dict[str, Any]) -> int | None:
    value = risk.get("contract_id")
    if value is None and risk.get("entity_type") == "contract":
        value = risk.get("entity_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _scoped_ai_contract(
    db: Session,
    contract_id: int,
    scope: SelectedPropertyScope,
) -> Contract:
    contract = (
        db.query(Contract)
        .filter(Contract.id == int(contract_id), Contract.is_deleted.is_(False))
        .first()
    )
    if not contract:
        raise HTTPException(404, "合同不存在或无权访问")
    assert_contract_in_scope(db, contract, scope)
    return contract


def _risk_customer_id(risk: dict[str, Any]) -> int | None:
    value = risk.get("customer_id")
    if value is None and risk.get("entity_type") == "customer":
        value = risk.get("entity_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _validate_risk_payload_scope(
    db: Session,
    payload: dict[str, Any],
    scope: SelectedPropertyScope,
) -> None:
    contract_id = payload.get("contract_id")
    if contract_id is not None:
        _scoped_ai_contract(db, int(contract_id), scope)
    customer_id = payload.get("customer_id")
    if customer_id is not None:
        get_customer(db, int(customer_id), scope=scope)


def _validate_resolved_risk_scope(
    db: Session,
    risk: dict[str, Any],
    scope: SelectedPropertyScope,
) -> None:
    contract_id = _risk_contract_id(risk)
    if contract_id is not None:
        _scoped_ai_contract(db, contract_id, scope)
    customer_id = _risk_customer_id(risk)
    if customer_id is not None:
        get_customer(db, customer_id, scope=scope)


def _resolve_risk(
    db: Session,
    payload: dict[str, Any],
    scope: SelectedPropertyScope,
) -> dict[str, Any]:
    risk_id = _clean_text(payload.get("id") or payload.get("stable_key"))
    risk_type = _clean_text(payload.get("risk_type"))
    contract_id = payload.get("contract_id")
    customer_id = payload.get("customer_id")
    entity_type = _clean_text(payload.get("target_type") or payload.get("entity_type"))
    entity_id = payload.get("target_id") or payload.get("entity_id")
    risks = list_risks(
        db,
        risk_type=risk_type or None,
        contract_id=int(contract_id) if contract_id else None,
        customer_id=int(customer_id) if customer_id else None,
        limit=500,
        building_ids=scope.building_ids,
    )["items"]
    for risk in risks:
        if risk_id and risk_id not in {str(risk.get("id") or ""), str(risk.get("stable_key") or "")}:
            continue
        if entity_type and risk.get("entity_type") != entity_type:
            continue
        if entity_id and int(risk.get("entity_id") or 0) != int(entity_id):
            continue
        return risk
    raise HTTPException(404, "风险项不存在")


def _chat_completions_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    return f"{value}/chat/completions"


def _prepare_retry_payload(payload: dict[str, Any], messages: list[dict[str, str]], task: str) -> None:
    payload.pop("response_format", None)
    payload["messages"] = [
        *messages,
        {
            "role": "user",
            "content": f"Previous attempt returned no usable content. Return non-empty JSON with a populated {TASK_TEXT_FIELD[task]} field.",
        },
    ]


def _task_output_contract(task: str) -> dict[str, Any]:
    if task == AI_TASK_CUSTOMER_SUMMARY:
        return {
            "style": "compact JSON only; no markdown, no prose outside JSON, do not repeat source data",
            "summary": "1-2 short Chinese sentences, <= 160 Chinese characters total",
            "highlights": "1-3 short strings, each <= 24 Chinese characters",
            "suggested_actions": "1-3 short human-only actions, each <= 24 Chinese characters",
        }
    return {
        "style": "JSON only; no prose outside JSON",
    }


def _extract_chat_content(data: dict[str, Any]) -> str:
    if not isinstance(data, dict):
        raise AiAssistProviderError("AI Assist 返回格式不正确，请重试")
    choices = data.get("choices")
    if choices is None:
        return ""
    if not isinstance(choices, list):
        raise AiAssistProviderError("AI Assist 返回格式不正确，请重试")
    if not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        raise AiAssistProviderError("AI Assist 返回格式不正确，请重试")
    message = first.get("message")
    if message is None:
        return ""
    if not isinstance(message, dict):
        raise AiAssistProviderError("AI Assist 返回格式不正确，请重试")
    return _chat_content_text(message.get("content"))


def _chat_content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(text, dict) and isinstance(text.get("value"), str):
                    parts.append(text["value"])
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    raise AiAssistProviderError("AI Assist 返回格式不正确，请重试")


def _allowed_keys(data: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: data.get(key) for key in keys if key in data}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_clean_text(item, limit=160) for item in value if _clean_text(item, limit=160)]


def _clean_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _date_text(value: date | datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return _date_text(value)
    return str(value)
