"""Tests for per-hook filesystem namespacing (ADR-054, 7.4)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from duh.hooks import HookConfig, HookContext, HookContextRegistry, HookEvent, HookFSViolation, HookType


# ---------------------------------------------------------------------------
# 7.4.1 — HookContext basic lifecycle
# ---------------------------------------------------------------------------


def test_hook_context_creates_tmp_dir() -> None:
    ctx = HookContext(hook_name="test-hook")
    assert ctx.tmp_dir.exists()
    assert ctx.tmp_dir.is_dir()
    ctx.cleanup()


def test_hook_context_tmp_dir_is_unique() -> None:
    ctx1 = HookContext(hook_name="hook-a")
    ctx2 = HookContext(hook_name="hook-b")
    assert ctx1.tmp_dir != ctx2.tmp_dir
    ctx1.cleanup()
    ctx2.cleanup()


def test_hook_context_cleanup_removes_dir() -> None:
    ctx = HookContext(hook_name="test-hook")
    tmp = ctx.tmp_dir
    ctx.cleanup()
    assert not tmp.exists()


# ---------------------------------------------------------------------------
# 7.4.2 — HookFSViolation exception
# ---------------------------------------------------------------------------


def test_hook_fs_violation_is_exception() -> None:
    exc = HookFSViolation("test violation")
    assert isinstance(exc, Exception)
    assert "test violation" in str(exc)


def test_hook_fs_violation_is_permission_error() -> None:
    assert issubclass(HookFSViolation, PermissionError)


# ---------------------------------------------------------------------------
# 7.4.3 — ctx.open() namespace enforcement
# ---------------------------------------------------------------------------


def test_ctx_open_allows_write_inside_namespace(tmp_path: Path) -> None:
    ctx = HookContext(hook_name="test-hook")
    target = ctx.tmp_dir / "log.txt"
    with ctx.open(target, "w") as f:
        f.write("ok")
    assert target.read_text() == "ok"
    ctx.cleanup()


def test_ctx_open_blocks_write_outside_namespace(tmp_path: Path) -> None:
    ctx = HookContext(hook_name="test-hook")
    outside = tmp_path / "evil.txt"
    with pytest.raises(HookFSViolation, match="wrote outside namespace"):
        ctx.open(outside, "w")
    ctx.cleanup()


def test_ctx_open_blocks_read_outside_namespace() -> None:
    ctx = HookContext(hook_name="test-hook")
    with pytest.raises(HookFSViolation, match="read outside namespace"):
        ctx.open("/etc/passwd", "r")
    ctx.cleanup()


def test_ctx_open_allows_read_in_allowed_read_set(tmp_path: Path) -> None:
    readable = tmp_path / "data.txt"
    readable.write_text("readable content")
    ctx = HookContext(hook_name="test-hook")
    ctx.allowed_read = frozenset({tmp_path})
    with ctx.open(readable, "r") as f:
        assert f.read() == "readable content"
    ctx.cleanup()


def test_ctx_open_allows_read_inside_tmp_dir() -> None:
    ctx = HookContext(hook_name="test-hook")
    f_path = ctx.tmp_dir / "mine.txt"
    f_path.write_text("internal")
    with ctx.open(f_path, "r") as f:
        assert f.read() == "internal"
    ctx.cleanup()


def test_ctx_open_blocks_append_outside_namespace(tmp_path: Path) -> None:
    ctx = HookContext(hook_name="test-hook")
    outside = tmp_path / "append-target.txt"
    outside.write_text("existing")
    with pytest.raises(HookFSViolation, match="wrote outside namespace"):
        ctx.open(outside, "a")
    ctx.cleanup()


# ---------------------------------------------------------------------------
# 7.4.4 — sandbox field on HookConfig
# ---------------------------------------------------------------------------


def test_hook_config_sandbox_defaults_false() -> None:
    cfg = HookConfig(name="my-hook", event=HookEvent.POST_TOOL_USE, hook_type=HookType.FUNCTION)
    assert cfg.sandbox is False


def test_hook_config_sandbox_can_be_true() -> None:
    cfg = HookConfig(name="my-hook", event=HookEvent.POST_TOOL_USE, hook_type=HookType.FUNCTION, sandbox=True)
    assert cfg.sandbox is True


# ---------------------------------------------------------------------------
# 7.4.5 — sandboxed hooks receive HookContext at fire time
# ---------------------------------------------------------------------------


def test_sandboxed_hook_receives_context() -> None:
    from duh.hooks import fire_hook

    received_ctx: list = []

    def my_hook(event: HookEvent, data: dict, ctx: HookContext | None = None) -> None:
        received_ctx.append(ctx)

    config = HookConfig(
        name="sandbox-test",
        event=HookEvent.POST_TOOL_USE,
        hook_type=HookType.FUNCTION,
        sandbox=True,
        callback=my_hook,
    )
    fire_hook(HookEvent.POST_TOOL_USE, {"tool": "Bash"}, hooks=[(config, my_hook)])
    assert len(received_ctx) == 1
    assert isinstance(received_ctx[0], HookContext)
    # tmp_dir is cleaned up after fire, so it no longer exists
    assert not received_ctx[0].tmp_dir.exists()


def test_non_sandboxed_hook_receives_no_context() -> None:
    from duh.hooks import fire_hook

    received_kwargs: list = []

    def my_hook(event: HookEvent, data: dict, **kwargs) -> None:
        received_kwargs.append(kwargs)

    config = HookConfig(
        name="non-sandbox",
        event=HookEvent.POST_TOOL_USE,
        hook_type=HookType.FUNCTION,
        sandbox=False,
        callback=my_hook,
    )
    fire_hook(HookEvent.POST_TOOL_USE, {"tool": "Bash"}, hooks=[(config, my_hook)])
    assert len(received_kwargs) == 1
    assert "ctx" not in received_kwargs[0]


# ---------------------------------------------------------------------------
# 7.4.6 — built-in security hooks are sandboxed
# ---------------------------------------------------------------------------


def test_builtin_security_hooks_are_sandboxed() -> None:
    from duh.hooks import get_builtin_hooks

    for hook_cfg in get_builtin_hooks():
        if "security" in hook_cfg.name.lower():
            assert hook_cfg.sandbox is True, (
                f"Built-in hook '{hook_cfg.name}' should have sandbox=True"
            )


# ---------------------------------------------------------------------------
# 7.4.7 — HookContextRegistry bulk cleanup
# ---------------------------------------------------------------------------


def test_session_end_cleans_up_all_hook_contexts() -> None:
    registry = HookContextRegistry()
    ctx1 = registry.create("hook-a")
    ctx2 = registry.create("hook-b")
    assert ctx1.tmp_dir.exists()
    assert ctx2.tmp_dir.exists()
    registry.cleanup_all()
    assert not ctx1.tmp_dir.exists()
    assert not ctx2.tmp_dir.exists()


def test_registry_cleanup_all_is_idempotent() -> None:
    registry = HookContextRegistry()
    ctx = registry.create("hook-x")
    registry.cleanup_all()
    # Second call should not raise even though dirs are gone
    registry.cleanup_all()
    assert not ctx.tmp_dir.exists()
