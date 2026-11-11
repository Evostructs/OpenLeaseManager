from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.services.property_scope import require_platform_admin
from app.database import get_db
from app.services.common import get_operator, log_action
from sqlalchemy.orm import Session
from app.services.backup import create_backup_archive, get_backup_file, list_backup_archives, restore_backup_archive


router = APIRouter(prefix="/api/backups", tags=["backups"], dependencies=[Depends(require_platform_admin)])


@router.post("")
def create_backup():
    return create_backup_archive()


@router.get("")
def list_backups():
    return list_backup_archives()


@router.get("/{filename}")
def download_backup(filename: str, request: Request, db: Session = Depends(get_db)):
    try:
        target = get_backup_file(filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, "备份文件不存在") from exc
    log_action(db, None, "download", f"下载备份 {target.name}", get_operator(request), target_type="backup")
    return FileResponse(target, filename=target.name)


@router.post("/{filename}/restore")
def restore_backup(filename: str):
    try:
        return restore_backup_archive(filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, "备份文件不存在") from exc
