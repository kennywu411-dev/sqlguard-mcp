import json

from mcp import Client

from sqlguard_mcp.catalog import Catalog
from sqlguard_mcp.server import build_server


def payload(result) -> dict:
    """dict-returning tools come back as JSON text content unless structured output is enabled"""
    if result.structured_content is not None:
        return result.structured_content
    return json.loads("".join(c.text for c in result.content))


async def test_tools_over_mcp(shop_db):
    server = build_server(Catalog([shop_db]))
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {"list_databases", "list_tables", "describe_table", "search_values", "run_sql"}
        ok = await client.call_tool("run_sql", {"db_id": "shop", "sql": "SELECT COUNT(*) AS n FROM orders"})
        assert payload(ok)["rows"] == [[3]]
        bad = await client.call_tool("run_sql", {"db_id": "shop", "sql": "DELETE FROM orders"})
        assert payload(bad)["error"] == "rejected"
