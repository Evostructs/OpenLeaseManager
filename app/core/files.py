from __future__ import annotations

from pathlib import Path
import re

from fastapi import HTTPException, status


SAFE_FILE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_stem(value: str, fallback: str) -> str:
    cleaned = SAFE_FILE_RE.sub("_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def ensure_pdf_filename(name: str, fallback_stem: str) -> str:
    suffix = Path(name or "").suffix.lower()
    if suffix != ".pdf":
        suffix = ".pdf"
    stem = sanitize_stem(Path(name or "").stem, fallback_stem)
    return f"{stem}{suffix}"


def ensure_safe_backup_name(name: str) -> str:
    candidate = Path(name).name
    if candidate != name or not (candidate.endswith(".zip") or candidate.endswith(".zip.enc")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="非法备份文件名")
    return candidate


def ensure_child_path(parent: Path, child_name: str) -> Path:
    target = (parent / child_name).resolve()
    if parent.resolve() not in target.parents and target != parent.resolve():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="非法文件路径")
    return target
