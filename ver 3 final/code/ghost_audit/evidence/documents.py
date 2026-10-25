"""Text extraction for PDFs (pypdf / pdftotext) and screenshots (RapidOCR, cached)."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import threading
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from ..atomic import atomic_write_bytes
from ..config import EVIDENCE_CACHE_DIR

_OCR_ENGINE = None
_ENGINE_LOCK = threading.RLock()
_STATS_LOCK = threading.Lock()
CACHE_SCHEMA_VERSION = 1
MAX_CACHE_BYTES = 16 * 1024 * 1024
_COUNTERS = (
    "image_requests", "inference_calls", "inference_successes", "inference_failures",
    "cache_hits", "cache_misses", "invalid_cache_entries", "unavailable",
    "cache_write_failures", "source_changed",
)
_OCR_STATS = {name: 0 for name in _COUNTERS}
_LAST_ENGINE_IDENTITY = None


def _count(name: str):
    with _STATS_LOCK:
        _OCR_STATS[name] += 1


def reset_ocr_telemetry():
    """Reset run counters without loading models or discarding a warm engine."""
    global _LAST_ENGINE_IDENTITY
    with _STATS_LOCK:
        _OCR_STATS.update({name: 0 for name in _COUNTERS})
        _LAST_ENGINE_IDENTITY = None


def get_ocr_telemetry() -> dict:
    """Return a detached snapshot; OCR inference has no text-token quantity."""
    with _STATS_LOCK:
        return {**_OCR_STATS, "engine_identity": deepcopy(_LAST_ENGINE_IDENTITY),
                "text_tokens": None, "paid_api_calls": 0}


@lru_cache(maxsize=1)
def _engine_identity() -> dict:
    """Fingerprint installed engine/model/config bytes without model inference.

    Packages and model files are immutable during a run. The cached fingerprint
    is recomputed in a fresh process, so dependency/model upgrades invalidate
    entries. The checksum protects accidental corruption, not a malicious user
    able to rewrite both cached text and its checksum.
    """
    names = ("rapidocr-onnxruntime", "onnxruntime", "numpy", "opencv-python",
             "pillow", "pyclipper", "shapely")
    versions = {name: importlib.metadata.version(name) for name in names}
    dist = importlib.metadata.distribution("rapidocr-onnxruntime")
    package = Path(dist.locate_file("rapidocr_onnxruntime"))
    resources = {}
    for resource in sorted(package.rglob("*")):
        if resource.is_file() and "__pycache__" not in resource.parts and resource.suffix in {
            ".onnx", ".yaml", ".txt", ".py",
        }:
            resources[resource.relative_to(package).as_posix()] = hashlib.sha256(resource.read_bytes()).hexdigest()
    if not any(name.endswith(".onnx") for name in resources) or "config.yaml" not in resources:
        raise ImportError("RapidOCR model/configuration resources are missing")
    return {"provider": "local", "engine": "rapidocr_onnxruntime.RapidOCR",
            "package_versions": versions, "resources_sha256": resources,
            "configuration": "installed defaults; no constructor overrides",
            "reading_order": "box-center-line-v1",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": platform.machine()}


def ocr_engine_identity() -> dict:
    """Return exact local OCR identity, raising ImportError if unavailable."""
    try:
        return deepcopy(_engine_identity())
    except importlib.metadata.PackageNotFoundError as exc:
        raise ImportError("Required local OCR dependency is unavailable") from exc


def pdf_text(path: Path) -> tuple[str, str]:
    """Return (text, method)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        if text.strip():
            return text, "pypdf"
    except Exception:  # pragma: no cover - fallback path
        pass
    exe = shutil.which("pdftotext")
    if exe:
        out = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, text=True, encoding="utf-8")
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout, "pdftotext"
    return "", "failed"


def _ocr_engine():
    global _OCR_ENGINE
    with _ENGINE_LOCK:
        if _OCR_ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR  # required (code/requirements.txt)

            _OCR_ENGINE = RapidOCR()
        return _OCR_ENGINE


def _ocr_lines(path: Path) -> list[str]:
    """OCR the image and rebuild reading-order lines from the detected boxes."""
    with _ENGINE_LOCK:
        engine = _ocr_engine()
        _count("inference_calls")
        try:
            result, _ = engine(str(path))
        except Exception:
            _count("inference_failures")
            raise
        _count("inference_successes")
    boxes = []
    for box, text, _conf in result or []:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append((sum(ys) / len(ys), min(xs), max(ys) - min(ys), text))
    boxes.sort()
    lines: list[list[tuple[float, str]]] = []
    last_y = None
    for y, x, h, text in boxes:
        if last_y is None or abs(y - last_y) > max(8.0, h * 0.6):
            lines.append([])
            last_y = y
        lines[-1].append((x, text))
    return ["  |  ".join(t for _, t in sorted(line)) for line in lines]


def _cached_text(cache: Path, source_digest: str, identity: dict) -> str | None:
    if not cache.exists():
        return None
    try:
        if cache.stat().st_size > MAX_CACHE_BYTES:
            raise ValueError("oversized OCR cache")
        envelope = json.loads(cache.read_text(encoding="utf-8"))
        text = envelope["text"]
        if (envelope["schema_version"] != CACHE_SCHEMA_VERSION
                or envelope["source_sha256"] != source_digest
                or envelope["engine_identity"] != identity
                or not isinstance(text, str) or not text.strip() or "\x00" in text
                or envelope["text_sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest()):
            raise ValueError("invalid OCR cache envelope")
        return text
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        _count("invalid_cache_entries")
        return None


def image_text(path: Path, file_id: str) -> tuple[str, str]:
    """Return (text, method) using only validated, versioned atomic caches.

    Unverified legacy text files are deliberately ignored. The full source and
    engine identities determine the name; file_id never becomes a path segment.
    """
    global _LAST_ENGINE_IDENTITY
    _count("image_requests")
    try:
        identity = ocr_engine_identity()
    except ImportError:
        _count("unavailable")
        return "", "ocr_unavailable"
    with _STATS_LOCK:
        _LAST_ENGINE_IDENTITY = deepcopy(identity)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    identity_digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    cache = EVIDENCE_CACHE_DIR / f"ocr_v{CACHE_SCHEMA_VERSION}_{digest}_{identity_digest}.json"
    text = _cached_text(cache, digest, identity)
    if text is not None:
        _count("cache_hits")
        return text, "ocr_cache"
    _count("cache_misses")
    try:
        lines = _ocr_lines(path)
    except ImportError:
        _count("unavailable")
        return "", "ocr_unavailable"
    text = "\n".join(lines)
    if not text.strip() or "\x00" in text:
        return "", "ocr_empty"
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        _count("source_changed")
        return "", "ocr_source_changed"
    envelope = {"schema_version": CACHE_SCHEMA_VERSION, "source_sha256": digest,
                "engine_identity": identity, "text": text,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    payload = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        if len(payload) <= MAX_CACHE_BYTES:
            atomic_write_bytes(cache, payload)
        else:
            _count("cache_write_failures")
    except OSError:
        # A cache is optional derived data. Valid extraction is still usable;
        # any old entry survives an atomic-write failure and is revalidated.
        _count("cache_write_failures")
    return text, "ocr"
