"""Text extraction for PDFs (pypdf / pdftotext) and screenshots (RapidOCR, cached)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from ..config import EVIDENCE_CACHE_DIR

_OCR_ENGINE = None


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
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR  # required (code/requirements.txt)

        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _ocr_lines(path: Path) -> list[str]:
    """OCR the image and rebuild reading-order lines from the detected boxes."""
    result, _ = _ocr_engine()(str(path))
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


def image_text(path: Path, file_id: str) -> tuple[str, str]:
    """Return (text, method). OCR output is cached locally keyed by file content hash; the cache is
    derived from the evidence, so it is a run-time artifact and is never packaged."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()[:16]
    cache = EVIDENCE_CACHE_DIR / f"{file_id}_{digest}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8"), "ocr_cache"
    try:
        lines = _ocr_lines(path)
    except ImportError:
        return "", "ocr_unavailable"
    text = "\n".join(lines)
    EVIDENCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text, "ocr"
