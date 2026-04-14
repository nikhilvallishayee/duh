"""Tests for per-hook filesystem namespacing (ADR-054, 7.4)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from duh.hooks import HookContext


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
