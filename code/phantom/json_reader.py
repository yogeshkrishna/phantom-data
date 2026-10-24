"""json_reader.py – lossless JSON evidence reader with JSON Pointer traversal."""

import hashlib
import json
import math
from collections.abc import Iterator

from .corpus import ManifestEntry


def _no_dup_keys(pairs: list) -> dict:
    """Object pairs hook that rejects duplicate keys without exposing their values."""
    result: dict = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate key in JSON object")
        result[k] = v
    return result


def _safe_float(s: str) -> float:
    """parse_float hook that rejects numeric literals producing nonfinite floats."""
    f = float(s)
    if not math.isfinite(f):
        raise ValueError("nonfinite float literal in JSON")
    return f


def _check_finite(val: object) -> None:
    """Recursively reject nonfinite floats admitted by the default parse_constant."""
    if isinstance(val, float):
        if not math.isfinite(val):
            raise ValueError("nonfinite float value in JSON")
    elif isinstance(val, dict):
        for v in val.values():
            _check_finite(v)
    elif isinstance(val, list):
        for v in val:
            _check_finite(v)


def load_json_entry(entry: ManifestEntry) -> object:
    """Read, SHA-256-verify and parse one JSON evidence file.

    Rejects duplicate keys, nonfinite numbers and invalid encodings.
    Filesystem errors propagate; all other failures raise ValueError.
    Error messages do not include source text or personal-data values.
    """
    raw = entry.path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ValueError("SHA-256 mismatch for JSON evidence file")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("invalid UTF-8 in JSON evidence file") from None
    try:
        result = json.loads(
            text,
            object_pairs_hook=_no_dup_keys,
            parse_float=_safe_float,
        )
    except json.JSONDecodeError:
        raise ValueError("malformed JSON in evidence file") from None
    _check_finite(result)
    return result


def _walk(pointer: str, node: object) -> Iterator[tuple[str, object]]:
    """Depth-first preorder generator yielding (JSON Pointer, node) pairs."""
    if isinstance(node, float) and not math.isfinite(node):
        raise ValueError("walk_json: nonfinite float")
    if not isinstance(node, (dict, list, str, int, float, bool, type(None))):
        raise ValueError(f"walk_json: unsupported type {type(node).__name__!r}")
    yield (pointer, node)
    if isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                raise ValueError("walk_json: non-string dict key")
            escaped = k.replace("~", "~0").replace("/", "~1")
            yield from _walk(f"{pointer}/{escaped}", v)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(f"{pointer}/{i}", v)


def walk_json(value: object) -> Iterator[tuple[str, object]]:
    """Yield (JSON Pointer, node) pairs for every node in a JSON-compatible tree.

    Traversal is depth-first preorder. Root pointer is "". Object keys are
    escaped per RFC 6901 ('~' → '~0', '/' → '~1'). Array indexes are decimal.
    Raises ValueError on nonfinite floats, non-string keys or unsupported types.
    """
    yield from _walk("", value)
