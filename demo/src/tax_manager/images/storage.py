"""Private image files and upload validation."""

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import warnings

from tax_manager.web import current_app
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
KEY_RE = re.compile(r"[0-9a-f]{32}\Z")


class InvalidImage(ValueError):
    pass


@dataclass(frozen=True)
class UploadedImage:
    data: bytes
    original_name: str
    mime_type: str
    size_bytes: int


def read_image(stream, filename: str) -> UploadedImage:
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not name or len(name) > 255:
        raise InvalidImage("请选择名称不超过 255 字符的图片")
    data = stream.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise InvalidImage("图片必须在 10 MB 以内")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                if image_format not in MIME_TYPES or image.width * image.height > 25_000_000:
                    raise InvalidImage("仅支持不超过 2500 万像素的 PNG、JPEG、WebP 图片")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise InvalidImage("文件不是有效的 PNG、JPEG 或 WebP 图片") from exc
    return UploadedImage(data, name, MIME_TYPES[image_format], len(data))


def file_path(kind: str, key: str) -> Path:
    if kind not in {"customer", "unassigned"} or not KEY_RE.fullmatch(key):
        raise ValueError("无效的图片存储键")
    return Path(current_app.config["STORAGE_DIR"]) / kind / key[:2] / key


def save_image(kind: str, upload: UploadedImage) -> str:
    key = secrets.token_hex(16)
    path = file_path(kind, key)
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


def remove_image(kind: str, key: str) -> None:
    file_path(kind, key).unlink(missing_ok=True)
