"""SEC-MEDIUM hardening regression tests (issue #24).

Covers four medium-severity findings:

* SEC-MEDIUM-5 — trust store file is written with mode 0o600.
* SEC-MEDIUM-6 — entry-point plugins are TOFU-verified; untrusted ones are
  skipped (not silently loaded).
* SEC-MEDIUM-4 — Seatbelt profile uses an explicit file-read* allow-list
  instead of the historical global ``(allow file-read*)`` rule.
* SEC-MEDIUM-2 — when the AST classifier raises, the regex fallback path
  logs the exception at WARNING instead of swallowing it silently.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from duh.adapters.sandbox.policy import SandboxPolicy
from duh.adapters.sandbox.seatbelt import generate_profile
from duh.plugins import PluginSpec, discover_entry_point_plugins
from duh.plugins.trust_store import TrustStore
from duh.tools import bash_security


# ---------------------------------------------------------------------------
# SEC-MEDIUM-5: TrustStore.save() chmods to 0o600
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
class TestTrustStorePermissions:
    def test_save_sets_0600(self, tmp_path):
        store_path = tmp_path / "trust.json"
        store = TrustStore(store_path=store_path)
        store.add("plugin-a", "hash-1")  # add() calls save()

        assert store_path.exists()
        mode = stat.S_IMODE(store_path.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_save_preserves_0600_after_re_save(self, tmp_path):
        store_path = tmp_path / "trust.json"
        store = TrustStore(store_path=store_path)
        store.add("plugin-a", "hash-1")
        # Loosen perms to verify save() re-applies 0o600.
        store_path.chmod(0o644)
        store.save()
        mode = stat.S_IMODE(store_path.stat().st_mode)
        assert mode == 0o600


# ---------------------------------------------------------------------------
# SEC-MEDIUM-6: entry-point plugin TOFU enforcement
# ---------------------------------------------------------------------------


def _make_entry_point(name: str, spec: PluginSpec) -> MagicMock:
    ep = MagicMock()
    ep.name = name
    ep.value = f"pkg.{name}:plugin"
    ep.load.return_value = spec
    return ep


class TestEntryPointTOFU:
    def test_first_use_default_skips_untrusted_plugin(self, tmp_path, caplog):
        """With no confirm_tofu and no opt-in flag, first-use is REJECTED."""
        store = TrustStore(store_path=tmp_path / "trust.json")
        spec = PluginSpec(name="ep-evil", version="1.0")
        ep = _make_entry_point("ep-evil", spec)

        caplog.set_level(logging.WARNING, logger="duh.plugins")
        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins(
                trust_store=store, trust_entry_points=False
            )

        assert specs == []
        assert any("untrusted" in rec.message for rec in caplog.records)

    def test_trust_entry_points_flag_admits_first_use(self, tmp_path):
        store = TrustStore(store_path=tmp_path / "trust.json")
        spec = PluginSpec(name="ep-good", version="1.0")
        ep = _make_entry_point("ep-good", spec)

        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins(
                trust_store=store, trust_entry_points=True
            )

        assert len(specs) == 1
        # The plugin is now in the trust store.
        sig_hash = next(iter(store._entries.values()))["sig_hash"]
        assert store.verify("ep-good", sig_hash).status == "trusted"

    def test_env_flag_admits_first_use(self, tmp_path, monkeypatch):
        store = TrustStore(store_path=tmp_path / "trust.json")
        spec = PluginSpec(name="ep-env", version="1.0")
        ep = _make_entry_point("ep-env", spec)

        monkeypatch.setenv("DUH_TRUST_ENTRYPOINT_PLUGINS", "1")
        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins(trust_store=store)

        assert len(specs) == 1

    def test_confirm_tofu_callback_can_accept(self, tmp_path):
        store = TrustStore(store_path=tmp_path / "trust.json")
        spec = PluginSpec(name="ep-confirm", version="1.0")
        ep = _make_entry_point("ep-confirm", spec)

        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins(
                trust_store=store,
                trust_entry_points=False,
                confirm_tofu=lambda _: True,
            )
        assert len(specs) == 1

    def test_confirm_tofu_callback_can_reject(self, tmp_path):
        store = TrustStore(store_path=tmp_path / "trust.json")
        spec = PluginSpec(name="ep-no", version="1.0")
        ep = _make_entry_point("ep-no", spec)

        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins(
                trust_store=store,
                trust_entry_points=True,  # would normally accept
                confirm_tofu=lambda _: False,  # but callback overrides
            )
        assert specs == []

    def test_revoked_entry_point_is_skipped(self, tmp_path, caplog):
        store = TrustStore(store_path=tmp_path / "trust.json")
        spec = PluginSpec(name="ep-revoked", version="1.0")
        ep = _make_entry_point("ep-revoked", spec)

        # First load — accept it so it lands in the trust store.
        with patch("duh.plugins.entry_points", return_value=[ep]):
            discover_entry_point_plugins(
                trust_store=store, trust_entry_points=True
            )
        store.revoke("ep-revoked", reason="key compromised")

        caplog.set_level(logging.WARNING, logger="duh.plugins")
        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins(
                trust_store=store, trust_entry_points=True
            )
        assert specs == []
        assert any("revoked" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# SEC-MEDIUM-4: Seatbelt explicit read paths
# ---------------------------------------------------------------------------


class TestSeatbeltExplicitReadPaths:
    def test_no_global_file_read_rule(self):
        profile = generate_profile(SandboxPolicy())
        # The hardened profile must NOT contain the unscoped global rule.
        assert "(allow file-read*)" not in profile

    def test_explicit_read_subpaths_present(self):
        profile = generate_profile(SandboxPolicy())
        # Some core system paths must always be readable for bash to work.
        assert '(subpath "/usr")' in profile
        assert '(subpath "/bin")' in profile
        assert '(subpath "/System")' in profile
        # And the macOS temp dirs must be readable for subprocesses.
        assert '"/tmp"' in profile or '"/private/tmp"' in profile
        assert "/var/folders" in profile

    def test_python_stdlib_present(self):
        profile = generate_profile(SandboxPolicy())
        # The active Python prefix or stdlib must be in the read allow-list
        # so the sandboxed shell can launch python -c subprocesses.
        assert sys.prefix in profile or sys.base_prefix in profile

    def test_cwd_included_by_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        profile = generate_profile(SandboxPolicy())
        assert str(tmp_path) in profile

    def test_allowed_read_paths_override_replaces_defaults(self):
        custom = ["/opt/myapp", "/tmp"]
        profile = generate_profile(
            SandboxPolicy(), allowed_read_paths=custom
        )
        assert '(subpath "/opt/myapp")' in profile
        # The override should not include the default /System path.
        assert '(subpath "/System")' not in profile

    def test_policy_readable_paths_merged_into_allow_list(self):
        policy = SandboxPolicy(readable_paths=["/srv/data"])
        profile = generate_profile(policy)
        assert '(subpath "/srv/data")' in profile

    def test_dev_null_explicitly_readable(self):
        profile = generate_profile(SandboxPolicy())
        assert '(literal "/dev/null")' in profile

    def test_profile_parens_balanced_after_change(self):
        profile = generate_profile(
            SandboxPolicy(
                writable_paths=["/tmp/work"],
                readable_paths=["/srv/data"],
                network_allowed=False,
            )
        )
        assert profile.count("(") == profile.count(")")


# ---------------------------------------------------------------------------
# SEC-MEDIUM-2: AST classifier failure is logged
# ---------------------------------------------------------------------------


class TestASTFallbackLogged:
    def test_ast_failure_emits_warning(self, monkeypatch, caplog):
        # Force ast_classify to raise.
        def boom(cmd, *, shell="bash"):
            raise RuntimeError("synthetic parser failure")

        # Replace the imported symbol inside the bash_ast module so the
        # ``from duh.tools.bash_ast import ast_classify`` re-import inside
        # classify_command picks up the broken function.
        import duh.tools.bash_ast as bash_ast_mod
        monkeypatch.setattr(bash_ast_mod, "ast_classify", boom)

        caplog.set_level(logging.WARNING, logger="duh.tools.bash_security")
        result = bash_security.classify_command("echo hello")
        # Fallback still produces a classification.
        assert result["risk"] in ("safe", "moderate", "dangerous")
        # And the failure was recorded.
        warnings = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "bash_ast classifier failed" in rec.message
        ]
        assert warnings, "expected a WARNING when AST classifier raises"
        assert "synthetic parser failure" in warnings[0].message

    def test_regex_fallback_still_blocks_dangerous(self, monkeypatch):
        """Even with a broken AST parser, the regex fallback must catch
        a clearly dangerous command."""
        def boom(cmd, *, shell="bash"):
            raise RuntimeError("synthetic parser failure")

        import duh.tools.bash_ast as bash_ast_mod
        monkeypatch.setattr(bash_ast_mod, "ast_classify", boom)

        result = bash_security.classify_command("rm -rf /")
        assert result["risk"] == "dangerous"
