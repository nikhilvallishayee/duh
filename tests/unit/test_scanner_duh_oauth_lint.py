"""Tests for OAuthLintScanner."""

from __future__ import annotations

import asyncio
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.scanners.duh_oauth_lint import OAuthLintScanner


_BAD_BIND = 'server.bind(("0.0.0.0", 0))\n'
_BAD_REUSE = 'sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n'
_BAD_LOG = 'log.info(f"Authorization: {token}")\n'
_BAD_REDIRECT = 'if redirect.startswith("https://good.example"):\n    accept()\n'
_BAD_PKCE = 'code_challenge_method = "plain"\n'
_GOOD = '''\
server.bind(("127.0.0.1", 0))
if redirect == "https://good.example/callback":
    accept()
code_challenge_method = "S256"
'''


def _run(tmp_path: Path, src: str) -> list:
    (tmp_path / "oauth.py").write_text(src)
    return asyncio.run(OAuthLintScanner().scan(tmp_path, ScannerConfig(), changed_files=None))


def test_flags_0_0_0_0_bind(tmp_path: Path) -> None:
    assert any(f.id == "DUH-OAUTH-BIND" for f in _run(tmp_path, _BAD_BIND))


def test_flags_so_reuseaddr(tmp_path: Path) -> None:
    assert any(f.id == "DUH-OAUTH-REUSEADDR" for f in _run(tmp_path, _BAD_REUSE))


def test_flags_auth_header_log(tmp_path: Path) -> None:
    assert any(f.id == "DUH-OAUTH-LOG-SECRET" for f in _run(tmp_path, _BAD_LOG))


def test_flags_startswith_redirect(tmp_path: Path) -> None:
    assert any(f.id == "DUH-OAUTH-REDIRECT-PREFIX" for f in _run(tmp_path, _BAD_REDIRECT))


def test_flags_plain_pkce(tmp_path: Path) -> None:
    assert any(f.id == "DUH-OAUTH-PKCE" for f in _run(tmp_path, _BAD_PKCE))


def test_clean_oauth_passes(tmp_path: Path) -> None:
    findings = _run(tmp_path, _GOOD)
    assert not any(f.id.startswith("DUH-OAUTH-") for f in findings)
