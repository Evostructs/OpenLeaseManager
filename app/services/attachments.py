from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, selectinload
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.crypto import CryptoError, decrypt_file, encrypted_name, encrypt_stream_to_file, is_encrypted_file
from app.core.files import ensure_child_path, sanitize_stem
from app.core.time import now_in_timezone
from app.models import Attachment, Contract, ContractDeposit, Customer, CustomerFollowUp, OperationLog, Room
from app.schemas import AttachmentUpdate
from app.services.common import contract_room_summary, customer_display_name, dump_changes
from app.services.contracts import _is_currently_active
from app.services.property_scope import SelectedPropertyScope


VALID_ENTITY_TYPES = {"contract", "customer", "deposit", "follow_up", "room"}
ATTACHMENT_CATEGORIES = {"合同正文", "补充协议", "证照", "押金凭证", "收款凭证", "沟通记录", "其他"}
MAX_ATTACHMENT_FILE_SIZE = 50 * 1024 * 1024
ATTACHMENT_CHUNK_SIZE = 1024 * 1024
CONTRACT_BODY_CATEGORY = "合同正文"
CUSTOMER_LICENSE_CATEGORY = "证照"


def _clean_text(value, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _validate_entity_type(entity_type: str) -> str:
    normalized = _clean_text(entity_type).lower()
    if normalized not in VALID_ENTITY_TYPES:
        raise HTTPException(400, "附件对象类型无效")
    return normalized


def _validate_category(category: str | None) -> str:
    normalized = _clean_text(category, "其他")
    if normalized not in ATTACHMENT_CATEGORIES:
        raise HTTPException(400, "附件分类无效")
    return normalized


@dataclass(frozen=True)
class AttachmentEntityScope:
    contract_id: int | None
    building_id: int | None


def _selected_building_pairs(scope: SelectedPropertyScope) -> tuple[tuple[int, str], ...]:
    if (
        scope.scope_type not in {"building", "portfolio"}
        or scope.access_level not in {"view", "edit"}
        or not str(scope.scope_code or "").strip()
        or not scope.building_ids
        or len(scope.building_ids) != len(scope.building_codes)
    ):
        raise HTTPException(403, "当前楼宇范围无效")
    try:
        pairs = tuple((int(building_id), str(code).strip()) for building_id, code in zip(scope.building_ids, scope.building_codes))
        portfolio_id = int(scope.portfolio_id) if scope.portfolio_id is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "当前楼宇范围无效") from exc
    ids = [building_id for building_id, _code in pairs]
    codes = [code.casefold() for _building_id, code in pairs]
    if (
        any(building_id <= 0 or not code for building_id, code in pairs)
        or len(set(ids)) != len(ids)
        or len(set(codes)) != len(codes)
        or (
            scope.scope_type == "building"
            and (
                len(pairs) != 1
                or scope.portfolio_id is not None
                or str(scope.scope_code).strip().casefold() != pairs[0][1].casefold()
            )
        )
        or (
            scope.scope_type == "portfolio"
            and (portfolio_id is None or portfolio_id <= 0)
        )
    ):
        raise HTTPException(403, "当前楼宇范围无效")
    return pairs


def _assert_building_in_scope(building_id: int | None, scope: SelectedPropertyScope) -> int:
    selected_ids = {item_id for item_id, _code in _selected_building_pairs(scope)}
    if building_id is None:
        raise HTTPException(409, "附件对象尚未明确楼宇归属")
    normalized = int(building_id)
    if normalized not in selected_ids:
        raise HTTPException(404, "附件对象不存在或无权访问")
    return normalized


def _single_selected_building(scope: SelectedPropertyScope) -> int:
    pairs = _selected_building_pairs(scope)
    if scope.scope_type != "building" or len(pairs) != 1:
        raise HTTPException(422, "上传附件必须选择单栋楼宇")
    return pairs[0][0]


def _require_edit_scope(scope: SelectedPropertyScope) -> None:
    _selected_building_pairs(scope)
    if scope.access_level != "edit":
        raise HTTPException(403, "当前楼宇仅有查看权限")


def _resolve_entity_scope(
    db: Session,
    entity_type: str,
    entity_id: int,
    scope: SelectedPropertyScope,
) -> AttachmentEntityScope:
    """Resolve the parent first; IDs never grant access by themselves."""
    _selected_building_pairs(scope)
    if entity_type == "contract":
        contract = db.query(Contract).filter(Contract.id == entity_id, Contract.is_deleted.is_(False)).first()
        if not contract:
            raise HTTPException(404, "附件对象不存在或无权访问")
        building_id = _assert_building_in_scope(contract.building_id, scope)
        return AttachmentEntityScope(contract_id=contract.id, building_id=building_id)

    if entity_type == "customer":
        # The legal master is shared, but a guessed global customer ID must not
        # establish its first relationship to the selected building.  The customer
        # must already be visible through a local profile, contract or follow-up.
        from app.services.customers import get_customer

        get_customer(db, entity_id, scope=scope)
        return AttachmentEntityScope(contract_id=None, building_id=None)

    if entity_type == "deposit":
        deposit = db.query(ContractDeposit).filter(ContractDeposit.id == entity_id).first()
        if not deposit:
            raise HTTPException(404, "附件对象不存在或无权访问")
        contract = db.get(Contract, deposit.contract_id)
        if not contract or contract.is_deleted:
            raise HTTPException(404, "附件对象不存在或无权访问")
        building_id = _assert_building_in_scope(contract.building_id, scope)
        return AttachmentEntityScope(contract_id=contract.id, building_id=building_id)

    if entity_type == "follow_up":
        follow_up = db.query(CustomerFollowUp).filter(CustomerFollowUp.id == entity_id).first()
        if not follow_up:
            raise HTTPException(404, "附件对象不存在或无权访问")
        building_id = _assert_building_in_scope(follow_up.building_id, scope)
        if follow_up.contract_id is not None:
            contract = db.get(Contract, follow_up.contract_id)
            if not contract or contract.is_deleted:
                raise HTTPException(409, "附件跟进记录的关联合同不存在")
            contract_building_id = _assert_building_in_scope(contract.building_id, scope)
            if contract_building_id != building_id:
                raise HTTPException(409, "附件跟进记录与关联合同楼宇不一致")
        return AttachmentEntityScope(contract_id=follow_up.contract_id, building_id=building_id)

    if entity_type == "room":
        room = db.query(Room).filter(Room.id == entity_id, Room.is_deleted.is_(False)).first()
        if not room:
            raise HTTPException(404, "附件对象不存在或无权访问")
        building_id = _assert_building_in_scope(room.building_id, scope)
        return AttachmentEntityScope(contract_id=None, building_id=building_id)

    raise HTTPException(400, "附件对象类型无效")


def _assert_attachment_scope(attachment: Attachment, scope: SelectedPropertyScope) -> int:
    selected_ids = {building_id for building_id, _code in _selected_building_pairs(scope)}
    if (
        attachment.scope_type != "building"
        or attachment.building_id is None
        or attachment.portfolio_id is not None
    ):
        raise HTTPException(409, "附件尚未明确楼宇归属")
    building_id = int(attachment.building_id)
    if building_id not in selected_ids:
        raise HTTPException(404, "附件不存在或无权访问")
    return building_id


def _validate_attachment_parent(
    db: Session,
    attachment: Attachment,
    scope: SelectedPropertyScope,
) -> AttachmentEntityScope:
    building_id = _assert_attachment_scope(attachment, scope)
    entity_type = _validate_entity_type(attachment.entity_type)
    parent = _resolve_entity_scope(db, entity_type, int(attachment.entity_id), scope)
    if parent.building_id is not None and parent.building_id != building_id:
        raise HTTPException(409, "附件楼宇与业务对象楼宇不一致")
    return parent


def _add_attachment_log(
    db: Session,
    attachment: Attachment,
    parent: AttachmentEntityScope,
    action: str,
    changes: str,
    operator: str,
) -> None:
    db.add(
        OperationLog(
            contract_id=parent.contract_id,
            scope_type="building",
            portfolio_id=None,
            building_id=attachment.building_id,
            target_type="attachment",
            target_id=attachment.id,
            action=action,
            changes=changes,
            operator=operator,
        )
    )


def record_attachment_download(
    db: Session,
    attachment: Attachment,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> None:
    parent = _validate_attachment_parent(db, attachment, scope)
    _add_attachment_log(
        db,
        attachment,
        parent,
        "download",
        dump_changes({"attachment_id": attachment.id, "file_name": attachment.file_name}),
        operator,
    )
    db.commit()


def _original_filename(upload: UploadFile) -> str:
    raw_name = (upload.filename or "").replace("\\", "/")
    filename = Path(raw_name).name.strip()
    if not filename:
        raise HTTPException(400, "请上传文件")
    return filename[:255]


def _safe_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if not suffix or len(suffix) > 16:
        return ""
    return "".join(char for char in suffix if char in ".abcdefghijklmnopqrstuvwxyz0123456789")


def _storage_path(entity_type: str, entity_id: int, filename: str) -> str:
    stem = sanitize_stem(Path(filename).stem, "attachment")[:80]
    basename = f"{uuid4().hex}_{stem}{_safe_suffix(filename)}"
    if get_settings().data_encryption_enabled:
        basename = encrypted_name(basename)
    return f"{entity_type}/{entity_id}/{basename}"


def _attachment_path(storage_path: str) -> Path:
    settings = get_settings()
    return ensure_child_path(settings.attachment_dir, storage_path)


def _write_limited_upload(upload: UploadFile, target: Path) -> int:
    try:
        upload.file.seek(0)
    except (AttributeError, OSError) as exc:
        raise HTTPException(400, "无法读取上传文件") from exc

    settings = get_settings()
    target.parent.mkdir(parents=True, exist_ok=True)
    if settings.data_encryption_enabled:
        try:
            return encrypt_stream_to_file(upload.file, target, settings.data_key, max_size=MAX_ATTACHMENT_FILE_SIZE)
        except CryptoError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(413, f"附件不能超过 {MAX_ATTACHMENT_FILE_SIZE // 1024 // 1024}MB") from exc

    total = 0
    temp_target = target.with_name(f".{target.name}.tmp")
    try:
        with temp_target.open("wb") as handle:
            while True:
                chunk = upload.file.read(ATTACHMENT_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ATTACHMENT_FILE_SIZE:
                    raise HTTPException(413, f"附件不能超过 {MAX_ATTACHMENT_FILE_SIZE // 1024 // 1024}MB")
                handle.write(chunk)
        temp_target.replace(target)
        return total
    except Exception:
        temp_target.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def get_attachment(
    db: Session,
    attachment_id: int,
    *,
    scope: SelectedPropertyScope,
    include_deleted: bool = False,
) -> Attachment:
    query = db.query(Attachment).filter(Attachment.id == attachment_id)
    if not include_deleted:
        query = query.filter(Attachment.deleted_at.is_(None))
    attachment = query.first()
    if not attachment:
        raise HTTPException(404, "附件不存在或无权访问")
    _validate_attachment_parent(db, attachment, scope)
    return attachment


def serialize_attachment(attachment: Attachment) -> dict:
    return {
        "id": attachment.id,
        "entity_type": attachment.entity_type or "",
        "entity_id": attachment.entity_id,
        "file_name": attachment.file_name or "",
        "content_type": attachment.content_type or "",
        "file_size": attachment.file_size or 0,
        "category": attachment.category or "其他",
        "notes": attachment.notes or "",
        "uploaded_by": attachment.uploaded_by or "",
        "created_at": attachment.created_at,
        "deleted_at": attachment.deleted_at,
    }


def list_attachments(
    db: Session,
    entity_type: str,
    entity_id: int,
    *,
    scope: SelectedPropertyScope,
) -> list[dict]:
    entity_type = _validate_entity_type(entity_type)
    entity_id = int(entity_id)
    parent = _resolve_entity_scope(db, entity_type, entity_id, scope)
    query = db.query(Attachment).filter(
        Attachment.entity_type == entity_type,
        Attachment.entity_id == entity_id,
        Attachment.scope_type == "building",
        Attachment.building_id.in_(scope.building_ids),
        Attachment.portfolio_id.is_(None),
        Attachment.deleted_at.is_(None),
    )
    if parent.building_id is not None:
        query = query.filter(Attachment.building_id == parent.building_id)
    rows = query.order_by(Attachment.created_at.desc(), Attachment.id.desc()).all()
    for item in rows:
        _validate_attachment_parent(db, item, scope)
    return [serialize_attachment(item) for item in rows]


def attachment_summary(attachments: list[dict], *, entity_type: str) -> dict:
    categories: dict[str, int] = {}
    for item in attachments:
        category = item.get("category") or "其他"
        categories[category] = categories.get(category, 0) + 1

    missing_required = []
    if entity_type == "contract" and categories.get("合同正文", 0) == 0:
        missing_required.append("合同正文")
    if entity_type == "customer" and categories.get("证照", 0) == 0:
        missing_required.append("证照")
    return {"total": len(attachments), "categories": categories, "missing_required": missing_required}


def _task_context(contract: Contract | None = None, customer: Customer | None = None) -> dict:
    summary = contract_room_summary(contract) if contract else {}
    return {
        "contract_id": contract.id if contract else None,
        "customer_id": (contract.customer_id if contract else None) or (customer.id if customer else None),
        "contract_no": contract.contract_no if contract else "",
        "customer_name": customer_display_name(contract) if contract else (customer.name if customer else ""),
        "room_no": summary.get("room_no", ""),
        "tower": summary.get("tower", ""),
    }


def _contract_attachment_task(contract: Contract) -> dict:
    context = _task_context(contract=contract)
    contract_label = context["contract_no"] or f"合同 {contract.id}"
    return {
        "id": f"attachment:contract:{contract.id}:body",
        "target_type": "contract",
        "target_id": contract.id,
        **context,
        "title": "缺少合同正文",
        "reason": f"{contract_label} 当前有效，但附件区未归档合同正文。",
        "severity": "medium",
        "priority": "medium",
        "missing_category": CONTRACT_BODY_CATEGORY,
        "suggested_action": "打开合同详情，在附件区以“合同正文”分类上传签署版文件。",
        "action_target": {"type": "contract", "contract_id": contract.id, "section": "attachments"},
    }


def _customer_attachment_task(customer_id: int, customer: Customer, building_code: str) -> dict:
    context = _task_context(customer=customer)
    customer_label = context["customer_name"] or f"客户 {customer_id}"
    return {
        "id": f"attachment:customer:{customer_id}:{building_code}:license",
        "target_type": "customer",
        "target_id": customer_id,
        **context,
        "title": "缺少客户证照",
        "reason": f"{customer_label} 在 {building_code} 有关联合同，但客户附件区未归档证照或营业执照。",
        "severity": "medium",
        "priority": "medium",
        "missing_category": CUSTOMER_LICENSE_CATEGORY,
        "suggested_action": "打开客户详情，在附件区以“证照”分类上传证照或营业执照。",
        "action_target": {
            "type": "customer",
            "customer_id": customer_id,
            "building": building_code,
            "section": "attachments",
        },
    }


def list_completeness_tasks(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    target_type: str | None = None,
    contract_id: int | None = None,
    customer_id: int | None = None,
    limit: int = 100,
) -> dict:
    selected_pairs = _selected_building_pairs(scope)
    selected_ids = tuple(building_id for building_id, _code in selected_pairs)
    building_codes = {building_id: code for building_id, code in selected_pairs}
    query = (
        db.query(Contract)
        .options(selectinload(Contract.customer), selectinload(Contract.room_items))
        .filter(
            Contract.is_deleted.is_(False),
            Contract.building_id.in_(selected_ids),
        )
    )
    if contract_id:
        query = query.filter(Contract.id == contract_id)
    if customer_id:
        query = query.filter(Contract.customer_id == customer_id)

    active_contracts = [contract for contract in query.all() if _is_currently_active(contract)]
    contract_ids = [contract.id for contract in active_contracts if contract.id]
    customer_map = {
        (int(contract.customer_id), int(contract.building_id)): getattr(contract, "customer", None)
        for contract in active_contracts
        if contract.customer_id and contract.building_id and getattr(contract, "customer", None)
    }

    attached_contract_keys: set[tuple[int, int]] = set()
    if contract_ids:
        attached_contract_keys = {
            (int(row[0]), int(row[1]))
            for row in db.query(Attachment.entity_id, Attachment.building_id)
            .filter(
                Attachment.entity_type == "contract",
                Attachment.entity_id.in_(contract_ids),
                Attachment.scope_type == "building",
                Attachment.building_id.in_(selected_ids),
                Attachment.portfolio_id.is_(None),
                Attachment.category == CONTRACT_BODY_CATEGORY,
                Attachment.deleted_at.is_(None),
            )
            .all()
        }

    tasks = []
    if target_type in (None, "", "contract"):
        for contract in active_contracts:
            contract_key = (int(contract.id), int(contract.building_id))
            if contract_key not in attached_contract_keys:
                tasks.append(_contract_attachment_task(contract))

    if target_type in (None, "", "customer") and customer_map:
        customer_ids = list({row_customer_id for row_customer_id, _building_id in customer_map})
        attached_customer_keys = {
            (int(row[0]), int(row[1]))
            for row in db.query(Attachment.entity_id, Attachment.building_id)
            .filter(
                Attachment.entity_type == "customer",
                Attachment.entity_id.in_(customer_ids),
                Attachment.scope_type == "building",
                Attachment.building_id.in_(selected_ids),
                Attachment.portfolio_id.is_(None),
                Attachment.category == CUSTOMER_LICENSE_CATEGORY,
                Attachment.deleted_at.is_(None),
            )
            .all()
        }
        for (row_customer_id, row_building_id), customer in customer_map.items():
            if (row_customer_id, row_building_id) not in attached_customer_keys:
                tasks.append(
                    _customer_attachment_task(
                        row_customer_id,
                        customer,
                        building_codes[row_building_id],
                    )
                )

    tasks.sort(key=lambda item: (item["target_type"], item["contract_no"] or item["customer_name"], item["id"]))
    summary = {
        "by_target_type": {
            "contract": sum(1 for item in tasks if item["target_type"] == "contract"),
            "customer": sum(1 for item in tasks if item["target_type"] == "customer"),
        },
        "by_severity": {
            "high": sum(1 for item in tasks if item["severity"] == "high"),
            "medium": sum(1 for item in tasks if item["severity"] == "medium"),
            "low": sum(1 for item in tasks if item["severity"] == "low"),
        },
    }
    return {"total": len(tasks), "summary": summary, "items": tasks[:limit]}


def create_attachment(
    db: Session,
    *,
    scope: SelectedPropertyScope,
    entity_type: str,
    entity_id: int,
    upload: UploadFile,
    category: str | None,
    notes: str | None,
    operator: str,
) -> dict:
    _require_edit_scope(scope)
    entity_type = _validate_entity_type(entity_type)
    entity_id = int(entity_id)
    building_id = _single_selected_building(scope)
    parent = _resolve_entity_scope(db, entity_type, entity_id, scope)
    if parent.building_id is not None and parent.building_id != building_id:
        raise HTTPException(404, "附件对象不存在或无权访问")
    category = _validate_category(category)
    file_name = _original_filename(upload)
    storage_path = _storage_path(entity_type, entity_id, file_name)
    target = _attachment_path(storage_path)
    size = _write_limited_upload(upload, target)

    attachment = Attachment(
        scope_type="building",
        portfolio_id=None,
        building_id=building_id,
        entity_type=entity_type,
        entity_id=entity_id,
        file_name=file_name,
        storage_path=storage_path,
        content_type=(upload.content_type or "application/octet-stream")[:120],
        file_size=size,
        category=category,
        notes=_clean_text(notes),
        uploaded_by=operator,
    )
    db.add(attachment)
    try:
        db.flush()
        _add_attachment_log(
            db,
            attachment,
            parent,
            "attachment_upload",
            dump_changes(
                {
                    "attachment_id": attachment.id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "file_name": file_name,
                    "category": category,
                }
            ),
            operator,
        )
        db.commit()
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    db.refresh(attachment)
    return serialize_attachment(attachment)


def update_attachment(
    db: Session,
    attachment: Attachment,
    payload: AttachmentUpdate,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    _require_edit_scope(scope)
    parent = _validate_attachment_parent(db, attachment, scope)
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    changes = {}
    if "category" in data:
        data["category"] = _validate_category(data.get("category"))
    if "notes" in data:
        data["notes"] = _clean_text(data.get("notes"))

    for key in ("category", "notes"):
        if key not in data:
            continue
        old_value = getattr(attachment, key) or ""
        new_value = data[key] or ""
        if str(old_value) != str(new_value):
            changes[key] = {"old": old_value, "new": new_value}
            setattr(attachment, key, new_value)

    if changes:
        _add_attachment_log(
            db,
            attachment,
            parent,
            "attachment_update",
            dump_changes({"attachment_id": attachment.id, "changes": changes}),
            operator,
        )
        db.commit()
        db.refresh(attachment)
    return serialize_attachment(attachment)


def delete_attachment(
    db: Session,
    attachment: Attachment,
    operator: str,
    *,
    scope: SelectedPropertyScope,
) -> dict:
    _require_edit_scope(scope)
    parent = _validate_attachment_parent(db, attachment, scope)
    if attachment.deleted_at is None:
        attachment.deleted_at = now_in_timezone()
        _add_attachment_log(
            db,
            attachment,
            parent,
            "attachment_delete",
            dump_changes(
                {
                    "attachment_id": attachment.id,
                    "entity_type": attachment.entity_type,
                    "entity_id": attachment.entity_id,
                    "file_name": attachment.file_name,
                }
            ),
            operator,
        )
        db.commit()
    return {"ok": True, "id": attachment.id}


def download_attachment(attachment: Attachment, *, scope: SelectedPropertyScope) -> FileResponse:
    _assert_attachment_scope(attachment, scope)
    file_path = _attachment_path(attachment.storage_path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "文件不存在")

    media_type = attachment.content_type or "application/octet-stream"
    if is_encrypted_file(file_path):
        settings = get_settings()
        if not settings.data_encryption_enabled:
            raise HTTPException(500, "附件已加密，但当前未启用数据加密配置")
        temp_dir = settings.data_dir / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        suffix = _safe_suffix(attachment.file_name)
        temp = tempfile.NamedTemporaryFile(delete=False, dir=temp_dir, suffix=suffix)
        temp_path = Path(temp.name)
        temp.close()
        try:
            decrypt_file(file_path, temp_path, settings.data_key)
        except CryptoError as exc:
            temp_path.unlink(missing_ok=True)
            raise HTTPException(500, "附件解密失败，请检查数据加密密钥") from exc
        return FileResponse(
            temp_path,
            filename=attachment.file_name,
            media_type=media_type,
            background=BackgroundTask(lambda path: Path(path).unlink(missing_ok=True), temp_path),
        )

    return FileResponse(file_path, filename=attachment.file_name, media_type=media_type)
