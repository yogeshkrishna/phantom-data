"""Publish a complete file by replacing it from a sibling temporary file."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Keep an existing destination intact on staging/replace failure.

    fsync covers the staged file. Atomic rename is a filesystem guarantee, not a
    promise against hardware failure or filesystems without atomic replacement.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != data:
            raise OSError("staged file readback differs from serialized bytes")
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))
