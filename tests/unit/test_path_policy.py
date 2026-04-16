"""Tests for duh.security.path_policy — filesystem boundary enforcement (ADR-072)."""

from __future__ import annotations

import os
import tempfile

import pytest

from duh.security.path_policy import PathPolicy


@pytest.fixture()
def project_dir(tmp_path):
    """Create a minimal project directory."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    return tmp_path


class TestInsideProject:
    def test_file_inside_project(self, project_dir):
        policy = PathPolicy(str(project_dir))
        ok, reason = policy.check(str(project_dir / "src" / "main.py"))
        assert ok is True
        assert reason == ""

    def test_project_root_itself(self, project_dir):
        policy = PathPolicy(str(project_dir))
        ok, _ = policy.check(str(project_dir))
        assert ok is True

    def test_subdir_inside_project(self, project_dir):
        policy = PathPolicy(str(project_dir))
        ok, _ = policy.check(str(project_dir / "src"))
        assert ok is True


class TestOutsideProject:
    def test_path_outside_project_blocked(self, project_dir):
        policy = PathPolicy(str(project_dir))
        ok, reason = policy.check("/etc/passwd")
        assert ok is False
        assert "outside project boundary" in reason

    def test_parent_directory_blocked(self, project_dir):
        # Use allowed_paths=[] to avoid /tmp being in allowed list
        # (on CI, tmp_path is under /tmp so parent would be allowed)
        policy = PathPolicy(str(project_dir), allowed_paths=[])
        ok, reason = policy.check(str(project_dir.parent / "other_project"))
        assert ok is False
        assert "outside" in reason

    def test_traversal_blocked(self, project_dir):
        """Path traversal (../../etc/passwd) is caught after resolve()."""
        policy = PathPolicy(str(project_dir), allowed_paths=[])
        sneaky = str(project_dir / "src" / ".." / ".." / ".." / "etc" / "passwd")
        ok, reason = policy.check(sneaky)
        assert ok is False
        assert "outside" in reason


class TestAllowedPaths:
    def test_tmp_allowed_by_default(self, project_dir):
        policy = PathPolicy(str(project_dir))
        ok, _ = policy.check("/tmp/some_cache_file")
        assert ok is True

    def test_custom_allowed_path(self, project_dir):
        policy = PathPolicy(str(project_dir), allowed_paths=["/var/data"])
        ok, _ = policy.check("/var/data/dataset.csv")
        assert ok is True

    def test_custom_allowed_replaces_default(self, project_dir):
        """When you specify allowed_paths, /tmp default is replaced."""
        policy = PathPolicy(str(project_dir), allowed_paths=["/var/data"])
        ok, _ = policy.check("/tmp/cache")
        assert ok is False

    def test_empty_allowed_paths(self, project_dir):
        """Empty list means only project root is allowed."""
        policy = PathPolicy(str(project_dir), allowed_paths=[])
        ok, _ = policy.check("/tmp/cache")
        assert ok is False
        ok2, _ = policy.check(str(project_dir / "file.txt"))
        assert ok2 is True

    def test_allowed_path_root_itself(self, project_dir):
        policy = PathPolicy(str(project_dir), allowed_paths=["/tmp"])
        ok, _ = policy.check("/tmp")
        assert ok is True


class TestEdgeCases:
    def test_relative_path_resolved(self, project_dir):
        """Relative paths are resolved against cwd, not project root."""
        policy = PathPolicy(str(project_dir))
        # A relative path resolves against os.getcwd(), which is
        # likely NOT inside project_dir, so it should be blocked
        # unless cwd happens to be inside project_dir.
        cwd = os.getcwd()
        if not cwd.startswith(str(project_dir)):
            ok, _ = policy.check("relative/file.txt")
            assert ok is False

    def test_symlink_resolved(self, project_dir):
        """Symlinks pointing outside should be caught."""
        link = project_dir / "sneaky_link"
        try:
            link.symlink_to("/etc")
        except OSError:
            pytest.skip("Cannot create symlinks")
        policy = PathPolicy(str(project_dir))
        ok, _ = policy.check(str(link / "passwd"))
        assert ok is False

    def test_project_root_property(self, project_dir):
        policy = PathPolicy(str(project_dir))
        assert policy.project_root == project_dir.resolve()
