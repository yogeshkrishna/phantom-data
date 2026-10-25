"""sql_reader.py – lossless SQLite-compatible SQL snapshot reader."""

import hashlib
import sqlite3
from dataclasses import dataclass

from .corpus import ManifestEntry

MAX_SQL_STEPS = 5_000_000

_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_RECURSIVE,
})


@dataclass(frozen=True, slots=True)
class SqlTable:
    """One ordinary user table extracted from the snapshot."""
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, slots=True)
class SqlDump:
    """Full decoded text and extracted tables for one SQL snapshot file."""
    text: str
    tables: tuple[SqlTable, ...]


def _quote_id(name: str) -> str:
    """Surround an identifier in double quotes, doubling embedded double quotes."""
    return '"' + name.replace('"', '""') + '"'


def _make_authorizer() -> object:
    """Return a SQLite authorizer that restricts source-script operations."""
    def authorizer(action, arg1, arg2, db, trigger):
        if action == sqlite3.SQLITE_FUNCTION:
            allowed = (arg2 or "").lower() == "json_valid"
        elif action == sqlite3.SQLITE_PRAGMA:
            # Corpus dumps enable FK checks. No other source PRAGMA is needed.
            allowed = ((arg1 or "").lower() == "foreign_keys"
                       and (arg2 or "").lower() in {"on", "1"})
        elif action == sqlite3.SQLITE_UPDATE:
            allowed = db == "main" and arg1 in {"sqlite_master", "sqlite_schema"}
        else:
            allowed = action in _ALLOWED_ACTIONS
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
    return authorizer


def read_sql_entry(entry: ManifestEntry) -> SqlDump:
    """Read, verify and parse one SQL snapshot file; return its text and tables."""
    raw = entry.path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ValueError(f"read_sql_entry: SHA-256 mismatch for file_id={entry.file_id!r}")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("read_sql_entry: invalid UTF-8") from None
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise ValueError("read_sql_entry: SQLite 3.37 or newer is required")
    steps_used = 0

    def progress_handler():
        nonlocal steps_used
        steps_used += 1
        return int(steps_used >= MAX_SQL_STEPS)

    conn = sqlite3.connect(":memory:")
    try:
        conn.set_authorizer(_make_authorizer())
        # A one-instruction interval counts across statements and inspection.
        conn.set_progress_handler(progress_handler, 1)
        conn.executescript(text)

        # Remove authorizer for trusted internal inspection queries only.
        conn.set_authorizer(None)
        table_info = conn.execute("PRAGMA main.table_list").fetchall()
        user_tables = sorted(
            (row for row in table_info
             if row[0] == "main" and row[2] == "table"
             and not row[1].lower().startswith("sqlite_")),
            key=lambda row: row[1],
        )

        tables: list[SqlTable] = []
        for table in user_tables:
            tname = table[1]
            if table[4]:
                raise ValueError("read_sql_entry: WITHOUT ROWID is unsupported")
            quoted = _quote_id(tname)
            columns = tuple(
                r[1] for r in conn.execute(f"PRAGMA main.table_xinfo({quoted})").fetchall()
                if r[6] != 1
            )
            names = {column.lower() for column in columns}
            rowid = next((alias for alias in ("rowid", "_rowid_", "oid")
                          if alias not in names), None)
            if rowid is None:
                raise ValueError("read_sql_entry: all rowid aliases are shadowed")
            rows_raw = conn.execute(
                f"SELECT * FROM {quoted} ORDER BY {_quote_id(rowid)}"
            ).fetchall()
            tables.append(SqlTable(name=tname, columns=columns,
                                   rows=tuple(tuple(r) for r in rows_raw)))
        return SqlDump(text=text, tables=tuple(tables))
    except sqlite3.Error:
        reason = "execution budget exceeded" if steps_used >= MAX_SQL_STEPS else "SQL rejected"
        # SQLite error strings can contain CHECK expressions and source values.
        raise ValueError(f"read_sql_entry: {reason}") from None
    finally:
        conn.close()
