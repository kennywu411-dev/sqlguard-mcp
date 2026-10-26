"""Database tools: discover tables, inspect schema, find real values, run SQL.

A Catalog maps db_id (the file stem) to SQLite files. Every call goes through a
read-only connection from guard.py.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from sqlglot import exp

from sqlguard_mcp.guard import SQLRejected, connect_readonly, run_readonly

SAMPLE_ROWS = 3
MAX_CELL_CHARS = 200
SEARCH_TIMEOUT_S = 5.0
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def _q(name: str) -> str:
    return exp.to_identifier(name, quoted=True).sql(dialect="sqlite")


def _clip(value):
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + "..."
    return value


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_text(declared_type: str) -> bool:
    t = (declared_type or "").upper()
    return not t or any(k in t for k in ("CHAR", "TEXT", "CLOB"))


class Catalog:
    def __init__(self, paths: list[str | Path], timeout_s: float = 30.0, max_rows: int = 1000):
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self.dbs: dict[str, Path] = {}
        for p in map(Path, paths):
            files = sorted(f for f in p.rglob("*") if f.suffix in SQLITE_SUFFIXES) if p.is_dir() else [p]
            for f in files:
                if not f.is_file():
                    raise FileNotFoundError(f)
                if f.stem in self.dbs:
                    raise ValueError(f"duplicate db_id {f.stem!r}: {self.dbs[f.stem]} and {f}")
                self.dbs[f.stem] = f.resolve()
        if not self.dbs:
            raise ValueError("no SQLite databases found")

    def _db(self, db_id: str) -> Path:
        try:
            return self.dbs[db_id]
        except KeyError:
            raise ValueError(f"unknown db_id {db_id!r}; available: {sorted(self.dbs)}") from None

    def _tables(self, conn: sqlite3.Connection) -> list[str]:
        return [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

    def _table(self, conn: sqlite3.Connection, table: str) -> str:
        """Resolve the table name case-insensitively; unknown names raise."""
        by_lower = {t.lower(): t for t in self._tables(conn)}
        try:
            return by_lower[table.lower()]
        except KeyError:
            raise ValueError(f"unknown table {table!r}") from None

    def list_databases(self) -> list[str]:
        return sorted(self.dbs)

    def list_tables(self, db_id: str) -> list[dict]:
        conn = connect_readonly(self._db(db_id))
        try:
            return [
                {"table": t, "columns": len(conn.execute(f"PRAGMA table_info({_q(t)})").fetchall())}
                for t in self._tables(conn)
            ]
        finally:
            conn.close()

    def describe_table(self, db_id: str, table: str) -> dict:
        conn = connect_readonly(self._db(db_id))
        try:
            table = self._table(conn, table)
            cols = conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
            fks = conn.execute(f"PRAGMA foreign_key_list({_q(table)})").fetchall()
            sample = conn.execute(f"SELECT * FROM {_q(table)} LIMIT {SAMPLE_ROWS}").fetchall()
        finally:
            conn.close()
        return {
            "table": table,
            "columns": [{"name": c[1], "type": c[2], "primary_key": bool(c[5])} for c in cols],
            "foreign_keys": [{"column": f[3], "ref_table": f[2], "ref_column": f[4]} for f in fks],
            "sample_rows": [[_clip(v) for v in r] for r in sample],
        }

    def search_values(
        self, db_id: str, keyword: str, table: str | None = None, column: str | None = None, limit: int = 10
    ) -> dict:
        """Case-insensitive LIKE search over text columns, to find the value actually
        stored (the user says "female", the column holds 'F')."""
        if not keyword:
            raise ValueError("keyword must not be empty")
        limit = max(1, min(limit, 100))
        conn = connect_readonly(self._db(db_id))
        try:
            tables = [self._table(conn, table)] if table else self._tables(conn)
            targets = []
            for t in tables:
                cols = conn.execute(f"PRAGMA table_info({_q(t)})").fetchall()
                if column:
                    match = [c[1] for c in cols if c[1].lower() == column.lower()]
                    if not match:
                        raise ValueError(f"unknown column {t}.{column}")
                    targets.append((t, match[0]))
                else:
                    targets += [(t, c[1]) for c in cols if _is_text(c[2])]

            matches, skipped = [], []
            deadline = time.monotonic() + SEARCH_TIMEOUT_S
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            for t, c in targets:
                if len(matches) >= limit:
                    break
                sql = (
                    f"SELECT DISTINCT {_q(c)} FROM {_q(t)} WHERE {_q(c)} LIKE ? ESCAPE '\\' "
                    f"LIMIT {limit - len(matches)}"
                )
                try:
                    rows = conn.execute(sql, (f"%{_escape_like(keyword)}%",)).fetchall()
                except sqlite3.OperationalError:  # timed out or a bad column: skip and report
                    skipped.append(f"{t}.{c}")
                    continue
                matches += [{"table": t, "column": c, "value": _clip(r[0])} for r in rows]
        finally:
            conn.close()
        return {"keyword": keyword, "matches": matches[:limit], "skipped": skipped}

    def run_sql(self, db_id: str, sql: str, max_rows: int = 50) -> dict:
        """Errors are returned, not raised, so an agent can read them and fix its SQL."""
        try:
            res = run_readonly(
                self._db(db_id), sql, timeout_s=self.timeout_s, max_rows=min(max(max_rows, 0), self.max_rows)
            )
        except SQLRejected as e:
            return {"error": "rejected", "message": str(e)}
        except TimeoutError as e:
            return {"error": "timeout", "message": str(e)}
        except sqlite3.Error as e:
            return {"error": "sql_error", "message": str(e)}
        return {
            "columns": res.columns,
            "rows": [[_clip(v) for v in r] for r in res.rows],
            "truncated": res.truncated,
            "elapsed_s": round(res.elapsed_s, 4),
        }
