"""Tests for duh.duhwave.coordinator.view — RLMHandleView.

The view is a worker's keyhole onto the coordinator's REPL: only the
explicitly exposed handle names are reachable. Each test verifies one
boundary condition.
"""

from __future__ import annotations

import pytest

from duh.duhwave.coordinator.view import RLMHandleView
from duh.duhwave.rlm import RLMRepl


@pytest.fixture
async def repl():
    r = RLMRepl()
    await r.start()
    try:
        # Pre-bind three handles so the view has something to expose.
        await r.bind("a", "alpha content")
        await r.bind("b", "beta content")
        await r.bind("c", "gamma content")
        yield r
    finally:
        await r.shutdown()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    async def test_from_names_seeds_visibility(self, repl):
        view = RLMHandleView.from_names(repl, ["a", "b"])
        assert view.list_exposed() == ["a", "b"]

    async def test_default_visibility_is_empty(self, repl):
        view = RLMHandleView(repl=repl)
        assert view.list_exposed() == []


# ---------------------------------------------------------------------------
# Read paths — exposed handles
# ---------------------------------------------------------------------------


class TestExposedRead:
    async def test_peek_exposed_succeeds(self, repl):
        view = RLMHandleView.from_names(repl, ["a", "b"])
        out = await view.peek("a", start=0, end=5)
        assert out == "alpha"

    async def test_search_exposed_succeeds(self, repl):
        view = RLMHandleView.from_names(repl, ["a", "b"])
        hits = await view.search("b", r"beta")
        assert len(hits) == 1


# ---------------------------------------------------------------------------
# Read paths — non-exposed handles raise BEFORE hitting the REPL
# ---------------------------------------------------------------------------


class TestNonExposedDenied:
    async def test_peek_non_exposed_raises(self, repl):
        view = RLMHandleView.from_names(repl, ["a", "b"])
        with pytest.raises(ValueError, match="handle not exposed: c"):
            await view.peek("c")

    async def test_search_non_exposed_raises(self, repl):
        view = RLMHandleView.from_names(repl, ["a"])
        with pytest.raises(ValueError, match="handle not exposed: c"):
            await view.search("c", "anything")

    async def test_slice_non_exposed_source_raises(self, repl):
        view = RLMHandleView.from_names(repl, ["a"])
        with pytest.raises(ValueError, match="handle not exposed: c"):
            await view.slice("c", 0, 1, "out")


# ---------------------------------------------------------------------------
# Slice creates a new visible handle
# ---------------------------------------------------------------------------


class TestSliceVisibility:
    async def test_slice_adds_bind_as_to_view(self, repl):
        view = RLMHandleView.from_names(repl, ["a"])
        await view.slice("a", 0, 5, "a_head")
        assert "a_head" in view.list_exposed()
        # And subsequent peeks succeed.
        out = await view.peek("a_head")
        assert out == "alpha"

    async def test_view_does_not_expose_other_workers_slices(self, repl):
        # Two distinct views, each from independent expose lists.
        view1 = RLMHandleView.from_names(repl, ["a"])
        view2 = RLMHandleView.from_names(repl, ["b"])
        await view1.slice("a", 0, 5, "shared_name")
        # view2 does not see view1's local slice — even though the
        # underlying handle exists in the REPL.
        assert "shared_name" not in view2.list_exposed()
        with pytest.raises(ValueError, match="handle not exposed"):
            await view2.peek("shared_name")


# ---------------------------------------------------------------------------
# View routes through the REPL — does not directly mutate the handle store
# ---------------------------------------------------------------------------


class TestRoutingThroughRepl:
    async def test_view_does_not_have_bind_method(self, repl):
        view = RLMHandleView.from_names(repl, ["a"])
        # Read-only by construction: the view exposes peek/search/slice and
        # does NOT expose bind / exec_code.
        assert not hasattr(view, "bind")
        assert not hasattr(view, "exec_code")

    async def test_view_uses_repl_handle_store_indirectly(self, repl):
        # The view's slice goes through repl.slice, which updates repl.handles.
        view = RLMHandleView.from_names(repl, ["a"])
        before = {h.name for h in repl.handles.list()}
        await view.slice("a", 0, 3, "tip")
        after = {h.name for h in repl.handles.list()}
        # The new handle landed in the REPL's handle store.
        assert "tip" in after - before
