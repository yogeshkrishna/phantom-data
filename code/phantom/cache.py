"""Safe JSON cache envelopes and exclusively owned atomic temporary writes."""
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

VERSION = "compiled-cache.v1"


def atomic_replace(source, destination):
    """Windows may briefly lock a destination during another writer's replacement."""
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.01 * 2**attempt)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _valid_json(value, depth=0):
    if depth > 64:
        return False
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_valid_json(v, depth+1) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _valid_json(v, depth+1) for k, v in value.items())
    return False


class JsonCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.last_error = None

    def _path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("cache key must be a SHA256 digest")
        return self.directory / (key + ".json")

    def load(self, key):
        self.last_error = None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("cache size limit")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not _valid_json(raw) or set(raw) != {"key", "version", "sha256", "data"}:
                raise ValueError("invalid envelope")
            if raw["key"] != key or raw["version"] != VERSION or hashlib.sha256(canonical(raw["data"])).hexdigest() != raw["sha256"]:
                raise ValueError("stale or corrupt cache")
            return raw["data"]
        except (OSError, ValueError, TypeError, RecursionError):
            self.last_error = "CORRUPT_CACHE_REBUILT"
            return None

    def store(self, key, data):
        path = self._path(key)
        payload = canonical(dict(key=key, version=VERSION, data=data, sha256=hashlib.sha256(canonical(data)).hexdigest()))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile("wb", dir=path.parent, prefix=".compile-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            atomic_replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
