"""Tests for env var allowlist and binary hijack detection."""

from duh.tools.bash_security import classify_command, is_env_var_safe, BINARY_HIJACK_RE


def test_safe_env_var_known():
    """Well-known safe env vars should be allowed."""
    assert is_env_var_safe("PATH") is True
    assert is_env_var_safe("HOME") is True
    assert is_env_var_safe("TERM") is True
    assert is_env_var_safe("LANG") is True
    assert is_env_var_safe("GOPATH") is True
    assert is_env_var_safe("RUST_LOG") is True
    assert is_env_var_safe("NODE_ENV") is True
    assert is_env_var_safe("PYTHONPATH") is True


def test_unsafe_env_var_hijack():
    """Binary hijack vars must be blocked."""
    assert is_env_var_safe("LD_PRELOAD") is False
    assert is_env_var_safe("LD_LIBRARY_PATH") is False
    assert is_env_var_safe("DYLD_INSERT_LIBRARIES") is False
    assert is_env_var_safe("DYLD_LIBRARY_PATH") is False


def test_binary_hijack_regex():
    """Regex should match LD_* and DYLD_* patterns."""
    assert BINARY_HIJACK_RE.match("LD_PRELOAD")
    assert BINARY_HIJACK_RE.match("LD_LIBRARY_PATH")
    assert BINARY_HIJACK_RE.match("DYLD_INSERT_LIBRARIES")
    assert not BINARY_HIJACK_RE.match("PATH")
    assert not BINARY_HIJACK_RE.match("NODE_ENV")


def test_command_with_env_injection_blocked():
    """Commands setting hijack vars should be flagged dangerous."""
    result = classify_command("LD_PRELOAD=/evil.so ./app")
    assert result["risk"] == "dangerous"
    assert "hijack" in result["reason"].lower() or "LD_PRELOAD" in result["reason"]


def test_command_with_safe_env_allowed():
    """Commands setting safe env vars should pass."""
    result = classify_command("NODE_ENV=production npm start")
    assert result["risk"] != "dangerous"


def test_export_hijack_blocked():
    """export of hijack vars should be flagged."""
    result = classify_command("export LD_PRELOAD=/evil.so")
    assert result["risk"] == "dangerous"


def test_export_safe_allowed():
    """export of safe vars should pass."""
    result = classify_command("export PATH=$PATH:/usr/local/bin")
    assert result["risk"] != "dangerous"


def test_dyld_env_blocked():
    """macOS DYLD injection should be blocked."""
    result = classify_command("DYLD_INSERT_LIBRARIES=/evil.dylib ./app")
    assert result["risk"] == "dangerous"
