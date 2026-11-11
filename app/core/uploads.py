from __future__ import annotations

from fastapi import HTTPException, UploadFile

from app.core.config import get_settings


UPLOAD_READ_CHUNK_SIZE = 1024 * 1024


async def read_import_upload(upload: UploadFile) -> bytes:
    """Read a spreadsheet upload with a strict in-memory size limit."""
    limit = get_settings().max_import_file_size_bytes
    declared_size = getattr(upload, "size", None)
    if declared_size is not None and declared_size > limit:
        raise HTTPException(413, f"导入文件不能超过 {limit // 1024 // 1024}MB")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, f"导入文件不能超过 {limit // 1024 // 1024}MB")
        chunks.append(chunk)
    return b"".join(chunks)
