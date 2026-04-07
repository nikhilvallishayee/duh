"""End-to-end integration tests for D.U.H. hook system (ADR-019 T2).

Proves that the real hook executor fires real shell commands and that
the full config -> registry -> execute pipeline works end-to-end.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from duh.hooks import (
    HookEvent,
    HookRegistry,
    HookResult,
    execute_hooks,
)
from duh.config import load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine synchronously (works even if no loop exists)."""
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _make_settings(tmp_path: Path, hooks_config: dict) -> Path:
    """Create a .duh/settings.json with the given hooks config.

    Returns the project root (tmp_path itself).
    """
    duh_dir = tmp_path / ".duh"
    duh_dir.mkdir()
    settings = {"hooks": hooks_config}
    (duh_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: config -> registry round-trip
# ---------------------------------------------------------------------------


class TestHookRegistryFromConfig:
    """Verify that HookRegistry.from_config correctly parses settings."""

    def test_parses_pre_and_post_tool_use(self, tmp_path: Path):
        log_file = tmp_path / "hook.log"
        config = {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"echo PRE >> {log_file}",
                            "name": "log-pre",
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"echo POST >> {log_file}",
                            "name": "log-post",
                        }
                    ],
                }
            ],
        }
        registry = HookRegistry.from_config({"hooks": config})
        all_hooks = registry.list_all()
        assert len(all_hooks) == 2
        assert all_hooks[0].event == HookEvent.PRE_TOOL_USE
        assert all_hooks[1].event == HookEvent.POST_TOOL_USE

    def test_matcher_filtering(self, tmp_path: Path):
        config = {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "echo bash"}],
                },
                {
                    "matcher": "Read",
                    "hooks": [{"type": "command", "command": "echo read"}],
                },
            ],
        }
        registry = HookRegistry.from_config({"hooks": config})
        bash_hooks = registry.get_hooks(HookEvent.PRE_TOOL_USE, matcher_value="Bash")
        read_hooks = registry.get_hooks(HookEvent.PRE_TOOL_USE, matcher_value="Read")
        all_hooks = registry.get_hooks(HookEvent.PRE_TOOL_USE)
        assert len(bash_hooks) == 1
        assert len(read_hooks) == 1
        assert len(all_hooks) == 2

    def test_wildcard_matcher(self):
        """Empty matcher matches everything."""
        config = {
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "echo all"}],
                },
            ],
        }
        registry = HookRegistry.from_config({"hooks": config})
        hooks = registry.get_hooks(HookEvent.PRE_TOOL_USE, matcher_value="AnyTool")
        assert len(hooks) == 1


# ---------------------------------------------------------------------------
# Tests: real shell execution (the core of T2)
# ---------------------------------------------------------------------------


class TestHookShellExecution:
    """Prove that command hooks actually run real shell commands."""

    def test_pre_tool_use_writes_to_file(self, tmp_path: Path):
        """A PreToolUse hook runs a shell command that writes to a file."""
        log_file = tmp_path / "hook.log"

        registry = HookRegistry.from_config({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"echo 'PRE_TOOL_USE fired' >> {log_file}",
                                "name": "pre-log",
                            }
                        ],
                    }
                ],
            }
        })

        results = _run_async(execute_hooks(
            registry,
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash", "input": {"command": "ls"}},
            matcher_value="Bash",
        ))

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].exit_code == 0
        assert log_file.exists(), "Hook shell command did not create the log file"
        content = log_file.read_text()
        assert "PRE_TOOL_USE fired" in content

    def test_post_tool_use_appends_to_file(self, tmp_path: Path):
        """A PostToolUse hook appends to an existing file."""
        log_file = tmp_path / "hook.log"
        log_file.write_text("existing\n")

        registry = HookRegistry.from_config({
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"echo 'POST_TOOL_USE done' >> {log_file}",
                                "name": "post-log",
                            }
                        ],
                    }
                ],
            }
        })

        results = _run_async(execute_hooks(
            registry,
            HookEvent.POST_TOOL_USE,
            {"tool_name": "Bash", "output": "some output"},
            matcher_value="Bash",
        ))

        assert len(results) == 1
        assert results[0].success is True
        content = log_file.read_text()
        assert "existing" in content
        assert "POST_TOOL_USE done" in content

    def test_pre_and_post_hooks_full_lifecycle(self, tmp_path: Path):
        """Fire PreToolUse then PostToolUse and verify both wrote to the log."""
        log_file = tmp_path / "hook.log"

        config = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"echo 'PRE:Bash' >> {log_file}",
                                "name": "pre-bash-log",
                            }
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"echo 'POST:Bash' >> {log_file}",
                                "name": "post-bash-log",
                            }
                        ],
                    }
                ],
            }
        }
        registry = HookRegistry.from_config(config)

        # Simulate: pre -> tool runs -> post
        pre_results = _run_async(execute_hooks(
            registry,
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash", "input": {"command": "ls -la"}},
            matcher_value="Bash",
        ))
        post_results = _run_async(execute_hooks(
            registry,
            HookEvent.POST_TOOL_USE,
            {"tool_name": "Bash", "output": "file1\nfile2"},
            matcher_value="Bash",
        ))

        assert all(r.success for r in pre_results)
        assert all(r.success for r in post_results)

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2
        assert "PRE:Bash" in lines[0]
        assert "POST:Bash" in lines[1]

    def test_hook_receives_json_on_stdin(self, tmp_path: Path):
        """Shell hooks receive event data as JSON on stdin."""
        output_file = tmp_path / "stdin_capture.json"

        # The hook reads stdin (JSON) and writes it to a file
        registry = HookRegistry.from_config({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"cat > {output_file}",
                                "name": "stdin-capture",
                            }
                        ],
                    }
                ],
            }
        })

        data = {"tool_name": "Read", "input": {"path": "/tmp/foo.py"}}
        results = _run_async(execute_hooks(
            registry,
            HookEvent.PRE_TOOL_USE,
            data,
            matcher_value="Read",
        ))

        assert results[0].success is True
        captured = json.loads(output_file.read_text())
        assert captured["tool_name"] == "Read"
        assert captured["input"]["path"] == "/tmp/foo.py"

    def test_hook_stdout_captured_in_result(self, tmp_path: Path):
        """Hook stdout is captured in HookResult.output."""
        registry = HookRegistry.from_config({
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo 'hello from hook'",
                                "name": "echo-hook",
                            }
                        ],
                    }
                ],
            }
        })

        results = _run_async(execute_hooks(
            registry,
            HookEvent.SESSION_START,
            {"session_id": "test-123"},
        ))

        assert results[0].success is True
        assert "hello from hook" in results[0].output

    def test_failing_hook_returns_error(self, tmp_path: Path):
        """A hook that exits non-zero has success=False and captures stderr."""
        registry = HookRegistry.from_config({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo 'bad things' >&2 && exit 1",
                                "name": "fail-hook",
                            }
                        ],
                    }
                ],
            }
        })

        results = _run_async(execute_hooks(
            registry,
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"},
        ))

        assert results[0].success is False
        assert results[0].exit_code == 1
        assert "bad things" in results[0].error

    def test_error_isolation_other_hooks_still_run(self, tmp_path: Path):
        """One hook failing does not prevent subsequent hooks from running."""
        log_file = tmp_path / "isolation.log"

        registry = HookRegistry.from_config({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "exit 1",
                                "name": "failing-hook",
                            },
                            {
                                "type": "command",
                                "command": f"echo 'survived' >> {log_file}",
                                "name": "surviving-hook",
                            },
                        ],
                    }
                ],
            }
        })

        results = _run_async(execute_hooks(
            registry,
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"},
        ))

        assert len(results) == 2
        assert results[0].success is False
        assert results[1].success is True
        assert "survived" in log_file.read_text()

    def test_matcher_prevents_unmatched_hooks(self, tmp_path: Path):
        """A hook with matcher='Read' does not fire for matcher_value='Bash'."""
        log_file = tmp_path / "no_fire.log"

        registry = HookRegistry.from_config({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"echo 'should not fire' >> {log_file}",
                                "name": "read-only",
                            }
                        ],
                    }
                ],
            }
        })

        results = _run_async(execute_hooks(
            registry,
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"},
            matcher_value="Bash",
        ))

        assert len(results) == 0
        assert not log_file.exists()


# ---------------------------------------------------------------------------
# Tests: config file -> registry -> execution (full pipeline)
# ---------------------------------------------------------------------------


class TestFullPipelineFromConfig:
    """Load hooks from .duh/settings.json on disk, fire them, verify effect."""

    def test_load_config_and_execute_hooks(self, tmp_path: Path):
        """Full pipeline: settings.json -> load_config -> HookRegistry -> execute."""
        log_file = tmp_path / "pipeline.log"
        project_root = _make_settings(tmp_path, {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"echo 'config-loaded' >> {log_file}",
                            "name": "from-config",
                        }
                    ],
                }
            ],
        })

        # Load config from the temp project root (just like duh would)
        config = load_config(cwd=str(project_root))
        assert "PreToolUse" in config.hooks

        # Build registry from the loaded config
        registry = HookRegistry.from_config({"hooks": config.hooks})
        hooks = registry.get_hooks(HookEvent.PRE_TOOL_USE, matcher_value="Bash")
        assert len(hooks) == 1
        assert hooks[0].name == "from-config"

        # Execute
        results = _run_async(execute_hooks(
            registry,
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash", "input": {"command": "whoami"}},
            matcher_value="Bash",
        ))

        assert results[0].success is True
        assert log_file.exists()
        assert "config-loaded" in log_file.read_text()

    def test_multiple_events_from_config(self, tmp_path: Path):
        """Config with multiple event types all fire correctly."""
        log_file = tmp_path / "multi.log"
        project_root = _make_settings(tmp_path, {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"echo 'SESSION_START' >> {log_file}",
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"echo 'PRE_TOOL' >> {log_file}",
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"echo 'POST_TOOL' >> {log_file}",
                        }
                    ],
                }
            ],
            "SessionEnd": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"echo 'SESSION_END' >> {log_file}",
                        }
                    ],
                }
            ],
        })

        config = load_config(cwd=str(project_root))
        registry = HookRegistry.from_config({"hooks": config.hooks})

        # Simulate a full session lifecycle
        for event, data in [
            (HookEvent.SESSION_START, {"session_id": "s1"}),
            (HookEvent.PRE_TOOL_USE, {"tool_name": "Bash"}),
            (HookEvent.POST_TOOL_USE, {"tool_name": "Bash", "output": "ok"}),
            (HookEvent.SESSION_END, {"session_id": "s1"}),
        ]:
            results = _run_async(execute_hooks(registry, event, data))
            assert all(r.success for r in results), f"Failed on {event}"

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 4
        assert "SESSION_START" in lines[0]
        assert "PRE_TOOL" in lines[1]
        assert "POST_TOOL" in lines[2]
        assert "SESSION_END" in lines[3]
