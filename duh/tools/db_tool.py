"""DatabaseTool -- execute read-only SQL queries against SQLite databases.

Supports three actions:
- query:  Execute a read-only SELECT statement
- schema: Show column names and types for a table
- tables: List all tables in the database

Connection string defaults to the DATABASE_URL environment variable.
Only SQLite is supported (no extra dependencies).
Large result sets are truncated at 100 rows.
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability

# Maximum rows returned from a query before truncation.
MAX_ROWS = 100

# SQL statements that are NOT allowed -- only SELECT is permitted.
_WRITE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _resolve_connection_string(input_conn: str | None) -> str:
    """Return a connection string from input or DATABASE_URL env var."""
    conn = input_conn or os.environ.get("DATABASE_URL", "")
    if not conn:
        return ""
    return conn.strip()


def _is_sqlite(conn: str) -> bool:
    """True if the connection string looks like a SQLite path."""
    lower = conn.lower()
    return (
        lower.endswith(".db")
        or lower.endswith(".sqlite")
        or lower.endswith(".sqlite3")
        or lower == ":memory:"
    )


def _validate_sql(sql: str) -> str | None:
    """Return an error message if the SQL contains write operations, else None."""
    if _WRITE_PATTERN.search(sql):
        return (
            "Only SELECT queries are allowed. "
            "Detected a write operation (INSERT/UPDATE/DELETE/DROP/ALTER/etc.)."
        )
    return None


def _format_rows(
    columns: list[str],
    rows: list[tuple[Any, ...]],
    truncated: bool,
) -> str:
    """Format query results as a readable table."""
    if not rows:
        return "(0 rows)"

    # Build header + divider + rows
    lines: list[str] = []
    # Header
    lines.append(" | ".join(columns))
    lines.append("-+-".join("-" * len(c) for c in columns))

    for row in rows:
        lines.append(" | ".join(str(v) for v in row))

    count = len(rows)
    suffix = f" (truncated to {MAX_ROWS} rows)" if truncated else ""
    lines.append(f"\n({count} row{'s' if count != 1 else ''}){suffix}")
    return "\n".join(lines)


class DatabaseTool:
    """Execute read-only SQL queries against a SQLite database."""

    name = "Database"
    capabilities = Capability.READ_PRIVATE
    description = (
        "Query a SQLite database. Supports three actions: "
        "'query' (read-only SELECT), 'schema' (table columns/types), "
        "'tables' (list all tables). Connection defaults to DATABASE_URL env var."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["query", "schema", "tables"],
                "description": "The action to perform.",
            },
            "sql": {
                "type": "string",
                "description": (
                    "SQL query to execute (required for 'query' action). "
                    "Only SELECT statements are allowed."
                ),
            },
            "table": {
                "type": "string",
                "description": "Table name (required for 'schema' action).",
            },
            "connection_string": {
                "type": "string",
                "description": (
                    "Path to a SQLite database file (.db/.sqlite/.sqlite3) "
                    "or ':memory:'. Defaults to DATABASE_URL env var."
                ),
            },
        },
        "required": ["action"],
    }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        action = input.get("action", "")
        conn_str = _resolve_connection_string(input.get("connection_string"))

        if not conn_str:
            return ToolResult(
                output=(
                    "No connection string provided and DATABASE_URL is not set. "
                    "Pass a connection_string or set the DATABASE_URL environment variable."
                ),
                is_error=True,
            )

        if not _is_sqlite(conn_str):
            return ToolResult(
                output=(
                    f"Unsupported database: {conn_str!r}. "
                    "Only SQLite databases (.db, .sqlite, .sqlite3, :memory:) are supported."
                ),
                is_error=True,
            )

        try:
            conn = sqlite3.connect(conn_str)
        except sqlite3.Error as exc:
            return ToolResult(
                output=f"Failed to connect to database: {exc}",
                is_error=True,
            )

        try:
            if action == "tables":
                return self._list_tables(conn)
            elif action == "schema":
                table = input.get("table", "").strip()
                if not table:
                    return ToolResult(
                        output="'table' parameter is required for the 'schema' action.",
                        is_error=True,
                    )
                return self._show_schema(conn, table)
            elif action == "query":
                sql = input.get("sql", "").strip()
                if not sql:
                    return ToolResult(
                        output="'sql' parameter is required for the 'query' action.",
                        is_error=True,
                    )
                return self._execute_query(conn, sql)
            else:
                return ToolResult(
                    output=f"Unknown action: {action!r}. Use 'query', 'schema', or 'tables'.",
                    is_error=True,
                )
        finally:
            conn.close()

    def _list_tables(self, conn: sqlite3.Connection) -> ToolResult:
        """List all tables in the database."""
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            return ToolResult(output=f"Error listing tables: {exc}", is_error=True)

        if not tables:
            return ToolResult(
                output="No tables found in the database.",
                metadata={"table_count": 0},
            )

        lines = [f"Tables ({len(tables)}):"]
        for t in tables:
            lines.append(f"  - {t}")

        return ToolResult(
            output="\n".join(lines),
            metadata={"table_count": len(tables), "tables": tables},
        )

    def _show_schema(self, conn: sqlite3.Connection, table: str) -> ToolResult:
        """Show column names and types for a given table."""
        try:
            cursor = conn.execute(f"PRAGMA table_info({table!r})")
            columns = cursor.fetchall()
        except sqlite3.Error as exc:
            return ToolResult(
                output=f"Error reading schema for '{table}': {exc}",
                is_error=True,
            )

        if not columns:
            return ToolResult(
                output=f"Table '{table}' not found or has no columns.",
                is_error=True,
            )

        lines = [f"Schema for '{table}':"]
        lines.append(f"{'Column':<20} {'Type':<15} {'Nullable':<10} {'PK'}")
        lines.append("-" * 55)
        for col in columns:
            # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
            cid, name, ctype, notnull, dflt, pk = col
            nullable = "NO" if notnull else "YES"
            pk_str = "YES" if pk else ""
            lines.append(f"{name:<20} {ctype or 'TEXT':<15} {nullable:<10} {pk_str}")

        return ToolResult(
            output="\n".join(lines),
            metadata={
                "table": table,
                "column_count": len(columns),
                "columns": [
                    {"name": c[1], "type": c[2] or "TEXT", "notnull": bool(c[3]), "pk": bool(c[5])}
                    for c in columns
                ],
            },
        )

    def _execute_query(self, conn: sqlite3.Connection, sql: str) -> ToolResult:
        """Execute a read-only SQL query."""
        error = _validate_sql(sql)
        if error:
            return ToolResult(output=error, is_error=True)

        try:
            cursor = conn.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as exc:
            return ToolResult(output=f"SQL error: {exc}", is_error=True)

        truncated = len(rows) > MAX_ROWS
        if truncated:
            rows = rows[:MAX_ROWS]

        output = _format_rows(columns, rows, truncated)
        return ToolResult(
            output=output,
            metadata={
                "row_count": len(rows),
                "columns": columns,
                "truncated": truncated,
            },
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
