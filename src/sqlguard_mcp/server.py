"""MCP server (stdio) exposing read-only tools over one or more SQLite files.

    sqlguard-mcp path/to/shop.db path/to/dir_of_dbs --timeout 30 --max-rows 1000
"""

from __future__ import annotations

import argparse

from mcp.server.mcpserver import MCPServer

from sqlguard_mcp.catalog import Catalog

INSTRUCTIONS = (
    "Read-only SQLite tools. Suggested order: list_databases -> list_tables -> describe_table "
    "-> search_values (confirm the value actually stored) -> run_sql. Only single SELECT "
    "statements are executed; writes, ATTACH and PRAGMA changes are refused."
)


def build_server(catalog: Catalog) -> MCPServer:
    server = MCPServer("sqlguard", instructions=INSTRUCTIONS)

    @server.tool()
    def list_databases() -> list[str]:
        """List the db_id of every database that can be queried."""
        return catalog.list_databases()

    @server.tool()
    def list_tables(db_id: str) -> list[dict]:
        """List the tables in a database and how many columns each has."""
        return catalog.list_tables(db_id)

    @server.tool()
    def describe_table(db_id: str, table: str) -> dict:
        """Columns, types, primary and foreign keys, plus 3 sample rows."""
        return catalog.describe_table(db_id, table)

    @server.tool()
    def search_values(
        db_id: str, keyword: str, table: str | None = None, column: str | None = None, limit: int = 10
    ) -> dict:
        """Fuzzy-search text columns for real stored values, to get WHERE conditions right."""
        return catalog.search_values(db_id, keyword, table, column, limit)

    @server.tool()
    def run_sql(db_id: str, sql: str, max_rows: int = 50) -> dict:
        """Run one SELECT read-only. Returns columns and rows, or error and message."""
        return catalog.run_sql(db_id, sql, max_rows)

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sqlguard-mcp", description=__doc__.strip().splitlines()[0])
    parser.add_argument("paths", nargs="+", help="SQLite files or directories containing them")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-query timeout in seconds")
    parser.add_argument("--max-rows", type=int, default=1000, help="hard cap on rows returned")
    args = parser.parse_args(argv)
    build_server(Catalog(args.paths, timeout_s=args.timeout, max_rows=args.max_rows)).run("stdio")


if __name__ == "__main__":
    main()
