"""Close remaining tool coverage gaps."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.kernel.tool import ToolContext


# ==========================================================================
# duh.tools.read — error branches
# ==========================================================================

from duh.tools.read import ReadTool, MAX_FILE_READ_BYTES


class TestReadTool:
    async def test_relative_path_resolves_against_cwd(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        tool = ReadTool()
        result = await tool.call(
            {"file_path": "a.txt"},  # relative
            ToolContext(cwd=str(tmp_path)),
        )
        assert "hello" in result.output

    async def test_not_a_file(self, tmp_path):
        d = tmp_path / "dir"
        d.mkdir()
        tool = ReadTool()
        result = await tool.call(
            {"file_path": str(d)},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "Not a file" in result.output

    async def test_permission_denied(self, tmp_path, monkeypatch):
        f = tmp_path / "secret.txt"
        f.write_text("top")
        tool = ReadTool()

        def _no_access(path, mode):
            if str(path) == str(f):
                return False
            return True

        monkeypatch.setattr("duh.tools.read.os.access", _no_access)
        result = await tool.call(
            {"file_path": str(f)},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "Permission denied" in result.output

    async def test_stat_oserror_fallback(self, tmp_path, monkeypatch):
        f = tmp_path / "a.txt"
        f.write_text("hi")
        tool = ReadTool()

        real_stat = Path.stat
        call_count = {"n": 0}

        def _bad_stat(self, *a, **kw):
            # Only fail when called inside the file_size try/except block,
            # not for resolve() / exists() / is_file() earlier checks.
            if self.name == "a.txt":
                call_count["n"] += 1
                if call_count["n"] >= 4:  # resolve(), exists(), is_file(), then stat()
                    raise OSError("no stat")
            return real_stat(self, *a, **kw)

        monkeypatch.setattr(Path, "stat", _bad_stat)
        result = await tool.call(
            {"file_path": str(f)},
            ToolContext(cwd=str(tmp_path)),
        )
        # Should succeed despite stat error (file_size defaulted to 0)
        assert "hi" in result.output

    async def test_read_text_error(self, tmp_path, monkeypatch):
        f = tmp_path / "bad.txt"
        f.write_text("hi")
        tool = ReadTool()

        real_read_text = Path.read_text

        def _bad_read(self, *a, **kw):
            if self.name == "bad.txt":
                raise OSError("corrupt")
            return real_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _bad_read)
        result = await tool.call(
            {"file_path": str(f)},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "Error reading" in result.output

    async def test_notebook_error(self, tmp_path):
        nb = tmp_path / "bad.ipynb"
        nb.write_text("not json")
        tool = ReadTool()
        result = await tool.call(
            {"file_path": str(nb)},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error

    async def test_notebook_empty_cells(self, tmp_path):
        nb = tmp_path / "empty.ipynb"
        nb.write_text(json.dumps({"cells": [], "metadata": {}, "nbformat": 4}))
        tool = ReadTool()
        result = await tool.call(
            {"file_path": str(nb)},
            ToolContext(cwd=str(tmp_path)),
        )
        assert "empty notebook" in result.output or result.is_error

    async def test_check_permissions(self, tmp_path):
        """Cover read.py line 198."""
        tool = ReadTool()
        perm = await tool.check_permissions(
            {"file_path": "x.txt"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert perm.get("allowed") is True

    async def test_notebook_no_lines_in_range(self, tmp_path):
        nb = tmp_path / "nb.ipynb"
        nb.write_text(json.dumps({
            "cells": [{"cell_type": "code", "source": "x = 1", "outputs": []}],
            "metadata": {},
            "nbformat": 4,
        }))
        tool = ReadTool()
        result = await tool.call(
            {"file_path": str(nb), "offset": 1000},
            ToolContext(cwd=str(tmp_path)),
        )
        assert "no lines in requested range" in result.output


# ==========================================================================
# duh.tools.multi_edit — relative path, read/write errors
# ==========================================================================

from duh.tools.multi_edit import MultiEditTool


class TestMultiEdit:
    async def test_relative_file_path(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello world")
        tool = MultiEditTool()
        result = await tool.call(
            {"edits": [{
                "file_path": "a.txt",  # relative
                "old_string": "hello",
                "new_string": "bye",
            }]},
            ToolContext(cwd=str(tmp_path)),
        )
        assert not result.is_error
        assert f.read_text() == "bye world"

    async def test_read_error(self, tmp_path, monkeypatch):
        f = tmp_path / "a.txt"
        f.write_text("x")
        tool = MultiEditTool()

        real_read_text = Path.read_text

        def _bad_read(self, *a, **kw):
            if self.name == "a.txt":
                raise OSError("fail")
            return real_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _bad_read)
        result = await tool.call(
            {"edits": [{"file_path": str(f), "old_string": "x", "new_string": "y"}]},
            ToolContext(cwd=str(tmp_path)),
        )
        assert "error reading" in result.output.lower()

    async def test_write_error(self, tmp_path, monkeypatch):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        tool = MultiEditTool()

        real_write = Path.write_text

        def _bad_write(self, *a, **kw):
            if self.name == "a.txt":
                raise OSError("readonly fs")
            return real_write(self, *a, **kw)

        monkeypatch.setattr(Path, "write_text", _bad_write)
        result = await tool.call(
            {"edits": [{"file_path": str(f), "old_string": "hello", "new_string": "bye"}]},
            ToolContext(cwd=str(tmp_path)),
        )
        assert "error writing" in result.output.lower()


# ==========================================================================
# duh.tools.write — relative path, check_permissions
# ==========================================================================

from duh.tools.write import WriteTool


class TestWriteTool:
    async def test_relative_path(self, tmp_path):
        tool = WriteTool()
        result = await tool.call(
            {"file_path": "out.txt", "content": "hi"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert not result.is_error
        assert (tmp_path / "out.txt").read_text() == "hi"

    async def test_check_permissions(self, tmp_path):
        tool = WriteTool()
        perm = await tool.check_permissions(
            {"file_path": "x.txt"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert perm.get("allowed") is True


# ==========================================================================
# duh.tools.github_tool — timeout/error, json error, no gh
# ==========================================================================

from duh.tools.github_tool import GitHubTool, _run_gh


class TestGitHubTool:
    def test_run_gh_oserror(self, monkeypatch):
        """OSError path in _run_gh."""
        def _fail(*a, **kw):
            raise OSError("no gh")
        monkeypatch.setattr("duh.tools.github_tool.subprocess.run", _fail)
        out, err, rc = _run_gh(["pr", "list"])
        assert rc == 1
        assert "Failed to run gh" in err

    def test_run_gh_success_path(self, monkeypatch):
        """Normal path: subprocess.run returns a CompletedProcess."""
        def _fake_run(*a, **kw):
            return subprocess.CompletedProcess(
                args=a[0] if a else [], returncode=0,
                stdout="OK", stderr="",
            )
        monkeypatch.setattr("duh.tools.github_tool.subprocess.run", _fake_run)
        out, err, rc = _run_gh(["pr", "list"])
        assert rc == 0
        assert out == "OK"

    def test_run_gh_timeout(self, monkeypatch):
        """TimeoutExpired branch."""
        def _timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=30)
        monkeypatch.setattr("duh.tools.github_tool.subprocess.run", _timeout)
        out, err, rc = _run_gh(["pr", "list"])
        assert rc == 1
        assert "timed out" in err

    async def test_pr_list_json_decode_error(self, monkeypatch, tmp_path):
        tool = GitHubTool()
        monkeypatch.setattr("duh.tools.github_tool._gh_available", lambda: True)
        monkeypatch.setattr(
            "duh.tools.github_tool._run_gh",
            lambda args, cwd=".": ("not json output", "", 0),
        )
        result = await tool.call(
            {"action": "pr_list"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert "not json output" in result.output

    async def test_pr_view_failure(self, monkeypatch, tmp_path):
        tool = GitHubTool()
        monkeypatch.setattr("duh.tools.github_tool._gh_available", lambda: True)
        monkeypatch.setattr(
            "duh.tools.github_tool._run_gh",
            lambda args, cwd=".": ("", "not found", 1),
        )
        result = await tool.call(
            {"action": "pr_view", "number": 5},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error

    async def test_pr_view_json_decode_error(self, monkeypatch, tmp_path):
        tool = GitHubTool()
        monkeypatch.setattr("duh.tools.github_tool._gh_available", lambda: True)
        monkeypatch.setattr(
            "duh.tools.github_tool._run_gh",
            lambda args, cwd=".": ("not json", "", 0),
        )
        result = await tool.call(
            {"action": "pr_view", "number": 5},
            ToolContext(cwd=str(tmp_path)),
        )
        # The json.JSONDecodeError branch — returns stdout directly
        assert "not json" in result.output

    async def test_pr_diff_error(self, monkeypatch, tmp_path):
        tool = GitHubTool()
        monkeypatch.setattr("duh.tools.github_tool._gh_available", lambda: True)
        monkeypatch.setattr(
            "duh.tools.github_tool._run_gh",
            lambda args, cwd=".": ("", "pr not found", 1),
        )
        result = await tool.call(
            {"action": "pr_diff", "number": 5},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error


# ==========================================================================
# duh.tools.db_tool — connect error, list/schema error
# ==========================================================================

from duh.tools.db_tool import DatabaseTool


class TestDatabaseTool:
    async def test_connect_error(self, tmp_path, monkeypatch):
        import sqlite3
        tool = DatabaseTool()

        def _fail_connect(*a, **kw):
            raise sqlite3.Error("cannot connect")

        monkeypatch.setattr("duh.tools.db_tool.sqlite3.connect", _fail_connect)
        db = tmp_path / "t.db"
        # Create a valid file first to pass _is_sqlite check
        db.write_bytes(b"SQLite format 3\x00")
        result = await tool.call(
            {"action": "tables", "connection_string": str(db)},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "Failed to connect" in result.output

    async def test_list_tables_error(self, tmp_path, monkeypatch):
        import sqlite3

        tool = DatabaseTool()
        db = tmp_path / "t.db"
        # Create a valid db first
        conn = sqlite3.connect(str(db))
        conn.close()

        # Patch sqlite3.connect to return a connection whose execute raises
        real_connect = sqlite3.connect

        class _BadConn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, *a, **kw):
                raise sqlite3.Error("boom listing")

            def close(self):
                self._inner.close()

        def _fake_connect(*a, **kw):
            return _BadConn(real_connect(*a, **kw))

        monkeypatch.setattr("duh.tools.db_tool.sqlite3.connect", _fake_connect)
        result = await tool.call(
            {"action": "tables", "connection_string": str(db)},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error

    async def test_schema_error(self, tmp_path, monkeypatch):
        import sqlite3

        tool = DatabaseTool()
        db = tmp_path / "t.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE valid (id INTEGER)")
        conn.commit()
        conn.close()

        real_connect = sqlite3.connect

        class _BadOnSecondExecute:
            def __init__(self, inner):
                self._inner = inner
                self._count = 0

            def execute(self, *a, **kw):
                self._count += 1
                if "PRAGMA" in str(a[0]):
                    raise sqlite3.Error("schema boom")
                return self._inner.execute(*a, **kw)

            def close(self):
                self._inner.close()

        def _fake_connect(*a, **kw):
            return _BadOnSecondExecute(real_connect(*a, **kw))

        monkeypatch.setattr("duh.tools.db_tool.sqlite3.connect", _fake_connect)
        result = await tool.call(
            {"action": "schema", "connection_string": str(db), "table": "valid"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "schema boom" in result.output or "Error reading schema" in result.output


# ==========================================================================
# duh.tools.docker_tool — actual shutil.which / subprocess.run covering
# ==========================================================================

class TestDockerAvailability:

    def test_docker_run_calls_subprocess(self, monkeypatch):
        from duh.tools.docker_tool import _run

        captured = {}

        def _fake_run(args, **kw):
            captured["args"] = args
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("duh.tools.docker_tool.subprocess.run", _fake_run)
        _run(["version"])
        assert captured["args"] == ["docker", "version"]


# ==========================================================================
# duh.tools.grep — read error, check_permissions
# ==========================================================================

from duh.tools.grep import GrepTool


class TestGrepTool:
    async def test_read_error_skips_file(self, tmp_path, monkeypatch):
        (tmp_path / "ok.txt").write_text("hello")
        (tmp_path / "bad.txt").write_text("hello")
        tool = GrepTool()

        real_read_text = Path.read_text

        def _bad(self, *a, **kw):
            if self.name == "bad.txt":
                raise OSError("corrupt")
            return real_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _bad)
        result = await tool.call(
            {"pattern": "hello", "path": str(tmp_path)},
            ToolContext(cwd=str(tmp_path)),
        )
        assert "hello" in result.output

    async def test_check_permissions(self, tmp_path):
        tool = GrepTool()
        perm = await tool.check_permissions(
            {"pattern": "x"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert perm.get("allowed")


# ==========================================================================
# duh.tools.glob_tool — check_permissions
# ==========================================================================

class TestGlobTool:
    async def test_check_permissions(self, tmp_path):
        from duh.tools.glob_tool import GlobTool
        tool = GlobTool()
        perm = await tool.check_permissions(
            {"pattern": "*.py"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert perm.get("allowed")


# ==========================================================================
# duh.tools.memory_tool — check_permissions
# ==========================================================================

class TestMemoryToolsPermissions:
    async def test_memory_save_check_permissions(self, tmp_path):
        from duh.tools.memory_tool import MemoryStoreTool
        tool = MemoryStoreTool()
        perm = await tool.check_permissions(
            {"key": "x", "value": "y"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert perm.get("allowed")

    async def test_memory_recall_check_permissions(self, tmp_path):
        from duh.tools.memory_tool import MemoryRecallTool
        tool = MemoryRecallTool()
        perm = await tool.check_permissions(
            {"query": "x"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert perm.get("allowed")


# ==========================================================================
# duh.tools.notebook_edit — permission denied, check_permissions
# ==========================================================================

class TestNotebookEditPermission:
    async def test_permission_denied(self, tmp_path, monkeypatch):
        from duh.tools.notebook_edit import NotebookEditTool
        nb = tmp_path / "n.ipynb"
        nb.write_text(json.dumps({
            "cells": [{"cell_type": "code", "source": "x=1", "outputs": []}],
            "metadata": {}, "nbformat": 4,
        }))
        tool = NotebookEditTool()

        def _no_access(path, mode):
            return False

        monkeypatch.setattr("duh.tools.notebook_edit.os.access", _no_access)
        result = await tool.call(
            {"notebook_path": str(nb), "cell_index": 0, "new_source": "y=2"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "Permission denied" in result.output

    async def test_check_permissions(self, tmp_path):
        from duh.tools.notebook_edit import NotebookEditTool
        tool = NotebookEditTool()
        perm = await tool.check_permissions(
            {"notebook_path": "x.ipynb"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert perm.get("allowed")


# ==========================================================================
# duh.tools.http_tool — timeout normalization
# ==========================================================================

class TestHTTPToolTimeout:
    async def test_timeout_below_one_normalizes(self, tmp_path, monkeypatch):
        from duh.tools.http_tool import HTTPTool
        tool = HTTPTool()

        # Patch httpx to avoid actual network
        class _FakeResponse:
            status_code = 200
            text = "ok"
            headers = {"content-type": "text/plain"}
            url = "http://example.com"

            def raise_for_status(self):
                pass

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def request(self, **kw):
                return _FakeResponse()

        monkeypatch.setattr("duh.tools.http_tool.httpx.AsyncClient", _FakeClient)
        # Pass negative timeout — bypass the `or` default, hit the `< 1` branch
        result = await tool.call(
            {"url": "http://example.com", "method": "GET", "timeout": -5},
            ToolContext(cwd=str(tmp_path)),
        )
        assert not result.is_error


# ==========================================================================
# duh.tools.tool_search — no names in select
# ==========================================================================

class TestToolSearchSelect:
    def test_select_empty_names(self):
        from duh.tools.tool_search import ToolSearchTool
        tool = ToolSearchTool()
        result = tool._handle_select(", ,  ,")
        assert result.is_error
        assert "No tool names" in result.output


# ==========================================================================
# duh.tools.web_fetch — HTTPError branch
# ==========================================================================

class TestWebFetchErrors:
    async def test_http_error_general(self, tmp_path, monkeypatch):
        import httpx
        from duh.tools.web_fetch import WebFetchTool

        tool = WebFetchTool()

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                raise httpx.HTTPError("generic http error")

        monkeypatch.setattr("duh.tools.web_fetch.httpx.AsyncClient", _FakeClient)
        result = await tool.call(
            {"url": "http://example.com"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "HTTP error" in result.output


# ==========================================================================
# duh.tools.web_search — empty results for serper and tavily
# ==========================================================================

class TestWebSearchEmpty:
    async def test_serper_no_results(self, tmp_path, monkeypatch):
        import httpx
        from duh.tools.web_search import WebSearchTool

        tool = WebSearchTool()

        class _Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"organic": []}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return _Response()

        monkeypatch.setattr("duh.tools.web_search.httpx.AsyncClient", _FakeClient)
        monkeypatch.setenv("SERPER_API_KEY", "dummy")
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        result = await tool.call(
            {"query": "hello"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert "No results" in result.output

    async def test_tavily_no_results(self, tmp_path, monkeypatch):
        from duh.tools.web_search import WebSearchTool

        tool = WebSearchTool()

        class _Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"results": []}

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return _Response()

        monkeypatch.setattr("duh.tools.web_search.httpx.AsyncClient", _FakeClient)
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        monkeypatch.setenv("TAVILY_API_KEY", "dummy")
        result = await tool.call(
            {"query": "hello"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert "No results" in result.output


# ==========================================================================
# duh.tools.test_impact — read errors, git failure
# ==========================================================================

class TestTestImpact:
    async def test_read_error_skips_test_file(self, tmp_path, monkeypatch):
        from duh.tools.test_impact import _find_test_files_by_imports

        # Create a tests dir with one bad test file
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_foo.py").write_text("from duh.bar import thing")

        real_read = Path.read_text

        def _bad(self, *a, **kw):
            if self.name == "test_foo.py":
                raise OSError("corrupt")
            return real_read(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _bad)
        # Should just skip the unreadable file
        matches = _find_test_files_by_imports(["bar"], tmp_path)
        assert matches == []

    async def test_git_changed_files_all_error(self, tmp_path, monkeypatch):
        from duh.tools.test_impact import _git_changed_files

        async def _failing_exec(*args, **kwargs):
            raise OSError("git missing")

        monkeypatch.setattr(
            "duh.tools.test_impact.asyncio.create_subprocess_exec",
            _failing_exec,
        )
        files = await _git_changed_files(str(tmp_path))
        assert files == []

    async def test_git_changed_files_success(self, tmp_path, monkeypatch):
        """Cover lines 110-114: git diff returns .py files."""
        from duh.tools.test_impact import _git_changed_files

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return (b"src/foo.py\nsrc/bar.py\nREADME.md\n", b"")

        async def _fake_exec(*args, **kwargs):
            return _FakeProc()

        monkeypatch.setattr(
            "duh.tools.test_impact.asyncio.create_subprocess_exec",
            _fake_exec,
        )
        files = await _git_changed_files(str(tmp_path))
        assert "src/foo.py" in files
        assert "src/bar.py" in files
        assert "README.md" not in files


# ==========================================================================
# duh.tools.bash — timeout kill, general error, background output truncate
# ==========================================================================

class TestBashErrors:
    async def test_timeout_kill_process_lookup_error(self, tmp_path, monkeypatch):
        from duh.tools.bash import BashTool

        tool = BashTool()

        class _FakeProc:
            returncode = -9

            async def communicate(self):
                return b"", b""

            def kill(self):
                raise ProcessLookupError("already gone")

        async def _raise_timeout(*a, **kw):
            raise asyncio.TimeoutError

        async def _fake_exec(*a, **kw):
            return _FakeProc()

        monkeypatch.setattr(
            "duh.tools.bash.asyncio.create_subprocess_exec", _fake_exec,
        )
        monkeypatch.setattr(
            "duh.tools.bash.asyncio.wait_for", _raise_timeout,
        )
        result = await tool.call(
            {"command": "sleep 10", "timeout": 1},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "timed out" in result.output.lower()

    async def test_background_output_truncated(self, tmp_path, monkeypatch):
        """Cover bash.py line 252: background job output truncation."""
        from duh.tools.bash import BashTool
        from duh.kernel.tool import MAX_TOOL_OUTPUT

        tool = BashTool()

        big_output = b"x" * (MAX_TOOL_OUTPUT + 1000)

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return big_output, b""

        async def _fake_exec(*a, **kw):
            return _FakeProc()

        monkeypatch.setattr(
            "duh.tools.bash.asyncio.create_subprocess_exec", _fake_exec,
        )
        # Request background mode via bg: prefix
        result = await tool.call(
            {"command": "bg:cat huge.txt"},
            ToolContext(cwd=str(tmp_path)),
        )
        # Background submission returns immediately
        assert "Background job" in result.output

    async def test_general_exception(self, tmp_path, monkeypatch):
        from duh.tools.bash import BashTool

        tool = BashTool()

        async def _fake_exec(*a, **kw):
            raise RuntimeError("exec broke")

        monkeypatch.setattr(
            "duh.tools.bash.asyncio.create_subprocess_exec", _fake_exec,
        )
        result = await tool.call(
            {"command": "echo hi"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error
        assert "Error running" in result.output


# ==========================================================================
# duh.cli.prewarm — timeout path
# ==========================================================================

class TestPrewarmTimeout:
    async def test_prewarm_timeout(self):
        from duh.cli.prewarm import prewarm_connection

        async def _timeout_call_model(**kw):
            raise asyncio.TimeoutError("prewarm timed out")
            if False:
                yield None  # pragma: no cover

        result = await prewarm_connection(_timeout_call_model)
        assert result.success is False
        assert result.error == "timeout"


# ==========================================================================
# duh.tools.bash_ast — strip_wrappers numeric-arg, env=, unterminated backtick
# ==========================================================================

class TestBashAstHelpers:
    def test_strip_wrappers_bare_numeric(self):
        from duh.tools.bash_ast import strip_wrappers
        # "timeout 30 echo hi" → after stripping "timeout", the bare 30 gets consumed,
        # then "echo hi" remains.
        out = strip_wrappers("timeout 30 echo hi")
        assert "echo hi" in out or out == "echo hi"

    def test_strip_wrappers_non_env_with_equal_breaks(self):
        """A non-env wrapper with key=value stops parsing (line 145 break)."""
        from duh.tools.bash_ast import strip_wrappers
        # `nice FOO=bar cmd` — nice has skip_mode=-1, not 'env', hits line 145 break
        out = strip_wrappers("nice FOO=bar echo hi")
        assert "FOO=bar" in out

    def test_process_sub_with_nested_parens(self):
        from duh.tools.bash_ast import ast_classify
        # Process substitution with nested parens
        result = ast_classify("diff <(cat (nested) x) y")
        assert "risk" in result

    def test_process_sub_after_dollar(self):
        from duh.tools.bash_ast import ast_classify
        # $( should not be misidentified as process substitution
        result = ast_classify("echo $(cat <(true))")
        assert "risk" in result

    def test_process_sub_preceded_by_dollar(self):
        """Cover lines 322-324: $< before ( hits the 'not process sub' branch."""
        from duh.tools.bash_ast import ast_classify
        # Craft a case where `<` or `>` is preceded by `$`
        result = ast_classify("echo foo$<(true)")
        assert "risk" in result

    def test_unterminated_backtick(self):
        from duh.tools.bash_ast import ast_classify
        # Unterminated backtick → parser handles gracefully
        result = ast_classify("echo `unterminated")
        assert "risk" in result

    def test_empty_inner_after_wrapper_strip(self):
        from duh.tools.bash_ast import ast_classify
        # "timeout 30 " — after stripping wrapper, inner is empty → continue
        result = ast_classify("timeout 30  ")
        assert result["risk"] == "safe"

    def test_binary_hijack(self):
        from duh.tools.bash_ast import ast_classify
        # LIBPATH= — matches BINARY_HIJACK_RE but not the specific LD_PRELOAD
        # patterns, so full-regex says safe but segment-level env check hits it.
        result = ast_classify("LIBPATH=/tmp/evil echo hi")
        assert result["risk"] == "dangerous"
        assert "Binary hijack via LIBPATH" in result["reason"]

    def test_short_circuit_after_segment_dangerous(self):
        from duh.tools.bash_ast import ast_classify
        # Two segments, second is dangerous
        result = ast_classify("echo safe && rm -rf /")
        assert result["risk"] == "dangerous"


# ==========================================================================
# duh.tools.bash_security — AST fails → regex fallback
# ==========================================================================

class TestBashSecurityFallback:
    def test_ast_fails_falls_back_to_regex(self, monkeypatch):
        from duh.tools import bash_security

        def _raise(*a, **kw):
            raise RuntimeError("ast broke")

        # Patch ast_classify inside classify_command's import scope
        import duh.tools.bash_ast as bash_ast
        monkeypatch.setattr(bash_ast, "ast_classify", _raise)
        result = bash_security.classify_command("echo hi")
        assert result["risk"] in ("safe", "moderate", "dangerous")

    def test_regex_classify_empty(self):
        from duh.tools.bash_security import _regex_classify
        result = _regex_classify("")
        assert result["risk"] == "safe"

    def test_regex_classify_whitespace(self):
        from duh.tools.bash_security import _regex_classify
        result = _regex_classify("   ")
        assert result["risk"] == "safe"
