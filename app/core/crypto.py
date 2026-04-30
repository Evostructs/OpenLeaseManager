from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"LEASE-MANAGER-ENC-v1\n"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


class CryptoError(RuntimeError):
    pass


def decode_key(raw_key: str) -> bytes:
    value = (raw_key or "").strip()
    if not value:
        raise CryptoError("DATA_ENCRYPTION_KEY is required when encryption is enabled.")
    padding = "=" * (-len(value) % 4)
    try:
        key = base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except Exception as exc:
        raise CryptoError("DATA_ENCRYPTION_KEY must be a base64 encoded 32-byte key.") from exc
    if len(key) != 32:
        raise CryptoError("DATA_ENCRYPTION_KEY must decode to exactly 32 bytes.")
    return key


def is_encrypted_name(filename: str) -> bool:
    return filename.lower().endswith(".enc")


def encrypted_name(filename: str) -> str:
    return filename if is_encrypted_name(filename) else f"{filename}.enc"


def public_name(filename: str) -> str:
    return filename[:-4] if is_encrypted_name(filename) else filename


def is_encrypted_file(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    with path.open("rb") as handle:
        return handle.read(len(MAGIC)) == MAGIC


def encrypt_stream_to_file(source, target: Path, key: bytes, *, max_size: int | None = None) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(NONCE_SIZE)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce))
    encryptor = cipher.encryptor()
    total = 0
    temp_target = target.with_name(f".{target.name}.tmp")

    try:
        with temp_target.open("wb") as output:
            output.write(MAGIC)
            output.write(nonce)
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if max_size is not None and total > max_size:
                    raise CryptoError("File exceeds the configured size limit.")
                output.write(encryptor.update(chunk))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
        temp_target.replace(target)
        return total
    except Exception:
        temp_target.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def encrypt_file(source: Path, target: Path, key: bytes, *, max_size: int | None = None) -> int:
    with source.open("rb") as handle:
        return encrypt_stream_to_file(handle, target, key, max_size=max_size)


def decrypt_file(source: Path, target: Path, key: bytes) -> None:
    size = source.stat().st_size
    min_size = len(MAGIC) + NONCE_SIZE + TAG_SIZE
    if size < min_size:
        raise CryptoError("Encrypted file is truncated.")

    with source.open("rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise CryptoError("File is not encrypted with the expected format.")
        nonce = encrypted.read(NONCE_SIZE)
        encrypted.seek(size - TAG_SIZE)
        tag = encrypted.read(TAG_SIZE)
        ciphertext_size = size - len(MAGIC) - NONCE_SIZE - TAG_SIZE
        encrypted.seek(len(MAGIC) + NONCE_SIZE)

        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag))
        decryptor = cipher.decryptor()
        temp_target = target.with_name(f".{target.name}.tmp")

        try:
            with temp_target.open("wb") as output:
                remaining = ciphertext_size
                while remaining > 0:
                    chunk = encrypted.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise CryptoError("Encrypted file ended unexpectedly.")
                    remaining -= len(chunk)
                    output.write(decryptor.update(chunk))
                output.write(decryptor.finalize())
            temp_target.replace(target)
        except InvalidTag as exc:
            temp_target.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise CryptoError("Encrypted file cannot be decrypted with the configured key.") from exc
        except Exception:
            temp_target.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
