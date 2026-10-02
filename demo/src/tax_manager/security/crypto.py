"""Authenticated encryption with a separate, stable 256-bit data key."""

import os
from pathlib import Path
import secrets
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag


def load_or_create_data_key(storage_dir: Path, explicit_path: Path | None = None) -> bytes:
    """Use an operator-supplied key, or create a sibling key once for local installs.

    A marker in the storage directory makes loss of an existing key fail closed.
    Docker deployments can point DATA_KEY_FILE at a read-only secret instead.
    """
    storage_dir = Path(storage_dir)
    storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = storage_dir / ".data-key-created"
    path = Path(explicit_path) if explicit_path else storage_dir.with_name(storage_dir.name + ".data-key")
    if not path.exists() and explicit_path:
        raise RuntimeError(f"加密密钥文件不存在：{path}")
    if not path.exists() and marker.exists():
        raise RuntimeError(f"加密密钥丢失，请从备份恢复：{path}")
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
    deadline = time.monotonic() + 5
    while True:
        key = path.read_bytes()
        if len(key) == 32:
            marker.touch(exist_ok=True)
            return key
        if time.monotonic() >= deadline:
            raise RuntimeError(f"加密密钥文件无效：{path}")
        time.sleep(0.05)


def encrypt_bytes(key: bytes, data: bytes, associated_data: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    return b"\x01" + nonce + AESGCM(key).encrypt(nonce, data, associated_data)


def decrypt_bytes(key: bytes, payload: bytes, associated_data: bytes) -> bytes:
    if len(payload) < 30 or payload[:1] != b"\x01":
        raise ValueError("加密数据格式无效")
    try:
        return AESGCM(key).decrypt(payload[1:13], payload[13:], associated_data)
    except InvalidTag as exc:
        raise ValueError("加密数据校验失败") from exc
