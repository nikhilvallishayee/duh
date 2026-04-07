"""Tests for the memory system -- port, adapter, and kernel integration.

Covers:
- MemoryStore protocol conformance
- FileMemoryStore CRUD (read/write/list/delete)
- MEMORY.md truncation at 200 lines
- build_memory_prompt output
- Sanitized path generation
- Frontmatter parsing
- Edge cases
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from duh.adapters.memory_store import (
    FileMemoryStore,
    INDEX_FILENAME,
    INDEX_LINE_CAP,
    _parse_frontmatter,
    _sanitize_cwd,
    _truncate_index,
)
from duh.kernel.memory import (
    MEMORY_TYPES,
    build_memory_prompt,
    make_frontmatter,
)
from duh.ports.memory import MemoryHeader, MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path, cwd: str = "/Users/alice/Code/proj") -> FileMemoryStore:
    """Create a FileMemoryStore with memory_dir pointing into tmp_path."""
    store = FileMemoryStore(cwd=cwd)
    # Override the memory dir to use tmp_path
    store._memory_dir = tmp_path / "memory"
    return store


def _make_topic(name: str = "Test Topic", desc: str = "A test", type_: str = "project") -> str:
    """Generate a topic file with frontmatter."""
    return f"---\nname: {name}\ndescription: {desc}\ntype: {type_}\n---\n\nSome content here.\n"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_file_memory_store_satisfies_protocol(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert isinstance(store, MemoryStore)

    def test_protocol_is_runtime_checkable(self):
        """MemoryStore must be decorated with @runtime_checkable."""
        assert hasattr(MemoryStore, "__protocol_attrs__") or hasattr(
            MemoryStore, "__abstractmethods__"
        ) or issubclass(type(MemoryStore), type)
        # The real test: isinstance check works
        store = FileMemoryStore(cwd="/tmp/test")
        assert isinstance(store, MemoryStore)

    def test_fake_store_satisfies_protocol(self):
        """A minimal fake must also satisfy the protocol."""

        class FakeMemory:
            def get_memory_dir(self) -> Path:
                return Path("/tmp")
            def read_index(self) -> str:
                return ""
            def write_index(self, content: str) -> None:
                pass
            def read_file(self, name: str) -> str:
                return ""
            def write_file(self, name: str, content: str) -> None:
                pass
            def list_files(self) -> list[MemoryHeader]:
                return []
            def delete_file(self, name: str) -> None:
                pass

        assert isinstance(FakeMemory(), MemoryStore)


# ---------------------------------------------------------------------------
# Sanitize cwd
# ---------------------------------------------------------------------------

class TestSanitizeCwd:
    def test_unix_path(self):
        assert _sanitize_cwd("/Users/alice/Code/proj") == "Users-alice-Code-proj"

    def test_home_path(self):
        assert _sanitize_cwd("/home/bob/work") == "home-bob-work"

    def test_root_path(self):
        assert _sanitize_cwd("/") == ""

    def test_no_leading_dash(self):
        result = _sanitize_cwd("/foo/bar")
        assert not result.startswith("-")

    def test_windows_style_path(self):
        result = _sanitize_cwd("C:\\Users\\alice\\Code")
        assert "\\" not in result
        assert "/" not in result

    def test_deeply_nested(self):
        result = _sanitize_cwd("/a/b/c/d/e/f/g")
        assert result == "a-b-c-d-e-f-g"


# ---------------------------------------------------------------------------
# FileMemoryStore -- get_memory_dir
# ---------------------------------------------------------------------------

class TestGetMemoryDir:
    def test_returns_path_object(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert isinstance(store.get_memory_dir(), Path)

    def test_memory_dir_contains_sanitized_cwd(self):
        store = FileMemoryStore(cwd="/Users/alice/proj")
        mem_dir = store.get_memory_dir()
        assert "Users-alice-proj" in str(mem_dir)
        assert str(mem_dir).endswith("memory")


# ---------------------------------------------------------------------------
# FileMemoryStore -- read/write index
# ---------------------------------------------------------------------------

class TestIndex:
    def test_read_index_missing_returns_empty(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert store.read_index() == ""

    def test_write_and_read_index(self, tmp_path: Path):
        store = _make_store(tmp_path)
        content = "# Memory\n\n- [Foo](foo.md) -- bar\n"
        store.write_index(content)
        assert store.read_index() == content

    def test_write_index_creates_dir(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert not store.get_memory_dir().exists()
        store.write_index("# Memory\n")
        assert store.get_memory_dir().exists()

    def test_write_index_overwrites(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_index("first")
        store.write_index("second")
        assert store.read_index() == "second"


# ---------------------------------------------------------------------------
# FileMemoryStore -- read/write/delete files
# ---------------------------------------------------------------------------

class TestFiles:
    def test_read_missing_file_returns_empty(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert store.read_file("nonexistent.md") == ""

    def test_write_and_read_file(self, tmp_path: Path):
        store = _make_store(tmp_path)
        content = _make_topic()
        store.write_file("project_setup.md", content)
        assert store.read_file("project_setup.md") == content

    def test_write_file_creates_dir(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert not store.get_memory_dir().exists()
        store.write_file("test.md", "hello")
        assert store.get_memory_dir().exists()

    def test_write_file_overwrites(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_file("test.md", "first")
        store.write_file("test.md", "second")
        assert store.read_file("test.md") == "second"

    def test_delete_file(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_file("test.md", "content")
        store.delete_file("test.md")
        assert store.read_file("test.md") == ""

    def test_delete_nonexistent_is_noop(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.delete_file("ghost.md")  # should not raise

    def test_unicode_content(self, tmp_path: Path):
        store = _make_store(tmp_path)
        content = "日本語テスト ☃ café 🎉"
        store.write_file("unicode.md", content)
        assert store.read_file("unicode.md") == content


# ---------------------------------------------------------------------------
# FileMemoryStore -- list_files
# ---------------------------------------------------------------------------

class TestListFiles:
    def test_list_empty_dir(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert store.list_files() == []

    def test_list_nonexistent_dir(self, tmp_path: Path):
        store = _make_store(tmp_path)
        # memory dir doesn't exist yet
        assert store.list_files() == []

    def test_list_excludes_memory_md(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_index("# Index\n")
        store.write_file("project_setup.md", _make_topic())
        headers = store.list_files()
        filenames = [h.filename for h in headers]
        assert INDEX_FILENAME not in filenames
        assert "project_setup.md" in filenames

    def test_list_parses_frontmatter(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_file("feedback_style.md", _make_topic(
            name="Code Style", desc="Formatting prefs", type_="feedback",
        ))
        headers = store.list_files()
        assert len(headers) == 1
        h = headers[0]
        assert h.filename == "feedback_style.md"
        assert h.name == "Code Style"
        assert h.description == "Formatting prefs"
        assert h.type == "feedback"

    def test_list_multiple_sorted(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_file("b_topic.md", _make_topic(name="B"))
        store.write_file("a_topic.md", _make_topic(name="A"))
        headers = store.list_files()
        assert len(headers) == 2
        assert headers[0].filename == "a_topic.md"
        assert headers[1].filename == "b_topic.md"

    def test_list_ignores_non_md_files(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store._ensure_dir()
        (store.get_memory_dir() / "notes.txt").write_text("ignore me")
        (store.get_memory_dir() / "data.json").write_text("{}")
        store.write_file("real.md", _make_topic())
        headers = store.list_files()
        assert len(headers) == 1
        assert headers[0].filename == "real.md"


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

class TestFrontmatterParsing:
    def test_full_frontmatter(self):
        text = "---\nname: My Topic\ndescription: About things\ntype: user\n---\n\nBody"
        h = _parse_frontmatter(text, "my_topic.md")
        assert h.name == "My Topic"
        assert h.description == "About things"
        assert h.type == "user"
        assert h.filename == "my_topic.md"

    def test_missing_frontmatter(self):
        text = "Just some content without frontmatter."
        h = _parse_frontmatter(text, "bare.md")
        assert h.filename == "bare.md"
        assert h.name == "bare.md"  # falls back to filename
        assert h.description == ""
        assert h.type == ""

    def test_partial_frontmatter(self):
        text = "---\nname: Only Name\n---\n\nBody"
        h = _parse_frontmatter(text, "partial.md")
        assert h.name == "Only Name"
        assert h.description == ""
        assert h.type == ""

    def test_empty_file(self):
        h = _parse_frontmatter("", "empty.md")
        assert h.filename == "empty.md"
        assert h.name == "empty.md"


# ---------------------------------------------------------------------------
# Index truncation
# ---------------------------------------------------------------------------

class TestIndexTruncation:
    def test_under_cap_unchanged(self):
        content = "# Header\n" + "- line\n" * 10
        assert _truncate_index(content, cap=200) == content

    def test_at_cap_unchanged(self):
        lines = ["# Header"] + [f"- entry {i}" for i in range(199)]
        content = "\n".join(lines)
        assert len(content.splitlines()) == 200
        assert _truncate_index(content, cap=200) == content

    def test_over_cap_truncates(self):
        lines = ["# Header"] + [f"- entry {i}" for i in range(250)]
        content = "\n".join(lines)
        result = _truncate_index(content, cap=200)
        result_lines = result.splitlines()
        assert len(result_lines) == 200
        # Header is preserved
        assert result_lines[0] == "# Header"
        # Last line is preserved
        assert result_lines[-1] == "- entry 249"
        # First entries are dropped
        assert "- entry 0" not in result_lines

    def test_preserves_header_line(self):
        lines = ["# My Memory"] + [f"- item {i}" for i in range(300)]
        content = "\n".join(lines)
        result = _truncate_index(content, cap=50)
        assert result.splitlines()[0] == "# My Memory"

    def test_write_index_applies_truncation(self, tmp_path: Path):
        store = _make_store(tmp_path)
        lines = ["# Header"] + [f"- entry {i}" for i in range(250)]
        content = "\n".join(lines)
        store.write_index(content)
        stored = store.read_index()
        assert len(stored.splitlines()) == INDEX_LINE_CAP


# ---------------------------------------------------------------------------
# build_memory_prompt
# ---------------------------------------------------------------------------

class TestBuildMemoryPrompt:
    def test_empty_index_returns_empty(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert build_memory_prompt(store) == ""

    def test_whitespace_only_index_returns_empty(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_index("   \n  \n")
        assert build_memory_prompt(store) == ""

    def test_wraps_in_memory_tags(self, tmp_path: Path):
        store = _make_store(tmp_path)
        index = "# Memory\n\n- [Setup](setup.md) -- Initial config\n"
        store.write_index(index)
        result = build_memory_prompt(store)
        assert result.startswith("<memory>\n")
        assert result.endswith("\n</memory>")
        assert "# Memory" in result
        assert "[Setup](setup.md)" in result

    def test_strips_trailing_whitespace(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_index("# Memory\n\n- entry\n\n\n")
        result = build_memory_prompt(store)
        # Should not have trailing newlines before </memory>
        assert result.endswith("- entry\n</memory>")


# ---------------------------------------------------------------------------
# make_frontmatter
# ---------------------------------------------------------------------------

class TestMakeFrontmatter:
    def test_generates_valid_frontmatter(self):
        result = make_frontmatter(
            name="Code Style",
            description="Formatting preferences",
            type="feedback",
        )
        assert result.startswith("---\n")
        assert "name: Code Style\n" in result
        assert "description: Formatting preferences\n" in result
        assert "type: feedback\n" in result
        assert result.endswith("---\n")

    def test_roundtrips_through_parser(self):
        fm = make_frontmatter(name="Test", description="Desc", type="user")
        h = _parse_frontmatter(fm + "\nBody content", "test.md")
        assert h.name == "Test"
        assert h.description == "Desc"
        assert h.type == "user"


# ---------------------------------------------------------------------------
# MEMORY_TYPES
# ---------------------------------------------------------------------------

class TestMemoryTypes:
    def test_has_four_types(self):
        assert len(MEMORY_TYPES) == 4

    def test_expected_keys(self):
        assert set(MEMORY_TYPES.keys()) == {"user", "feedback", "project", "reference"}

    def test_all_values_are_strings(self):
        for v in MEMORY_TYPES.values():
            assert isinstance(v, str)
            assert len(v) > 0


# ---------------------------------------------------------------------------
# MemoryHeader dataclass
# ---------------------------------------------------------------------------

class TestMemoryHeader:
    def test_frozen(self):
        h = MemoryHeader(filename="a.md", name="A", description="d", type="user")
        with pytest.raises(AttributeError):
            h.name = "B"  # type: ignore[misc]

    def test_equality(self):
        h1 = MemoryHeader(filename="a.md", name="A", description="d", type="user")
        h2 = MemoryHeader(filename="a.md", name="A", description="d", type="user")
        assert h1 == h2

    def test_fields(self):
        h = MemoryHeader(filename="f.md", name="N", description="D", type="T")
        assert h.filename == "f.md"
        assert h.name == "N"
        assert h.description == "D"
        assert h.type == "T"


# ---------------------------------------------------------------------------
# Integration: full CRUD flow
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_crud_flow(self, tmp_path: Path):
        store = _make_store(tmp_path)

        # Initially empty
        assert store.read_index() == ""
        assert store.list_files() == []

        # Write a topic file
        content = _make_topic(name="Setup", desc="Initial config", type_="project")
        store.write_file("project_setup.md", content)

        # Write the index
        store.write_index("# Memory\n\n- [Setup](project_setup.md) -- Initial config\n")

        # Read back
        assert "Setup" in store.read_index()
        headers = store.list_files()
        assert len(headers) == 1
        assert headers[0].name == "Setup"
        assert headers[0].type == "project"

        # Build prompt
        prompt = build_memory_prompt(store)
        assert "<memory>" in prompt
        assert "Setup" in prompt

        # Update
        store.write_file("project_setup.md", _make_topic(name="Setup v2"))
        headers = store.list_files()
        assert headers[0].name == "Setup v2"

        # Delete
        store.delete_file("project_setup.md")
        assert store.list_files() == []
        assert store.read_file("project_setup.md") == ""

    def test_multiple_types(self, tmp_path: Path):
        store = _make_store(tmp_path)

        for type_ in MEMORY_TYPES:
            store.write_file(
                f"{type_}_test.md",
                _make_topic(name=f"{type_} Topic", type_=type_),
            )

        headers = store.list_files()
        assert len(headers) == 4
        types = {h.type for h in headers}
        assert types == set(MEMORY_TYPES.keys())
