"""Tests for duh.tools.db_tool -- DatabaseTool.

Uses SQLite in-memory databases for all tests (no external dependencies).

Covers:
- action=tables: listing tables
- action=schema: column names, types, PK, nullable
- action=query: read-only SELECT, formatting, row counts
- SQL injection / write rejection (INSERT/UPDATE/DELETE/DROP/ALTER)
- Truncation at 100 rows
- Missing connection string handling
- Unsupported database type rejection
- Missing required parameters
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from duh.kernel.tool import ToolContext, ToolResult
from duh.tools.db_tool import (
    MAX_ROWS,
    DatabaseTool,
    _is_sqlite,
    _resolve_connection_string,
    _validate_sql,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx() -> ToolContext:
    return ToolContext(cwd="/tmp/test")


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _setup_test_db(conn: sqlite3.Connection) -> None:
    """Create a sample schema and insert rows for testing."""
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT)"
    )
    conn.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL)"
    )
    conn.execute("INSERT INTO users VALUES (1, 'Alice', 'alice@example.com')")
    conn.execute("INSERT INTO users VALUES (2, 'Bob', 'bob@example.com')")
    conn.execute("INSERT INTO orders VALUES (1, 1, 99.99)")
    conn.execute("INSERT INTO orders VALUES (2, 2, 42.50)")
    conn.commit()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a temporary SQLite database with sample data."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    _setup_test_db(conn)
    conn.close()
    return path


@pytest.fixture
def tool() -> DatabaseTool:
    return DatabaseTool()


# ---------------------------------------------------------------------------
# 1. action=tables -- list all tables
# ---------------------------------------------------------------------------

class TestListTables:
    def test_lists_tables(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "tables", "connection_string": db_path},
            _ctx(),
        ))
        assert not result.is_error
        assert "users" in result.output
        assert "orders" in result.output
        assert result.metadata["table_count"] == 2

    def test_empty_database(self, tool: DatabaseTool, tmp_path: Path):
        path = str(tmp_path / "empty.db")
        sqlite3.connect(path).close()
        result = _run(tool.call(
            {"action": "tables", "connection_string": path},
            _ctx(),
        ))
        assert not result.is_error
        assert "No tables" in result.output
        assert result.metadata["table_count"] == 0


# ---------------------------------------------------------------------------
# 2. action=schema -- show table columns/types
# ---------------------------------------------------------------------------

class TestShowSchema:
    def test_shows_columns(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "schema", "table": "users", "connection_string": db_path},
            _ctx(),
        ))
        assert not result.is_error
        assert "id" in result.output
        assert "name" in result.output
        assert "email" in result.output
        assert "INTEGER" in result.output
        assert result.metadata["column_count"] == 3

    def test_schema_metadata_has_columns(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "schema", "table": "users", "connection_string": db_path},
            _ctx(),
        ))
        cols = result.metadata["columns"]
        names = [c["name"] for c in cols]
        assert "id" in names
        assert "name" in names
        pk_cols = [c for c in cols if c["pk"]]
        assert len(pk_cols) == 1
        assert pk_cols[0]["name"] == "id"

    def test_nonexistent_table(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "schema", "table": "nonexistent", "connection_string": db_path},
            _ctx(),
        ))
        assert result.is_error
        assert "not found" in result.output

    def test_missing_table_param(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "schema", "connection_string": db_path},
            _ctx(),
        ))
        assert result.is_error
        assert "'table' parameter is required" in result.output


# ---------------------------------------------------------------------------
# 3. action=query -- read-only SELECT
# ---------------------------------------------------------------------------

class TestQuery:
    def test_select_all(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "query", "sql": "SELECT * FROM users", "connection_string": db_path},
            _ctx(),
        ))
        assert not result.is_error
        assert "Alice" in result.output
        assert "Bob" in result.output
        assert result.metadata["row_count"] == 2
        assert not result.metadata["truncated"]

    def test_select_with_where(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {
                "action": "query",
                "sql": "SELECT name FROM users WHERE id = 1",
                "connection_string": db_path,
            },
            _ctx(),
        ))
        assert not result.is_error
        assert "Alice" in result.output
        assert "Bob" not in result.output
        assert result.metadata["row_count"] == 1

    def test_empty_result(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {
                "action": "query",
                "sql": "SELECT * FROM users WHERE id = 999",
                "connection_string": db_path,
            },
            _ctx(),
        ))
        assert not result.is_error
        assert "(0 rows)" in result.output
        assert result.metadata["row_count"] == 0

    def test_missing_sql_param(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "query", "connection_string": db_path},
            _ctx(),
        ))
        assert result.is_error
        assert "'sql' parameter is required" in result.output

    def test_invalid_sql(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "query", "sql": "SELCT * FORM users", "connection_string": db_path},
            _ctx(),
        ))
        assert result.is_error
        assert "SQL error" in result.output


# ---------------------------------------------------------------------------
# 4. Write statement rejection
# ---------------------------------------------------------------------------

class TestWriteRejection:
    @pytest.mark.parametrize("sql", [
        "INSERT INTO users VALUES (3, 'Eve', 'eve@example.com')",
        "UPDATE users SET name = 'Mallory' WHERE id = 1",
        "DELETE FROM users WHERE id = 1",
        "DROP TABLE users",
        "ALTER TABLE users ADD COLUMN age INTEGER",
        "CREATE TABLE evil (id INTEGER)",
        "TRUNCATE TABLE users",
    ])
    def test_rejects_write_operations(self, tool: DatabaseTool, db_path: str, sql: str):
        result = _run(tool.call(
            {"action": "query", "sql": sql, "connection_string": db_path},
            _ctx(),
        ))
        assert result.is_error
        assert "Only SELECT" in result.output

    def test_rejects_case_insensitive(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "query", "sql": "insert into users values (3, 'x', 'x')", "connection_string": db_path},
            _ctx(),
        ))
        assert result.is_error
        assert "Only SELECT" in result.output


# ---------------------------------------------------------------------------
# 5. Truncation at MAX_ROWS
# ---------------------------------------------------------------------------

class TestTruncation:
    def test_truncates_large_result(self, tool: DatabaseTool, tmp_path: Path):
        path = str(tmp_path / "big.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE nums (n INTEGER)")
        for i in range(150):
            conn.execute("INSERT INTO nums VALUES (?)", (i,))
        conn.commit()
        conn.close()

        result = _run(tool.call(
            {"action": "query", "sql": "SELECT * FROM nums", "connection_string": path},
            _ctx(),
        ))
        assert not result.is_error
        assert result.metadata["truncated"]
        assert result.metadata["row_count"] == MAX_ROWS
        assert "truncated" in result.output


# ---------------------------------------------------------------------------
# 6. Connection string / env var handling
# ---------------------------------------------------------------------------

class TestConnectionHandling:
    def test_missing_connection_string_and_env(self, tool: DatabaseTool):
        with patch.dict(os.environ, {}, clear=True):
            # Ensure DATABASE_URL is not set
            os.environ.pop("DATABASE_URL", None)
            result = _run(tool.call(
                {"action": "tables"},
                _ctx(),
            ))
        assert result.is_error
        assert "No connection string" in result.output

    def test_reads_from_env(self, tool: DatabaseTool, db_path: str):
        with patch.dict(os.environ, {"DATABASE_URL": db_path}):
            result = _run(tool.call(
                {"action": "tables"},
                _ctx(),
            ))
        assert not result.is_error
        assert "users" in result.output

    def test_explicit_overrides_env(self, tool: DatabaseTool, db_path: str, tmp_path: Path):
        other = str(tmp_path / "other.db")
        sqlite3.connect(other).close()
        with patch.dict(os.environ, {"DATABASE_URL": db_path}):
            result = _run(tool.call(
                {"action": "tables", "connection_string": other},
                _ctx(),
            ))
        assert not result.is_error
        # other.db is empty, so no tables
        assert "No tables" in result.output

    def test_unsupported_connection_string(self, tool: DatabaseTool):
        result = _run(tool.call(
            {"action": "tables", "connection_string": "postgresql://localhost/mydb"},
            _ctx(),
        ))
        assert result.is_error
        assert "Unsupported database" in result.output


# ---------------------------------------------------------------------------
# 7. Unknown action
# ---------------------------------------------------------------------------

class TestUnknownAction:
    def test_unknown_action(self, tool: DatabaseTool, db_path: str):
        result = _run(tool.call(
            {"action": "drop_everything", "connection_string": db_path},
            _ctx(),
        ))
        assert result.is_error
        assert "Unknown action" in result.output


# ---------------------------------------------------------------------------
# 8. Helper function unit tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_is_sqlite_db(self):
        assert _is_sqlite("test.db")
        assert _is_sqlite("test.sqlite")
        assert _is_sqlite("test.sqlite3")
        assert _is_sqlite(":memory:")
        assert _is_sqlite("PATH/TO/FILE.DB")  # case-insensitive

    def test_is_not_sqlite(self):
        assert not _is_sqlite("postgresql://localhost/db")
        assert not _is_sqlite("mysql://root@localhost/db")
        assert not _is_sqlite("test.txt")

    def test_validate_sql_allows_select(self):
        assert _validate_sql("SELECT * FROM users") is None
        assert _validate_sql("select count(*) from orders") is None

    def test_validate_sql_rejects_writes(self):
        assert _validate_sql("INSERT INTO users VALUES (1)") is not None
        assert _validate_sql("delete from users") is not None

    def test_resolve_connection_string_prefers_input(self):
        with patch.dict(os.environ, {"DATABASE_URL": "env.db"}):
            assert _resolve_connection_string("input.db") == "input.db"

    def test_resolve_connection_string_falls_back_to_env(self):
        with patch.dict(os.environ, {"DATABASE_URL": "env.db"}):
            assert _resolve_connection_string(None) == "env.db"

    def test_resolve_connection_string_empty_when_nothing(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            assert _resolve_connection_string(None) == ""


# ---------------------------------------------------------------------------
# 9. Tool protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_has_required_attributes(self, tool: DatabaseTool):
        assert tool.name == "Database"
        assert isinstance(tool.description, str)
        assert isinstance(tool.input_schema, dict)
        assert tool.is_read_only is True
        assert tool.is_destructive is False

    def test_check_permissions(self, tool: DatabaseTool):
        result = _run(tool.check_permissions({}, _ctx()))
        assert result == {"allowed": True}


# ---------------------------------------------------------------------------
# 10. In-memory database support
# ---------------------------------------------------------------------------

class TestInMemory:
    def test_memory_db(self, tool: DatabaseTool):
        """Using :memory: as connection_string should work."""
        # :memory: creates a fresh empty DB each time
        result = _run(tool.call(
            {"action": "tables", "connection_string": ":memory:"},
            _ctx(),
        ))
        assert not result.is_error
        assert "No tables" in result.output


# ---------------------------------------------------------------------------
# 11. Registry integration
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_database_tool_in_registry(self):
        from duh.tools.registry import get_all_tools
        tools = get_all_tools()
        names = [t.name for t in tools]
        assert "Database" in names
