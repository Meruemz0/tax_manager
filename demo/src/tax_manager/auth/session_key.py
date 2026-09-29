"""Keep Flask's session signing key stable across restarts and workers."""

import os
import secrets
import time
from pathlib import Path


def load_or_create_session_key(storage_dir: Path) -> bytes:
    storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = storage_dir / ".session-key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_bytes(32))
            stream.flush()
            os.fsync(stream.fileno())

    # A second worker can see the new file before the first has finished writing.
    deadline = time.monotonic() + 5
    while True:
        key = path.read_bytes()
        if len(key) == 32:
            return key
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Invalid session key file: {path}")
        time.sleep(0.05)
