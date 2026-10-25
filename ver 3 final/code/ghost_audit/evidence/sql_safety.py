"""Bounded, in-memory execution of the supported SQLite snapshot grammar.

Evidence SQL is untrusted data. Only ordinary table/index creation and inserts,
read queries, transaction markers, json_valid checks and enabling foreign keys
are accepted. Source SQL cannot attach files, load extensions, install views or
triggers, or change arbitrary SQLite settings. Limits cover source execution and
the parser's subsequent inspection queries together.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

MAX_SQL_BYTES = 32 * 1024 * 1024
MAX_SQL_STEPS = 5_000_000
MAX_SQL_SECONDS = 10.0
MAX_DATABASE_PAGES = 16_384  # 64 MiB at the explicitly selected page size.
PROGRESS_INTERVAL = 1_000

_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_REINDEX, sqlite3.SQLITE_RECURSIVE,
})


def quote_identifier(name: str) -> str:
    """Quote names read from the source schema before internal inspection."""
    return '"' + name.replace('"', '""') + '"'


def _authorize(action, arg1, arg2, db, trigger):
    # SQLite also fills the final context argument for recursive CTEs. Views and
    # triggers cannot be installed because their CREATE actions are denied.
    if db not in (None, "main"):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        allowed = (arg2 or "").lower() == "json_valid"
    elif action == sqlite3.SQLITE_PRAGMA:
        allowed = (arg1 or "").lower() == "foreign_keys" and (arg2 or "").lower() in {"on", "1"}
    elif action == sqlite3.SQLITE_UPDATE:
        # SQLite updates its own schema while processing CREATE TABLE/INDEX.
        allowed = db == "main" and arg1 in {"sqlite_master", "sqlite_schema"}
    else:
        allowed = action in _ALLOWED_ACTIONS
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


@contextmanager
def snapshot_database(text: str):
    """Yield an inspection-ready connection, always closing it on every path.

    Rejected source scripts raise sqlite3.DatabaseError without source values in
    the error. A rejected script is never partially interpreted as evidence.
    """
    if len(text.encode("utf-8")) > MAX_SQL_BYTES:
        raise sqlite3.DatabaseError("SQL source exceeds byte budget")
    con = sqlite3.connect(":memory:")
    steps = 0
    deadline = time.monotonic() + MAX_SQL_SECONDS
    exhausted = False

    def progress():
        nonlocal steps, exhausted
        steps += PROGRESS_INTERVAL
        exhausted = steps >= MAX_SQL_STEPS or time.monotonic() >= deadline
        return int(exhausted)

    try:
        con.enable_load_extension(False)
        con.execute("PRAGMA page_size=4096")
        con.execute(f"PRAGMA max_page_count={MAX_DATABASE_PAGES}")
        con.execute("PRAGMA temp_store=MEMORY")
        # Runtime setlimit is available on supported Python 3.11+ releases.
        if hasattr(con, "setlimit"):
            for category, maximum in (
                (sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024),
                (sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES),
                (sqlite3.SQLITE_LIMIT_COLUMN, 1_024),
                (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100),
                (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 100),
                (sqlite3.SQLITE_LIMIT_ATTACHED, 0),
            ):
                con.setlimit(category, maximum)
        con.set_authorizer(_authorize)
        con.set_progress_handler(progress, PROGRESS_INTERVAL)
        con.executescript(text)
        # Internal inspection SQL is trusted; identifiers are quoted separately.
        # Keep the execution budget active, and forbid writes during inspection.
        con.set_authorizer(None)
        con.execute("PRAGMA query_only=ON")
        con.row_factory = sqlite3.Row
        yield con
    except sqlite3.Error:
        reason = "SQL execution budget exceeded" if exhausted else "SQL rejected or resource limit exceeded"
        raise sqlite3.DatabaseError(reason) from None
    finally:
        con.close()
