"""Tests for ``duh.duhwave.bundle.permissions.BundlePermissions``.

The permission envelope is the install-time guard. It needs to accept
both the terse list shape and the structured table-with-allow shape
(swarm authors can pick either) and render a clean diff at install
time so a user upgrading a bundle sees what changed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duh.duhwave.bundle.permissions import BundlePermissions, PermissionsError


def _write(path: Path, body: str) -> Path:
    path.write_text(body.strip() + "\n")
    return path


# ---------------------------------------------------------------------------
# from_toml accepts both shapes
# ---------------------------------------------------------------------------


class TestFromTomlShapes:
    def test_bare_list_shapes(self, tmp_path: Path):
        # Bare lists for `network` and `tools` must appear *before* any
        # named table — TOML otherwise scopes them under the last-opened
        # table (here, [filesystem]), which is not what the parser reads.
        spec_path = _write(
            tmp_path / "permissions.toml",
            """
network = ["api.github.com", "*.slack.com"]
tools = ["Bash", "Read", "Write"]

[filesystem]
read = ["/repos/**"]
write = ["/repos/work/**"]
""",
        )
        p = BundlePermissions.from_toml(spec_path)
        assert p.filesystem == {
            "read": ["/repos/**"],
            "write": ["/repos/work/**"],
        }
        assert p.network == ["api.github.com", "*.slack.com"]
        assert p.tools == ["Bash", "Read", "Write"]

    def test_table_with_allow_shapes(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "permissions.toml",
            """
[filesystem]
read = ["/repos/**"]

[network]
allow = ["api.github.com"]

[tools]
require = ["Read"]
""",
        )
        p = BundlePermissions.from_toml(spec_path)
        assert p.filesystem == {"read": ["/repos/**"]}
        assert p.network == ["api.github.com"]
        assert p.tools == ["Read"]

    def test_invalid_filesystem_value_raises(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "permissions.toml",
            """
[filesystem]
read = "not a list"
""",
        )
        with pytest.raises(PermissionsError, match="filesystem.read"):
            BundlePermissions.from_toml(spec_path)


# ---------------------------------------------------------------------------
# diff()
# ---------------------------------------------------------------------------


class TestDiff:
    def test_diff_against_self_is_empty(self):
        p = BundlePermissions(
            filesystem={"read": ["/a/**"]},
            network=["github.com"],
            tools=["Read"],
        )
        assert p.diff(p) == ""

    def test_diff_renders_added_and_removed(self):
        prior = BundlePermissions(
            filesystem={"read": ["/a/**"]},
            network=["github.com"],
            tools=["Read"],
        )
        new = BundlePermissions(
            filesystem={"read": ["/a/**", "/b/**"], "write": ["/c/**"]},
            network=["github.com", "slack.com"],
            tools=["Read", "Bash"],
        )
        diff = new.diff(prior)
        assert "+ filesystem.read: /b/**" in diff
        assert "+ filesystem.write: /c/**" in diff
        assert "+ network: slack.com" in diff
        assert "+ tool: Bash" in diff
        # Nothing was removed in this scenario.
        assert all(not line.startswith("-") for line in diff.splitlines())

    def test_diff_shows_removals(self):
        prior = BundlePermissions(
            network=["github.com", "slack.com"],
            tools=["Read", "Bash"],
        )
        new = BundlePermissions(network=["github.com"], tools=["Read"])
        diff = new.diff(prior)
        assert "- network: slack.com" in diff
        assert "- tool: Bash" in diff
        # No additions expected.
        assert all(not line.startswith("+") for line in diff.splitlines())


# ---------------------------------------------------------------------------
# is_empty()
# ---------------------------------------------------------------------------


class TestIsEmpty:
    def test_default_is_empty(self):
        assert BundlePermissions().is_empty()

    def test_any_field_set_is_not_empty(self):
        assert not BundlePermissions(network=["x"]).is_empty()
        assert not BundlePermissions(tools=["Read"]).is_empty()
        assert not BundlePermissions(filesystem={"read": ["/x"]}).is_empty()
