"""--pre-confirm allowlist loading and token pre-minting for SDK sessions."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from duh.cli.sdk_runner import load_preconfirm_allowlist
from duh.kernel.confirmation import ConfirmationMinter


def test_load_preconfirm_allowlist_returns_tokens() -> None:
    allowlist = [
        {"tool": "Bash", "input": {"command": "ls"}},
        {"tool": "Write", "input": {"file_path": "/tmp/x", "content": "y"}},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(allowlist, f)
        f.flush()
        path = Path(f.name)

    m = ConfirmationMinter(session_key=b"k" * 32)
    tokens = load_preconfirm_allowlist(path, m, "sess-1")
    assert len(tokens) == 2
    # Each token should be valid for its corresponding tool+input
    for entry, token in zip(allowlist, tokens):
        assert m.validate(token, "sess-1", entry["tool"], entry["input"])
    path.unlink()


def test_load_preconfirm_allowlist_empty() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump([], f)
        f.flush()
        path = Path(f.name)

    m = ConfirmationMinter(session_key=b"k" * 32)
    tokens = load_preconfirm_allowlist(path, m, "sess-1")
    assert tokens == []
    path.unlink()
