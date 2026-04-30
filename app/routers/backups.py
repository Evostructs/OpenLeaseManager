from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.access import require_admin
from app.services.backup import create_backup_archive, get_backup_file, list_backup_archives, restore_backup_archive


router = APIRouter(prefix="/api/backups", tags=["backups"], dependencies=[Depends(require_admin)])


@router.post("")
def create_backup():
    return create_backup_archive()


@router.get("")
def list_backups():
    return list_backup_archives()


@router.get("/{filename}")
def download_backup(filename: str):
    try:
        target = get_backup_file(filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, "备份文件不存在") from exc
    return FileResponse(target, filename=target.name)


@router.post("/{filename}/restore")
def restore_backup(filename: str):
    try:
        return restore_backup_archive(filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, "备份文件不存在") from exc
