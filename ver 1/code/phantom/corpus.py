"""corpus.py – read-only CSV and evidence-manifest loading boundary."""

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .contracts import parse_instant

MANIFEST_COLUMNS = ("file_id", "file_path", "origin", "captured_at")

_CHUNK = 65536  # bytes per hash read


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    file_id: str
    file_path: str
    origin: str
    captured_at: str
    path: Path
    sha256: str


def _validate_columns(columns: tuple[str, ...], id_column: str) -> None:
    """Raise ValueError for invalid column configuration."""
    if not columns:
        raise ValueError("read_csv_table: columns must be nonempty")
    for col in columns:
        if not isinstance(col, str) or not col:
            raise ValueError(f"read_csv_table: column name must be nonempty string, got {col!r}")
    if len(set(columns)) != len(columns):
        raise ValueError("read_csv_table: column names must be unique")
    if id_column not in columns:
        raise ValueError(f"read_csv_table: id_column {id_column!r} not in columns")


def read_csv_table(
    path: Path,
    columns: tuple[str, ...],
    id_column: str,
) -> list[dict[str, str]]:
    """Read a strict UTF-8 CSV and return rows as dicts preserving original text."""
    _validate_columns(columns, id_column)
    raw_bytes = path.read_bytes()
    # Accept optional UTF-8 BOM
    text = raw_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
    except csv.Error as exc:
        raise ValueError(f"read_csv_table: CSV parse error in header: {exc}") from exc
    if header is None:
        raise ValueError("read_csv_table: file is empty (no header row)")
    if list(header) != list(columns):
        raise ValueError(
            f"read_csv_table: header {header!r} does not match expected {list(columns)!r}"
        )
    n = len(columns)
    seen_ids: set[str] = set()
    rows: list[dict[str, str]] = []
    while True:
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise ValueError(
                f"read_csv_table: CSV parse error at line {reader.line_num}: {exc}"
            ) from exc
        lineno = reader.line_num
        # A genuinely empty record has no cells; blank identifiers remain errors.
        if not cells:
            continue
        if len(cells) != n:
            raise ValueError(
                f"read_csv_table: line {lineno} has {len(cells)} cells, expected {n}"
            )
        row = dict(zip(columns, cells))
        id_val = row[id_column]
        if not id_val.strip():
            raise ValueError(f"read_csv_table: line {lineno} has blank {id_column!r}")
        if id_val in seen_ids:
            raise ValueError(f"read_csv_table: duplicate {id_column!r} at line {lineno}")
        seen_ids.add(id_val)
        rows.append(row)
    return rows


def _safe_resolve(rel: str, base: Path) -> Path:
    """Resolve a relative evidence path; raise ValueError for unsafe paths."""
    if "\x00" in rel:
        raise ValueError(f"read_manifest: NUL character in path {rel!r}")
    # Parse both path syntaxes regardless of the operating system running us.
    normalized = rel.replace("\\", "/")
    portable = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if portable.is_absolute() or windows.root:
        raise ValueError(f"read_manifest: absolute path not allowed: {rel!r}")
    if windows.drive:
        raise ValueError(f"read_manifest: drive-relative path not allowed: {rel!r}")
    if ".." in portable.parts:
        raise ValueError(f"read_manifest: '..' component not allowed: {rel!r}")
    resolved = (base / Path(*portable.parts)).resolve()
    # Ensure it stays inside base after resolving symlinks
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(f"read_manifest: path escapes dataset_dir: {rel!r}")
    return resolved


def _sha256(path: Path) -> str:
    """Return lowercase hex SHA-256 of exact file bytes."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def read_manifest(dataset_dir: Path) -> tuple[ManifestEntry, ...]:
    """Load evidence_files.csv and return validated ManifestEntry objects."""
    base = dataset_dir.resolve()
    manifest_path = base / "evidence_files.csv"
    rows = read_csv_table(manifest_path, MANIFEST_COLUMNS, id_column="file_id")
    entries: list[ManifestEntry] = []
    for row in rows:
        file_id = row["file_id"]
        file_path_str = row["file_path"]
        captured_at = row["captured_at"]
        # Validate timestamp (raises ValueError for invalid/naive)
        parse_instant(captured_at)
        resolved = _safe_resolve(file_path_str, base)
        if not resolved.exists():
            raise FileNotFoundError(
                f"read_manifest: evidence file not found for {file_id!r}: {resolved}"
            )
        if not resolved.is_file():
            raise ValueError(
                f"read_manifest: path for {file_id!r} is not a regular file: {resolved}"
            )
        digest = _sha256(resolved)
        entries.append(ManifestEntry(
            file_id=file_id,
            file_path=file_path_str,
            origin=row["origin"],
            captured_at=captured_at,
            path=resolved,
            sha256=digest,
        ))
    return tuple(entries)
