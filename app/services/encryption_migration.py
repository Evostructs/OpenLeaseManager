from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.core.crypto import encrypt_file, encrypted_name, is_encrypted_file
from app.database import SessionLocal
from app.models import Contract


def _plaintext_pdf_files(upload_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(upload_dir.glob("*.pdf")):
        if path.is_file() and not is_encrypted_file(path):
            files.append(path)
    return files


def migrate_plaintext_pdfs_at_startup() -> dict:
    settings = get_settings()
    if not settings.data_encryption_enabled:
        return {"enabled": False, "migrated": 0}

    plaintext_files = _plaintext_pdf_files(settings.upload_dir)
    if not plaintext_files:
        _repair_contract_pdf_paths()
        return {"enabled": True, "migrated": 0}

    # The backup archive itself is encrypted by create_backup_archive when data encryption is enabled.
    from app.services.backup import create_backup_archive

    safety_backup = create_backup_archive()
    result = encrypt_plaintext_pdfs_without_backup()
    result["safety_backup"] = safety_backup.get("filename")
    return result


def encrypt_plaintext_pdfs_without_backup() -> dict:
    settings = get_settings()
    if not settings.data_encryption_enabled:
        return {"enabled": False, "migrated": 0}

    plaintext_files = _plaintext_pdf_files(settings.upload_dir)
    if not plaintext_files:
        _repair_contract_pdf_paths()
        return {"enabled": True, "migrated": 0}

    migrated = 0
    key = settings.data_key

    db = SessionLocal()
    try:
        for source in plaintext_files:
            target_name = encrypted_name(source.name)
            target = source.with_name(target_name)
            if target.exists():
                if not is_encrypted_file(target):
                    raise RuntimeError(f"Encrypted target already exists but is not valid: {target.name}")
            else:
                encrypt_file(source, target, key)

            db.query(Contract).filter(Contract.pdf_path == source.name).update({"pdf_path": target_name})
            source.unlink(missing_ok=True)
            migrated += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    _repair_contract_pdf_paths()
    return {"enabled": True, "migrated": migrated}


def _repair_contract_pdf_paths() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        changed = False
        rows = db.query(Contract).filter(Contract.pdf_path.isnot(None), Contract.pdf_path != "").all()
        for contract in rows:
            current = settings.upload_dir / contract.pdf_path
            encrypted = settings.upload_dir / encrypted_name(contract.pdf_path)
            if not current.exists() and encrypted.exists() and is_encrypted_file(encrypted):
                contract.pdf_path = encrypted.name
                changed = True
        if changed:
            db.commit()
    finally:
        db.close()
