"""Private encrypted storage for order vouchers."""

import os
from pathlib import Path
import re
import secrets

from tax_manager.security.crypto import decrypt_bytes, encrypt_bytes


_KEY = re.compile(r"[0-9a-f]{32}\Z")


def voucher_path(storage_dir: Path, storage_key: str) -> Path:
    if not _KEY.fullmatch(storage_key):
        raise ValueError("无效的凭证存储键")
    return Path(storage_dir) / "order_vouchers" / storage_key[:2] / storage_key


def _aad(order_id: int, storage_key: str) -> bytes:
    return f"order:{order_id}:voucher:{storage_key}:content".encode("ascii")


def save_encrypted_voucher(storage_dir: Path, key: bytes, order_id: int, data: bytes) -> str:
    storage_key = secrets.token_hex(16)
    path = voucher_path(storage_dir, storage_key)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = encrypt_bytes(key, data, _aad(order_id, storage_key))
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise
    return storage_key


def read_encrypted_voucher(storage_dir: Path, key: bytes, order_id: int, storage_key: str) -> bytes:
    return decrypt_bytes(key, voucher_path(storage_dir, storage_key).read_bytes(), _aad(order_id, storage_key))


def remove_encrypted_voucher(storage_dir: Path, storage_key: str) -> None:
    voucher_path(storage_dir, storage_key).unlink(missing_ok=True)


def voucher_name_aad(order_id: int, storage_key: str) -> bytes:
    return f"order:{order_id}:voucher:{storage_key}:name".encode("ascii")

