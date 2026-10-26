"""Read-only SQL execution with layered defenses.

1. Static check: sqlglot must parse the input as a single SELECT (WITH / UNION
   included). Input sqlglot cannot parse is not rejected outright; it is handed to
   SQLite so the caller gets SQLite's own error message, and layers 2-3 still apply.
2. Read-only connection: ``file:...?mode=ro``.
3. Authorizer: ``set_authorizer`` allows read actions only. A read-only connection
   alone does not stop ATTACH, most PRAGMAs or CREATE TEMP TABLE.
4. Timeout: ``set_progress_handler`` aborts queries that run too long.
5. Row cap: ``fetchmany`` keeps huge results out of the model's context.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_ROWS = 1000


class SQLRejected(Exception):
    """The SQL failed a safety check or was refused by the read-only connection."""


class SQLUnparseable(SQLRejected):
    """sqlglot could not parse the SQL. run_readonly still passes it to SQLite."""


_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}
# Read-only metadata pragmas used by describe_table.
_ALLOWED_PRAGMAS = {"table_info", "table_xinfo", "foreign_key_list", "index_list", "index_info"}
_WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command)


def _authorizer(action: int, arg1, arg2, _db, _source) -> int:
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    # arg2 is the pragma argument, e.g. the table name in table_info(t); every
    # allowed pragma is read-only, so its argument is harmless
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in _ALLOWED_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed_s: float


def check_sql(sql: str) -> None:
    """Allow a single query statement only. Raises SQLRejected otherwise."""
    if not sql or not sql.strip():
        raise SQLRejected("empty SQL")
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except SqlglotError as e:  # ParseError and TokenError (the latter is not a subclass)
        raise SQLUnparseable(f"cannot parse SQL: {e}") from e
    if len(statements) != 1:
        raise SQLRejected(f"exactly one statement allowed, got {len(statements)}")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise SQLRejected(f"only SELECT queries are allowed, got {type(stmt).__name__}")
    # e.g. WITH t AS (...) DELETE ...: no write node may appear anywhere in the tree
    if any(stmt.find_all(*_WRITE_NODES)):
        raise SQLRejected("query contains a write operation")


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Read-only connection with the read-only authorizer. Missing files raise
    instead of silently creating an empty database."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.set_authorizer(_authorizer)
    return conn


def run_readonly(
    db_path: str | Path,
    sql: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> QueryResult:
    """Run SQL on a read-only connection.

    Timeouts raise TimeoutError. Ordinary SQL errors are re-raised as sqlite3
    exceptions so their message can be fed back to the model for correction.
    """
    if max_rows < 0:
        raise ValueError(f"max_rows must be >= 0, got {max_rows}")
    try:
        check_sql(sql)
    except SQLUnparseable:
        pass  # let SQLite report the syntax error; layers 2-3 still block writes

    conn = connect_readonly(db_path)
    start = time.monotonic()
    deadline = start + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.DatabaseError as e:
        msg = str(e)
        if msg == "interrupted":
            raise TimeoutError(f"query interrupted after {timeout_s:g}s") from e
        if "readonly" in msg or "not authorized" in msg:
            raise SQLRejected(f"refused by read-only connection: {msg}") from e
        raise
    finally:
        conn.close()
    return QueryResult(columns, rows[:max_rows], len(rows) > max_rows, time.monotonic() - start)
