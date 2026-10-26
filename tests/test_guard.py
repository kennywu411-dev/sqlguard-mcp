import sqlite3

import pytest

from sqlguard_mcp.guard import SQLRejected, check_sql, connect_readonly, run_readonly


def test_select_runs(shop_db):
    res = run_readonly(shop_db, "SELECT name FROM customers WHERE gender = 'F' ORDER BY id")
    assert res.columns == ["name"]
    assert res.rows == [("Alice",), ("100%_off fan",)]
    assert not res.truncated


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers",
        "DELETE FROM customers",
        "UPDATE customers SET name = 'x'",
        "INSERT INTO customers VALUES (9, 'x', 'M')",
        "SELECT 1; DROP TABLE customers",
        "WITH t AS (SELECT 1) DELETE FROM customers",
    ],
)
def test_static_check_rejects_writes(sql):
    with pytest.raises(SQLRejected):
        check_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH DATABASE 'other.db' AS other",
        "PRAGMA writable_schema = 1",
        "CREATE TEMP TABLE t (x)",
        "DROP TABLE customers",
    ],
)
def test_connection_layers_block_writes_even_without_static_check(shop_db, sql):
    # bypass check_sql on purpose: the read-only connection and the authorizer must hold on their own
    conn = connect_readonly(shop_db)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
    finally:
        conn.close()
    assert run_readonly(shop_db, "SELECT COUNT(*) FROM customers").rows == [(3,)]


def test_unparseable_sql_returns_sqlite_error(shop_db):
    with pytest.raises(sqlite3.OperationalError):
        run_readonly(shop_db, "SELEC name FROM customers")


def test_timeout(shop_db):
    endless = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(TimeoutError):
        run_readonly(shop_db, endless, timeout_s=0.2)


def test_row_cap(shop_db):
    res = run_readonly(shop_db, "SELECT id FROM orders ORDER BY id", max_rows=2)
    assert res.rows == [(1,), (2,)] and res.truncated


def test_missing_file_is_not_created(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        connect_readonly(missing)
    assert not missing.exists()
