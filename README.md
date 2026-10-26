# sqlguard-mcp

A read-only SQLite MCP server for AI agents. It lets Claude Code, Claude Desktop or any MCP client explore and query SQLite databases, and it is built so that a model cannot change your data even when it tries to.

It was extracted from DataPilot, a text-to-SQL project evaluated on the BIRD Mini-Dev benchmark, where the same guard ran tens of thousands of model-generated queries and a prompt-injection test.

## Why another SQLite server

The common reference SQLite server exposes write tools. For an agent that only needs to answer questions about data, that is more power than it should have. A read-only file mode alone is not enough either: SQLite still lets a read-only connection run `ATTACH`, most `PRAGMA`s and `CREATE TEMP TABLE`.

sqlguard-mcp stacks five layers:

1. Static check: sqlglot must parse the input as a single `SELECT` (CTEs and `UNION` included). Multiple statements and write nodes anywhere in the tree, such as `WITH t AS (...) DELETE ...`, are rejected.
2. Read-only connection: the database is opened with `mode=ro`, and a missing path is an error rather than a new empty file.
3. Authorizer: `sqlite3.set_authorizer` allows read actions and a few metadata pragmas only, so `ATTACH`, `PRAGMA writable_schema = 1` and temp tables are refused even if layer 1 is bypassed.
4. Timeout: a progress handler interrupts queries that run too long.
5. Row cap: results are fetched with a hard limit so a large table cannot flood the model's context.

SQL that sqlglot cannot parse is still passed to SQLite, so the agent gets SQLite's own error message and can correct itself; layers 2 and 3 still apply.

## Tools

| Tool | Purpose |
|---|---|
| `list_databases` | db_id of every database (the file name without extension) |
| `list_tables` | tables and their column counts |
| `describe_table` | columns, types, primary and foreign keys, 3 sample rows |
| `search_values` | case-insensitive search of text columns, to find the value actually stored (the user says "female", the column holds `F`) |
| `run_sql` | runs one `SELECT`; errors come back as `{"error", "message"}` instead of failing the call |

## Usage

```bash
uv run sqlguard-mcp path/to/shop.db path/to/folder_of_dbs --timeout 30 --max-rows 1000
```

Add it to Claude Code:

```bash
claude mcp add sqlguard -- uv run --directory /path/to/sqlguard-mcp sqlguard-mcp /path/to/shop.db
```

Inspect it with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run sqlguard-mcp /path/to/shop.db
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

The tests include write attempts that bypass the static check, to show the connection layers hold on their own, plus a row of injected instructions stored in the data to show it comes back as plain data.

## License

MIT
