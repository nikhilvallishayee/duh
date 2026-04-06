"""Tests for duh.kernel.deps — injectable dependencies."""

from duh.kernel.deps import Deps


class TestDeps:
    def test_defaults(self):
        d = Deps()
        assert d.call_model is None
        assert d.run_tool is None
        assert d.approve is None
        assert d.compact is None
        assert callable(d.uuid)

    def test_uuid_generates_unique(self):
        d = Deps()
        id1 = d.uuid()
        id2 = d.uuid()
        assert id1 != id2
        assert len(id1) == 36  # UUID4 format

    def test_custom_uuid(self):
        counter = [0]
        def deterministic_uuid():
            counter[0] += 1
            return f"test-{counter[0]}"

        d = Deps(uuid=deterministic_uuid)
        assert d.uuid() == "test-1"
        assert d.uuid() == "test-2"

    def test_custom_call_model(self):
        async def fake_model(**kwargs):
            yield {"type": "assistant", "message": {"content": "hi"}}

        d = Deps(call_model=fake_model)
        assert d.call_model is not None

    def test_custom_approve(self):
        async def always_deny(tool_name, tool_input):
            return {"allowed": False, "reason": "testing"}

        d = Deps(approve=always_deny)
        assert d.approve is not None

    def test_all_fields_injectable(self):
        """Every dependency can be swapped — this is the whole point."""
        async def fake_model(**kw): yield {"type": "done"}
        async def fake_tool(name, input): return "result"
        async def fake_approve(name, input): return {"allowed": True}
        async def fake_compact(msgs): return msgs

        d = Deps(
            call_model=fake_model,
            run_tool=fake_tool,
            approve=fake_approve,
            compact=fake_compact,
            uuid=lambda: "fixed-id",
        )
        assert d.call_model is not None
        assert d.run_tool is not None
        assert d.approve is not None
        assert d.compact is not None
        assert d.uuid() == "fixed-id"
