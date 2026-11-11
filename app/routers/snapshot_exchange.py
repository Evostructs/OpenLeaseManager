from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SnapshotExchangeReceipt, User
from app.services import snapshot_exchange as exchange_service
from app.services import snapshot_exchange_uploads as upload_service
from app.core.uploads import read_import_upload
from app.services.property_scope import require_capability
from app.snapshot_exchange_schemas import (
    SnapshotExchangePackageV1,
    SnapshotExchangeReceiptOut,
)


API_PREFIX = "/api/snapshot-exchange/v1"
router = APIRouter(prefix=API_PREFIX, tags=["snapshot-exchange"])
require_snapshot_exchange_receive = require_capability("snapshot_exchange_receive")
require_data_import_submit = require_capability("data_import_submit")


_PROBLEM_DEFAULTS: dict[int, tuple[str, str, str]] = {
    400: (
        "urn:lease-manager:problem:snapshot-exchange-invalid-json",
        "请求格式不合法",
        "请求不是可接受的 JSON 交换包。",
    ),
    401: (
        "urn:lease-manager:problem:snapshot-exchange-authentication-required",
        "需要登录",
        "请先登录后再访问快照交换接口。",
    ),
    403: (
        "urn:lease-manager:problem:snapshot-exchange-forbidden",
        "无权访问",
        "当前账号没有所需的快照交换权限。",
    ),
    404: (
        "urn:lease-manager:problem:snapshot-exchange-receipt-not-found",
        "交换对象不存在",
        "未找到可访问的交换对象。",
    ),
    409: (
        "urn:lease-manager:problem:snapshot-exchange-conflict",
        "投递冲突",
        "本次请求与已有投递记录冲突。",
    ),
    413: (
        "urn:lease-manager:problem:snapshot-exchange-request-too-large",
        "请求内容过大",
        "交换包超过服务允许的大小。",
    ),
    422: (
        "urn:lease-manager:problem:snapshot-exchange-invalid-package",
        "交换包未通过校验",
        "交换包格式或业务内容不符合 OPS_EXCHANGE_V1。",
    ),
    503: (
        "urn:lease-manager:problem:snapshot-exchange-unavailable",
        "接收服务暂不可用",
        "快照交换接收服务暂不可用，请稍后重试。",
    ),
}


def is_snapshot_exchange_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def problem_response(
    status_code: int,
    *,
    receipt: SnapshotExchangeReceiptOut | None = None,
    errors: list[dict] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    problem_type, title, detail = _PROBLEM_DEFAULTS.get(
        status_code,
        (
            "urn:lease-manager:problem:snapshot-exchange-request-failed",
            "快照交换请求失败",
            "快照交换请求未能完成。",
        ),
    )
    content: dict[str, Any] = {
        "type": problem_type,
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    if receipt is not None:
        content["receipt"] = receipt.model_dump(mode="json")
    if errors is not None:
        content["errors"] = errors
    response_headers = {"Cache-Control": "private, no-store"}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=response_headers,
        media_type="application/problem+json",
    )


class _DuplicateJsonKey(ValueError):
    pass


_MAX_JSON_NESTING = 100


def _json_nesting_exceeds_limit(text: str, *, limit: int = _MAX_JSON_NESTING) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > limit:
                return True
        elif character in "]}":
            depth = max(0, depth - 1)
    return False


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


async def _parse_package(request: Request) -> SnapshotExchangePackageV1:
    try:
        text = (await request.body()).decode("utf-8", errors="strict")
        if _json_nesting_exceeds_limit(text):
            raise ValueError("JSON nesting limit exceeded")
        raw = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise HTTPException(status_code=400) from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422)
    try:
        return SnapshotExchangePackageV1.model_validate(raw)
    except ValidationError as exc:
        # Do not echo input values or internal validation locations into a public
        # transport error.  Detailed, stable business issues belong in a receipt.
        raise HTTPException(status_code=422) from exc


def _receipt_location(receipt: SnapshotExchangeReceiptOut) -> str:
    return f"{API_PREFIX}/receipts/{receipt.receipt_no}"


def _receipt_response(
    receipt: SnapshotExchangeReceiptOut,
    *,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=receipt.model_dump(mode="json"),
        headers={
            "Cache-Control": "private, no-store",
            "Location": _receipt_location(receipt),
        },
    )


def _read_receipt(db: Session, receipt_no: str) -> SnapshotExchangeReceiptOut | None:
    # This adapter deliberately stays at the HTTP boundary.  As soon as the
    # service exposes a public reader, the route uses it without changing the
    # transport contract.  The fallback keeps today's serialization centralized
    # in the service instead of duplicating or accidentally widening its fields.
    public_reader = getattr(exchange_service, "get_snapshot_exchange_receipt", None)
    if public_reader is None:
        public_reader = getattr(exchange_service, "get_receipt", None)
    if public_reader is not None:
        return public_reader(db, receipt_no=receipt_no)

    row = (
        db.query(SnapshotExchangeReceipt)
        .filter(SnapshotExchangeReceipt.receipt_no == receipt_no)
        .one_or_none()
    )
    if row is None:
        return None
    return exchange_service._serialize_receipt(db, row)


@router.post("/uploads/preflight")
async def preflight_upload(
    file: UploadFile = File(...),
    actor: User = Depends(require_data_import_submit),
    db: Session = Depends(get_db),
):
    content = await read_import_upload(file)
    try:
        result = upload_service.preflight_upload(db, content=content, actor=actor)
    except upload_service.SnapshotExchangeUploadError as exc:
        return problem_response(422, errors=[exc.issue])
    except exchange_service.SnapshotExchangeIdentityError as exc:
        db.rollback()
        return problem_response(
            422,
            errors=[
                {
                    "code": "source_binding_invalid",
                    "field": "source_system_code",
                    "row": None,
                    "message": "来源系统不存在、已停用或未绑定到声明楼宇",
                }
            ],
        )
    except exchange_service.SnapshotExchangeUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, headers={"Retry-After": "1"}) from exc
    return JSONResponse(
        status_code=200,
        content=result,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/uploads/receipts")
def list_upload_receipts(
    actor: User = Depends(require_data_import_submit),
    db: Session = Depends(get_db),
):
    return JSONResponse(
        status_code=200,
        content=upload_service.list_owned_upload_receipts(db, actor=actor),
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/uploads/sources")
def list_upload_sources(
    actor: User = Depends(require_data_import_submit),
    db: Session = Depends(get_db),
):
    return JSONResponse(
        status_code=200,
        content=upload_service.list_upload_sources(db, actor=actor),
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/uploads/template.xlsx")
def download_upload_template(
    source_system_code: str,
    actor: User = Depends(require_data_import_submit),
    db: Session = Depends(get_db),
):
    source = upload_service.get_upload_source(
        db,
        actor=actor,
        source_system_code=source_system_code,
    )
    if source is None:
        raise HTTPException(status_code=404)
    content = upload_service.build_excel_template(
        source_system_code=source["source_system_code"],
        building_code=source["building_code"],
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": (
                'attachment; filename="OPS_EXCHANGE_XLSX_V1-template.xlsx"'
            ),
        },
    )


@router.get("/uploads/receipts/{receipt_no}")
def get_upload_receipt(
    receipt_no: str,
    actor: User = Depends(require_data_import_submit),
    db: Session = Depends(get_db),
):
    if not receipt_no or len(receipt_no) > 150:
        raise HTTPException(status_code=404)
    receipt = upload_service.get_owned_upload_receipt(
        db,
        actor=actor,
        receipt_no=receipt_no,
    )
    if receipt is None:
        raise HTTPException(status_code=404)
    return _receipt_response(receipt, status_code=200)


@router.post("/uploads/submit")
async def submit_upload(
    file: UploadFile = File(...),
    actor: User = Depends(require_data_import_submit),
    db: Session = Depends(get_db),
):
    content = await read_import_upload(file)
    try:
        result = upload_service.submit_upload(db, content=content, actor=actor)
    except upload_service.SnapshotExchangeUploadError as exc:
        db.rollback()
        return problem_response(422, errors=[exc.issue])
    except exchange_service.SnapshotExchangeIdentityError as exc:
        db.rollback()
        raise HTTPException(status_code=422) from exc
    except exchange_service.SnapshotExchangeUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, headers={"Retry-After": "1"}) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422) from exc

    receipt = result.receipt
    if result.exact_replay:
        return _receipt_response(receipt, status_code=200)
    if receipt.status == "conflict":
        return problem_response(
            409,
            receipt=receipt,
            headers={"Location": _receipt_location(receipt)},
        )
    if receipt.status == "rejected":
        return problem_response(
            422,
            receipt=receipt,
            headers={"Location": _receipt_location(receipt)},
        )
    return _receipt_response(
        receipt,
        status_code=201 if receipt.status == "accepted" else 200,
    )


@router.put("/deliveries/{delivery_id}")
async def put_delivery(
    delivery_id: str,
    request: Request,
    actor: User = Depends(require_snapshot_exchange_receive),
    db: Session = Depends(get_db),
):
    package = await _parse_package(request)
    if delivery_id != package.delivery_id:
        raise HTTPException(status_code=422)
    try:
        result = exchange_service.receive_snapshot_package(
            db,
            package=package,
            actor=actor,
        )
    except exchange_service.SnapshotExchangeIdentityError as exc:
        db.rollback()
        raise HTTPException(status_code=422) from exc
    except Exception as exc:
        unavailable_type = getattr(exchange_service, "SnapshotExchangeUnavailable", None)
        if unavailable_type is not None and isinstance(exc, unavailable_type):
            db.rollback()
            raise HTTPException(
                status_code=503,
                headers={"Retry-After": "1"},
            ) from exc
        if isinstance(exc, ValueError):
            db.rollback()
            raise HTTPException(status_code=422) from exc
        raise

    receipt = result.receipt
    if result.exact_replay:
        return _receipt_response(receipt, status_code=200)
    if receipt.status == "conflict":
        return problem_response(
            409,
            receipt=receipt,
            headers={"Location": _receipt_location(receipt)},
        )
    if receipt.status == "rejected":
        return problem_response(
            422,
            receipt=receipt,
            headers={"Location": _receipt_location(receipt)},
        )
    return _receipt_response(
        receipt,
        status_code=201 if receipt.status == "accepted" and not result.exact_replay else 200,
    )


@router.get("/receipts/{receipt_no}")
def get_receipt(
    receipt_no: str,
    _actor: User = Depends(require_snapshot_exchange_receive),
    db: Session = Depends(get_db),
):
    if not receipt_no or len(receipt_no) > 150:
        raise HTTPException(status_code=404)
    receipt = _read_receipt(db, receipt_no)
    if receipt is None:
        raise HTTPException(status_code=404)
    return _receipt_response(receipt, status_code=200)
