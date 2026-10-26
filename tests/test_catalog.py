import pytest

from sqlguard_mcp.catalog import Catalog


@pytest.fixture
def catalog(shop_db):
    return Catalog([shop_db])


def test_list(catalog):
    assert catalog.list_databases() == ["shop"]
    assert catalog.list_tables("shop") == [{"table": "customers", "columns": 3}, {"table": "orders", "columns": 4}]


def test_directory_discovery(shop_db):
    assert Catalog([shop_db.parent]).list_databases() == ["shop"]


def test_describe_table(catalog):
    d = catalog.describe_table("shop", "ORDERS")  # case-insensitive
    assert d["table"] == "orders"
    assert d["foreign_keys"] == [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}]
    assert len(d["sample_rows"]) == 3


def test_unknown_names(catalog):
    with pytest.raises(ValueError):
        catalog.describe_table("shop", "missing")
    with pytest.raises(ValueError):
        catalog.list_tables("other")


def test_search_values_finds_stored_value(catalog):
    res = catalog.search_values("shop", "ali", table="customers")
    assert res["matches"] == [{"table": "customers", "column": "name", "value": "Alice"}]


def test_search_values_escapes_like_wildcards(catalog):
    res = catalog.search_values("shop", "%_", column="name", table="customers")
    assert [m["value"] for m in res["matches"]] == ["100%_off fan"]


def test_run_sql_returns_errors_instead_of_raising(catalog):
    assert catalog.run_sql("shop", "DROP TABLE customers")["error"] == "rejected"
    assert catalog.run_sql("shop", "SELECT nope FROM customers")["error"] == "sql_error"


def test_injected_text_is_returned_as_data(catalog):
    res = catalog.run_sql("shop", "SELECT note FROM orders WHERE id = 2")
    assert "DROP TABLE" in res["rows"][0][0]
    assert catalog.run_sql("shop", "SELECT COUNT(*) FROM customers")["rows"] == [[3]]
