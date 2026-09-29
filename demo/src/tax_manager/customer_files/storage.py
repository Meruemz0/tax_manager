"""Private storage for arbitrary customer attachments."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets

from flask import current_app


MAX_OTHER_FILE_BYTES = 1024 * 1024
KEY_RE = re.compile(r"[0-9a-f]{32}\Z")


class InvalidOtherFile(ValueError):
    pass


@dataclass(frozen=True)
class UploadedOtherFile:
    data: bytes
    original_name: str
    size_bytes: int


def valid_filename(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.replace("\\", "/").split("/")[-1].strip()
    if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
        return None
    return name


def read_other_file(stream, filename: str) -> UploadedOtherFile:
    name = valid_filename(filename)
    if not name:
        raise InvalidOtherFile("文件名称无效或超过 255 字")
    data = stream.read(MAX_OTHER_FILE_BYTES + 1)
    if not data or len(data) > MAX_OTHER_FILE_BYTES:
        raise InvalidOtherFile("其他文件必须大于 0 且不超过 1 MB")
    return UploadedOtherFile(data, name, len(data))


def file_path(key: str) -> Path:
    if not KEY_RE.fullmatch(key):
        raise ValueError("无效的文件存储键")
    return Path(current_app.config["STORAGE_DIR"]) / "other" / key[:2] / key


def save_other_file(upload: UploadedOtherFile) -> str:
    key = secrets.token_hex(16)
    path = file_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open("xb") as out:
            created = True
            out.write(upload.data)
            out.flush()
            os.fsync(out.fileno())
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise
    return key


def remove_other_file(key: str) -> None:
    file_path(key).unlink(missing_ok=True)
