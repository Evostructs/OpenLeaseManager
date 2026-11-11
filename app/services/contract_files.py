from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.crypto import (
    CryptoError,
    decrypt_file,
    encrypt_file,
    encrypt_stream_to_file,
    encrypted_name,
    is_encrypted_file,
    public_name,
)
from app.core.files import ensure_child_path, ensure_pdf_filename, sanitize_stem
from app.core.time import now_in_timezone, today_in_timezone
from app.models import Contract, ContractRoom, Room
from app.services.common import contract_room_items, customer_display_name, log_action, serialize_contract
from app.services.detail_operations_scope import require_detail_write_scope
from app.services.legacy_demo_a_scope import ObjectScope
from app.services.property_scope import SelectedPropertyScope


PDF_CHUNK_SIZE = 1024 * 1024
MAX_PDF_FILE_SIZE = 50 * 1024 * 1024
MAX_BATCH_PDF_COUNT = 20
MAX_BATCH_PDF_TOTAL_SIZE = 200 * 1024 * 1024
CONTRACT_PDF_STORAGE_PREFIX = "_contract_pdf__"
UNMATCHED_STORAGE_TOKEN_LENGTH = 32
PDF_DISPLAY_STEM_MAX_LENGTH = 120


def _selected_file_buildings(scope: SelectedPropertyScope) -> tuple[tuple[int, str], ...]:
    if (
        scope.scope_type not in {"building", "portfolio"}
        or scope.access_level not in {"view", "edit"}
        or not str(scope.scope_code or "").strip()
        or not scope.building_ids
        or len(scope.building_ids) != len(scope.building_codes)
    ):
        raise HTTPException(403, "当前楼宇范围无效")
    try:
        pairs = tuple(
            (int(building_id), str(code).strip())
            for building_id, code in zip(scope.building_ids, scope.building_codes)
        )
        portfolio_id = int(scope.portfolio_id) if scope.portfolio_id is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "当前楼宇范围无效") from exc
    if (
        any(building_id <= 0 or not code for building_id, code in pairs)
        or len({building_id for building_id, _code in pairs}) != len(pairs)
        or len({code.casefold() for _building_id, code in pairs}) != len(pairs)
        or (
            scope.scope_type == "building"
            and (
                len(pairs) != 1
                or portfolio_id is not None
                or str(scope.scope_code).strip().casefold() != pairs[0][1].casefold()
            )
        )
        or (scope.scope_type == "portfolio" and (portfolio_id is None or portfolio_id <= 0))
    ):
        raise HTTPException(403, "当前楼宇范围无效")
    return pairs


def _require_file_write_scope(
    db: Session,
    scope: SelectedPropertyScope,
) -> tuple[int, str]:
    detail_scope = require_detail_write_scope(db, scope, "contracts")
    return detail_scope.building_id, detail_scope.building_code


def _contract_log_scope(contract: Contract) -> ObjectScope:
    if contract.building_id is None:
        raise HTTPException(409, "合同尚未明确楼宇归属")
    return ObjectScope(
        scope_type="building",
        building_id=int(contract.building_id),
    )


def _assert_contract_file_scope(contract: Contract, scope: SelectedPropertyScope) -> int:
    selected_ids = {building_id for building_id, _code in _selected_file_buildings(scope)}
    if contract.building_id is None:
        raise HTTPException(409, "合同尚未明确楼宇归属")
    building_id = int(contract.building_id)
    if building_id not in selected_ids:
        raise HTTPException(404, "合同不存在或无权访问")
    return building_id


def _assert_contract_file_graphs(db: Session, contracts: list[Contract]) -> None:
    expected_buildings = {
        int(contract.id): int(contract.building_id)
        for contract in contracts
        if contract.id is not None and contract.building_id is not None
    }
    if not expected_buildings:
        return
    room_items = (
        db.query(ContractRoom)
        .filter(ContractRoom.contract_id.in_(tuple(expected_buildings)))
        .all()
    )
    for item in room_items:
        if item.building_id is None or int(item.building_id) != expected_buildings[int(item.contract_id)]:
            raise HTTPException(409, "合同与房源明细楼宇不一致")
    linked_room_ids = {int(item.room_id) for item in room_items if item.room_id is not None}
    linked_rooms = (
        {
            int(room.id): room
            for room in db.query(Room).filter(Room.id.in_(tuple(linked_room_ids))).all()
        }
        if linked_room_ids
        else {}
    )
    for item in room_items:
        if item.room_id is None:
            continue
        room = linked_rooms.get(int(item.room_id))
        expected_building_id = expected_buildings[int(item.contract_id)]
        if not room or room.is_deleted or room.building_id is None or int(room.building_id) != expected_building_id:
            raise HTTPException(409, "合同房源明细与房源台账楼宇不一致")


def _unmatched_scope_prefix(building_code: str) -> str:
    return f"_unmatched_scope_{sanitize_stem(building_code, 'building')}__"


def _scoped_unmatched_name(building_code: str, filename: str) -> str:
    return f"{_unmatched_scope_prefix(building_code)}{filename}"


@dataclass(frozen=True)
class _InstalledFile:
    target: Path
    backup: Path | None


@dataclass(frozen=True)
class _MovedSource:
    source: Path
    backup: Path


def _new_staging_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        delete=False,
        dir=target.parent,
        prefix=".pdf-stage-",
        suffix=".tmp",
    )
    path = Path(temporary.name)
    temporary.close()
    return path


def _new_rollback_path(path: Path) -> Path:
    while True:
        candidate = path.parent / f".pdf-rollback-{uuid4().hex}"
        if not candidate.exists():
            return candidate


def _install_staged_file(staged: Path, target: Path) -> _InstalledFile:
    backup = None
    try:
        if target.exists():
            backup = _new_rollback_path(target)
            target.replace(backup)
        staged.replace(target)
        return _InstalledFile(target=target, backup=backup)
    except Exception:
        if backup is not None and backup.exists():
            if target.exists():
                target.unlink()
            backup.replace(target)
        raise


def _hide_source_file(source: Path) -> _MovedSource:
    backup = _new_rollback_path(source)
    source.replace(backup)
    return _MovedSource(source=source, backup=backup)


def _restore_file_changes(
    installed: list[_InstalledFile],
    moved: list[_MovedSource] | None = None,
) -> None:
    errors: list[Exception] = []
    for item in reversed(moved or []):
        try:
            if item.source.exists():
                raise RuntimeError(f"源文件位置已被占用: {item.source.name}")
            if item.backup.exists():
                item.backup.replace(item.source)
        except Exception as exc:
            errors.append(exc)
    for item in reversed(installed):
        try:
            if item.target.exists():
                item.target.unlink()
            if item.backup is not None and item.backup.exists():
                item.backup.replace(item.target)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError("PDF 文件回滚未完整完成，需要检查隐藏回滚文件") from errors[0]


def _discard_rollback_files(
    installed: list[_InstalledFile],
    moved: list[_MovedSource] | None = None,
) -> None:
    rollback_paths = [item.backup for item in installed if item.backup is not None]
    rollback_paths.extend(item.backup for item in moved or [])
    for path in rollback_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # The committed DB and active PDF are already consistent. Keeping a
            # hidden rollback copy is safer than turning cleanup into a false failure.
            pass


def _cleanup_staging_files(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _contract_room_numbers(contract: Contract) -> list[str]:
    items = contract_room_items(contract)
    return [item["room_no"] for item in items if item.get("room_no")]


def _safe_upload_path(filename: str) -> Path:
    return ensure_child_path(get_settings().upload_dir, filename)


def _stored_pdf_name(filename: str) -> str:
    settings = get_settings()
    return encrypted_name(filename) if settings.data_encryption_enabled else filename


def _safe_pdf_display_name(filename: str, fallback_stem: str) -> str:
    safe_name = ensure_pdf_filename(filename, fallback_stem)
    stem = sanitize_stem(Path(safe_name).stem, fallback_stem)
    stem = stem[:PDF_DISPLAY_STEM_MAX_LENGTH].rstrip("._-") or fallback_stem
    return f"{stem}.pdf"


def _contract_pdf_display_name(stored_path: str) -> str:
    leaf = public_name(Path(stored_path).name)
    if leaf.startswith(CONTRACT_PDF_STORAGE_PREFIX):
        parts = leaf.split("__", 4)
        storage_token = parts[3] if len(parts) == 5 else ""
        if (
            len(parts) == 5
            and parts[1].startswith("b")
            and parts[1][1:].isdigit()
            and parts[2].startswith("c")
            and parts[2][1:].isdigit()
            and len(storage_token) == UNMATCHED_STORAGE_TOKEN_LENGTH
            and all(character in "0123456789abcdef" for character in storage_token.casefold())
            and parts[4]
        ):
            return parts[4]
    return leaf


def _strip_unmatched_storage_token(value: str) -> str:
    token, separator, filename = value.partition("__")
    if (
        separator
        and len(token) == UNMATCHED_STORAGE_TOKEN_LENGTH
        and all(character in "0123456789abcdef" for character in token.casefold())
        and filename
    ):
        return filename
    return value


def _unmatched_display_name(path: Path, building_code: str) -> str:
    stored_leaf = path.name.removeprefix(_unmatched_scope_prefix(building_code))
    return _strip_unmatched_storage_token(public_name(stored_leaf))


def _unmatched_paths(building_code: str, filename: str) -> list[Path]:
    safe_name = _safe_pdf_display_name(filename, Path(filename).stem or "pdf")
    prefix = _unmatched_scope_prefix(building_code)
    candidates = []
    for path in get_settings().upload_dir.glob(f"{prefix}*.pdf*"):
        if path.is_file() and _unmatched_display_name(path, building_code) == safe_name:
            candidates.append(path)
    return sorted(set(candidates))


def _storage_path_is_referenced(
    db: Session,
    stored_path: str,
    *,
    exclude_contract_id: int | None = None,
) -> bool:
    query = db.query(Contract.id).filter(Contract.pdf_path == stored_path)
    if exclude_contract_id is not None:
        query = query.filter(Contract.id != exclude_contract_id)
    return query.first() is not None


def _allocate_contract_pdf_storage(
    db: Session,
    *,
    building_id: int,
    contract_id: int,
    filename: str,
) -> tuple[str, Path, str]:
    display_name = _safe_pdf_display_name(filename, f"contract-{contract_id}")
    for _attempt in range(5):
        leaf = (
            f"{CONTRACT_PDF_STORAGE_PREFIX}"
            f"b{building_id}__c{contract_id}__{uuid4().hex}__{display_name}"
        )
        stored_path = _stored_pdf_name(leaf)
        target = _safe_upload_path(stored_path)
        if not target.exists() and not _storage_path_is_referenced(
            db,
            stored_path,
            exclude_contract_id=contract_id,
        ):
            return stored_path, target, display_name
    raise HTTPException(409, "PDF 存储位置冲突，当前上传已停止")


def _allocate_unmatched_pdf_storage(
    db: Session,
    *,
    building_code: str,
    filename: str,
) -> tuple[str, Path, str]:
    display_name = _safe_pdf_display_name(filename, Path(filename).stem or "pdf")
    if _unmatched_paths(building_code, display_name):
        raise HTTPException(409, "当前楼宇已有同名未匹配 PDF")
    for _attempt in range(5):
        leaf = f"{_unmatched_scope_prefix(building_code)}{uuid4().hex}__{display_name}"
        stored_path = _stored_pdf_name(leaf)
        target = _safe_upload_path(stored_path)
        if not target.exists() and not _storage_path_is_referenced(db, stored_path):
            return stored_path, target, display_name
    raise HTTPException(409, "PDF 存储位置冲突，当前上传已停止")


def _cleanup_replaced_contract_pdf(
    db: Session,
    *,
    contract_id: int,
    old_stored_path: str | None,
    new_stored_path: str,
) -> None:
    if not old_stored_path or old_stored_path == new_stored_path:
        return
    try:
        if _storage_path_is_referenced(
            db,
            old_stored_path,
            exclude_contract_id=contract_id,
        ):
            return
        _safe_upload_path(old_stored_path).unlink(missing_ok=True)
    except (HTTPException, OSError):
        # 数据库已经指向新文件。旧文件清理失败时宁可保留孤立副本，
        # 也不能破坏刚完成的上传或误删仍在使用的历史共享路径。
        return


def _ensure_pdf_upload_name(upload: UploadFile) -> str:
    raw_name = upload.filename or ""
    if Path(raw_name).suffix.lower() != ".pdf":
        raise HTTPException(400, "请上传 PDF 文件")
    return raw_name


def _write_limited_upload(upload: UploadFile, target: Path, *, max_size: int | None = None) -> int:
    max_size = max_size or MAX_PDF_FILE_SIZE
    try:
        upload.file.seek(0)
    except (AttributeError, OSError) as exc:
        raise HTTPException(400, "无法读取上传文件") from exc

    settings = get_settings()
    if settings.data_encryption_enabled:
        try:
            return encrypt_stream_to_file(upload.file, target, settings.data_key, max_size=max_size)
        except CryptoError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(413, f"PDF 文件不能超过 {max_size // 1024 // 1024}MB") from exc

    total = 0
    with target.open("wb") as handle:
        while True:
            chunk = upload.file.read(PDF_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, f"PDF 文件不能超过 {max_size // 1024 // 1024}MB")
            handle.write(chunk)
    return total


def save_contract_pdf(
    db: Session,
    contract: Contract,
    upload: UploadFile,
    operator: str,
    *,
    scope: SelectedPropertyScope,
    max_file_size: int = MAX_PDF_FILE_SIZE,
) -> dict:
    selected_building_id, _building_code = _require_file_write_scope(db, scope)
    if _assert_contract_file_scope(contract, scope) != selected_building_id:
        raise HTTPException(404, "合同不存在或无权访问")
    _assert_contract_file_graphs(db, [contract])
    if contract.id is None:
        raise HTTPException(409, "合同尚未持久化，当前上传已停止")
    raw_name = _ensure_pdf_upload_name(upload)
    stored_name, target, filename = _allocate_contract_pdf_storage(
        db,
        building_id=selected_building_id,
        contract_id=int(contract.id),
        filename=raw_name,
    )
    old_stored_path = str(contract.pdf_path or "")
    staged_paths: list[Path] = []
    installed: list[_InstalledFile] = []
    try:
        staged = _new_staging_path(target)
        staged_paths.append(staged)
        _write_limited_upload(upload, staged, max_size=max_file_size)

        contract.pdf_path = stored_name
        contract.updated_at = now_in_timezone()
        log_action(
            db,
            contract.id,
            "update",
            f"上传PDF: {filename}",
            operator,
            commit=False,
            object_scope=_contract_log_scope(contract),
        )
        installed.append(_install_staged_file(staged, target))
        db.commit()
    except Exception:
        try:
            db.rollback()
        finally:
            _restore_file_changes(installed)
        raise
    else:
        _discard_rollback_files(installed)
        _cleanup_replaced_contract_pdf(
            db,
            contract_id=int(contract.id),
            old_stored_path=old_stored_path,
            new_stored_path=stored_name,
        )
    finally:
        _cleanup_staging_files(staged_paths)
    return {"message": "上传成功", "filename": filename}


def download_contract_pdf(contract: Contract, *, scope: SelectedPropertyScope) -> FileResponse:
    _assert_contract_file_scope(contract, scope)
    if not contract.pdf_path:
        raise HTTPException(404, "PDF不存在")
    file_path = _safe_upload_path(contract.pdf_path)
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")

    if is_encrypted_file(file_path):
        settings = get_settings()
        if not settings.data_encryption_enabled:
            raise HTTPException(500, "PDF 已加密，但当前未启用数据加密配置")
        temp_dir = settings.data_dir / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp = tempfile.NamedTemporaryFile(delete=False, dir=temp_dir, suffix=".pdf")
        temp_path = Path(temp.name)
        temp.close()
        try:
            decrypt_file(file_path, temp_path, settings.data_key)
        except CryptoError as exc:
            temp_path.unlink(missing_ok=True)
            raise HTTPException(500, "PDF 解密失败，请检查数据加密密钥") from exc
        return FileResponse(
            temp_path,
            filename=_contract_pdf_display_name(contract.pdf_path),
            media_type="application/pdf",
            background=BackgroundTask(lambda path: Path(path).unlink(missing_ok=True), temp_path),
        )

    return FileResponse(
        file_path,
        filename=_contract_pdf_display_name(contract.pdf_path),
        media_type="application/pdf",
    )


def batch_upload_pdfs(
    db: Session,
    uploads: list[UploadFile],
    operator: str,
    *,
    scope: SelectedPropertyScope,
    max_file_size: int = MAX_PDF_FILE_SIZE,
    max_batch_count: int = MAX_BATCH_PDF_COUNT,
    max_total_size: int = MAX_BATCH_PDF_TOTAL_SIZE,
) -> dict:
    selected_building_id, building_code = _require_file_write_scope(db, scope)
    if len(uploads) > max_batch_count:
        raise HTTPException(413, f"单次最多上传 {max_batch_count} 个 PDF")
    upload_names: list[tuple[UploadFile, str]] = []
    seen_upload_names: set[str] = set()
    for upload in uploads:
        if upload.filename:
            filename = _ensure_pdf_upload_name(upload)
            duplicate_key = _safe_pdf_display_name(
                filename,
                Path(filename).stem or "pdf",
            ).casefold()
            if duplicate_key in seen_upload_names:
                raise HTTPException(400, f"同一批次 PDF 文件名重复: {filename}")
            seen_upload_names.add(duplicate_key)
            upload_names.append((upload, filename))

    contracts = db.query(Contract).filter(
        Contract.building_id == selected_building_id,
        Contract.is_deleted.is_(False),
    ).all()
    plans: list[dict] = []
    for upload, filename in upload_names:
        basename = Path(filename).stem
        best_match = None
        best_score = 0
        for contract in contracts:
            tenant_name = customer_display_name(contract).strip()
            if tenant_name and tenant_name in basename:
                score = len(tenant_name)
                if score > best_score:
                    best_match = contract
                    best_score = score
            if contract.contract_no and contract.contract_no in basename:
                score = len(contract.contract_no) + 1000
                if score > best_score:
                    best_match = contract
                    best_score = score
            for room_no in _contract_room_numbers(contract):
                if room_no in basename and len(room_no) >= 3:
                    score = len(room_no)
                    if score > best_score:
                        best_match = contract
                        best_score = score

        if best_match:
            if best_match.id is None:
                raise HTTPException(409, "合同尚未持久化，当前上传已停止")
            save_name, target, _display_name = _allocate_contract_pdf_storage(
                db,
                building_id=selected_building_id,
                contract_id=int(best_match.id),
                filename=filename,
            )
            plans.append(
                {
                    "upload": upload,
                    "filename": filename,
                    "contract": best_match,
                    "stored_name": save_name,
                    "target": target,
                    "old_stored_path": str(best_match.pdf_path or ""),
                }
            )
        else:
            stored_name, target, _display_name = _allocate_unmatched_pdf_storage(
                db,
                building_code=building_code,
                filename=filename,
            )
            plans.append(
                {
                    "upload": upload,
                    "filename": filename,
                    "contract": None,
                    "stored_name": stored_name,
                    "target": target,
                }
            )

    matched_contract_ids = [
        int(plan["contract"].id)
        for plan in plans
        if plan["contract"] is not None
    ]
    if len(matched_contract_ids) != len(set(matched_contract_ids)):
        raise HTTPException(400, "同一批次每份合同只能匹配一个 PDF")

    staged_paths: list[Path] = []
    installed: list[_InstalledFile] = []
    try:
        # Validate every matched contract before writing even a staging file.
        prepared_contract_ids: set[int] = set()
        for plan in plans:
            contract = plan["contract"]
            if contract is None or contract.id in prepared_contract_ids:
                continue
            if _assert_contract_file_scope(contract, scope) != selected_building_id:
                raise HTTPException(404, "合同不存在或无权访问")
            prepared_contract_ids.add(contract.id)
        _assert_contract_file_graphs(
            db,
            [plan["contract"] for plan in plans if plan["contract"] is not None],
        )

        total_bytes = 0
        for plan in plans:
            staged = _new_staging_path(plan["target"])
            staged_paths.append(staged)
            plan["staged"] = staged
            total_bytes += _write_limited_upload(
                plan["upload"],
                staged,
                max_size=max_file_size,
            )
            if total_bytes > max_total_size:
                raise HTTPException(
                    413,
                    f"单次上传总大小不能超过 {max_total_size // 1024 // 1024}MB",
                )

        matched = []
        unmatched = []
        for plan in plans:
            contract = plan["contract"]
            filename = plan["filename"]
            if contract is None:
                unmatched.append({"filename": filename})
                continue
            contract.pdf_path = plan["stored_name"]
            contract.updated_at = now_in_timezone()
            log_action(
                db,
                contract.id,
                "update",
                f"批量上传PDF匹配: {filename} → {contract.contract_no}",
                operator,
                commit=False,
                object_scope=_contract_log_scope(contract),
            )
            matched.append(
                {
                    "filename": filename,
                    "contract_no": contract.contract_no,
                    "tenant_name": customer_display_name(contract),
                    "room_no": contract.room_no,
                }
            )

        for plan in plans:
            installed.append(
                _install_staged_file(plan["staged"], plan["target"])
            )
        db.commit()
    except Exception:
        try:
            db.rollback()
        finally:
            _restore_file_changes(installed)
        raise
    else:
        _discard_rollback_files(installed)
        for plan in plans:
            contract = plan["contract"]
            if contract is None:
                continue
            _cleanup_replaced_contract_pdf(
                db,
                contract_id=int(contract.id),
                old_stored_path=plan.get("old_stored_path"),
                new_stored_path=plan["stored_name"],
            )
    finally:
        _cleanup_staging_files(staged_paths)

    return {
        "total": len(uploads),
        "matched": matched,
        "matched_count": len(matched),
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
    }


def manual_match_pdf(
    db: Session,
    filename: str,
    contract: Contract,
    operator: str,
    *,
    scope: SelectedPropertyScope,
    max_file_size: int = MAX_PDF_FILE_SIZE,
) -> dict:
    selected_building_id, building_code = _require_file_write_scope(db, scope)
    if _assert_contract_file_scope(contract, scope) != selected_building_id:
        raise HTTPException(404, "合同不存在或无权访问")
    _assert_contract_file_graphs(db, [contract])
    safe_name = _safe_pdf_display_name(filename, Path(filename).stem or "pdf")
    unmatched_paths = _unmatched_paths(building_code, safe_name)
    if not unmatched_paths:
        raise HTTPException(404, "未匹配文件不存在")
    if len(unmatched_paths) > 1:
        raise HTTPException(409, "同名未匹配 PDF 不唯一，当前匹配已停止")
    temp_path = unmatched_paths[0]

    if contract.id is None:
        raise HTTPException(409, "合同尚未持久化，当前匹配已停止")
    final_name, final_path, _display_name = _allocate_contract_pdf_storage(
        db,
        building_id=selected_building_id,
        contract_id=int(contract.id),
        filename=safe_name,
    )
    old_stored_path = str(contract.pdf_path or "")
    settings = get_settings()
    staged_paths: list[Path] = []
    installed: list[_InstalledFile] = []
    moved_sources: list[_MovedSource] = []
    try:
        staged = _new_staging_path(final_path)
        staged_paths.append(staged)
        if settings.data_encryption_enabled and not is_encrypted_file(temp_path):
            encrypt_file(temp_path, staged, settings.data_key, max_size=max_file_size)
        else:
            shutil.copyfile(temp_path, staged)

        contract.pdf_path = final_name
        contract.updated_at = now_in_timezone()
        log_action(
            db,
            contract.id,
            "update",
            f"手动匹配PDF: {filename} → {contract.contract_no}",
            operator,
            commit=False,
            object_scope=_contract_log_scope(contract),
        )
        installed.append(_install_staged_file(staged, final_path))
        moved_sources.append(_hide_source_file(temp_path))
        db.commit()
    except Exception:
        try:
            db.rollback()
        finally:
            _restore_file_changes(installed, moved_sources)
        raise
    else:
        _discard_rollback_files(installed, moved_sources)
        _cleanup_replaced_contract_pdf(
            db,
            contract_id=int(contract.id),
            old_stored_path=old_stored_path,
            new_stored_path=final_name,
        )
    finally:
        _cleanup_staging_files(staged_paths)
    return {"message": "匹配成功", "contract_no": contract.contract_no}


def list_unmatched_pdfs(*, scope: SelectedPropertyScope) -> list[dict]:
    selected_pairs = _selected_file_buildings(scope)
    items = []
    seen = set()
    for _building_id, building_code in selected_pairs:
        prefix = _unmatched_scope_prefix(building_code)
        for path in sorted(get_settings().upload_dir.glob(f"{prefix}*.pdf*")):
            filename = _unmatched_display_name(path, building_code)
            key = (building_code.casefold(), filename)
            if key in seen:
                continue
            seen.add(key)
            item = {"filename": filename, "size": path.stat().st_size}
            if len(selected_pairs) > 1:
                item["building"] = building_code
            items.append(item)
    return items


def list_missing_contract_pdfs(db: Session, *, scope: SelectedPropertyScope) -> list[dict]:
    selected_ids = tuple(building_id for building_id, _code in _selected_file_buildings(scope))
    today = today_in_timezone()
    contracts = (
        db.query(Contract)
        .filter(
            Contract.is_deleted.is_(False),
            Contract.building_id.in_(selected_ids),
            or_(Contract.pdf_path.is_(None), Contract.pdf_path == ""),
            or_(
                Contract.start_date > today,
                and_(Contract.start_date <= today, Contract.end_date >= today),
                and_(Contract.end_date < today, Contract.disposal == "逾期占用"),
            ),
        )
        .order_by(Contract.end_date.asc())
        .all()
    )
    _assert_contract_file_graphs(db, contracts)
    return [serialize_contract(contract) for contract in contracts]
