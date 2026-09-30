"""Access legacy image files when deleting historical customer records."""

from pathlib import Path
import re

from tax_manager.web import current_app

KEY_RE = re.compile(r"[0-9a-f]{32}\Z")


def file_path(kind: str, key: str) -> Path:
    if kind not in {"customer", "unassigned"} or not KEY_RE.fullmatch(key):
        raise ValueError("Invalid legacy image storage key")
    return Path(current_app.config["STORAGE_DIR"]) / kind / key[:2] / key


def remove_image(kind: str, key: str) -> None:
    file_path(kind, key).unlink(missing_ok=True)
