"""HMAC-SHA256 signature verification on :class:`WebhookListener`.

ADR-031 §B production-hardens the webhook listener against spoofing.
These tests cover:

* The :meth:`WebhookListener.compute_signature` helper produces an
  RFC 2104 / FIPS-198 compliant ``sha256=<hex>`` token.
* A listener constructed with a ``secret`` rejects unsigned and
  mismatched POSTs (``401`` + no log append) and accepts the correct
  signature (``202`` + log append).
* A listener constructed without a secret behaves exactly as before
  (legacy path).
* The per-prefix ``secrets`` map: longest matching prefix wins; paths
  that match no prefix are unverified.
* Constant-time comparison goes through :func:`hmac.compare_digest`
  (sniffed via :mod:`unittest.mock`).
"""
from __future__ import annotations

import hmac
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.duhwave.ingress.triggers import TriggerLog
from duh.duhwave.ingress.webhook import (
    SIGNATURE_HEADER,
    SIGNATURE_SCHEME,
    WebhookListener,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Reserve and release an ephemeral TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# compute_signature — pure function, no I/O
# ---------------------------------------------------------------------------


class TestComputeSignature:
    def test_deterministic_and_rfc_compliant(self):
        body = b'{"hello": "world"}'
        secret = "shhh"

        # Stable across calls.
        sig_a = WebhookListener.compute_signature(secret, body)
        sig_b = WebhookListener.compute_signature(secret, body)
        assert sig_a == sig_b

        # Format is exactly ``sha256=<lowercase-hex>``.
        assert sig_a.startswith(f"{SIGNATURE_SCHEME}=")
        hex_part = sig_a.split("=", 1)[1]
        assert len(hex_part) == 64
        int(hex_part, 16)  # raises if non-hex

        # Matches a vanilla hmac.new() invocation byte-for-byte. This
        # is the FIPS-198 / RFC 2104 reference path.
        import hashlib as _hashlib
        expected = _hashlib.sha256
        ref_hex = hmac.new(secret.encode("utf-8"), body, expected).hexdigest()
        assert sig_a == f"{SIGNATURE_SCHEME}={ref_hex}"

    def test_different_bodies_different_sigs(self):
        secret = "topsecret"
        a = WebhookListener.compute_signature(secret, b"payload one")
        b = WebhookListener.compute_signature(secret, b"payload two")
        assert a != b


# ---------------------------------------------------------------------------
# Single-secret mode (constructor kwarg ``secret=``)
# ---------------------------------------------------------------------------


class TestSingleSecretMode:
    async def test_unsigned_post_rejected_no_log_append(self, tmp_path: Path):
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(log, port=port, secret="abc")
        await listener.start()
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"http://127.0.0.1:{port}/x",
                    json={"k": "v"},
                )
                assert resp.status == 401
                body = await resp.json()
                assert body == {"error": "bad signature"}
        finally:
            await listener.stop()
        # Spoofed event must NOT have been appended.
        assert log.replay() == []

    async def test_bad_signature_rejected(self, tmp_path: Path):
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(log, port=port, secret="abc")
        await listener.start()
        try:
            async with aiohttp.ClientSession() as session:
                # Wrong secret → wrong signature.
                bad_sig = WebhookListener.compute_signature(
                    "WRONG", b'{"k": "v"}'
                )
                resp = await session.post(
                    f"http://127.0.0.1:{port}/x",
                    data=b'{"k": "v"}',
                    headers={
                        "Content-Type": "application/json",
                        SIGNATURE_HEADER: bad_sig,
                    },
                )
                assert resp.status == 401
        finally:
            await listener.stop()
        assert log.replay() == []

    async def test_correct_signature_accepted(self, tmp_path: Path):
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(log, port=port, secret="abc")
        await listener.start()
        try:
            async with aiohttp.ClientSession() as session:
                body = b'{"action": "opened"}'
                sig = WebhookListener.compute_signature("abc", body)
                resp = await session.post(
                    f"http://127.0.0.1:{port}/gh/issue",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        SIGNATURE_HEADER: sig,
                    },
                )
                assert resp.status == 202
                rj = await resp.json()
                assert "correlation_id" in rj
        finally:
            await listener.stop()

        triggers = log.replay()
        assert len(triggers) == 1
        assert triggers[0].source == "/gh/issue"
        assert triggers[0].payload == {"action": "opened"}


# ---------------------------------------------------------------------------
# Legacy / no-secret mode
# ---------------------------------------------------------------------------


class TestNoSecret:
    async def test_unsigned_post_accepted_when_no_secret_configured(
        self, tmp_path: Path
    ):
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(log, port=port)
        await listener.start()
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"http://127.0.0.1:{port}/legacy",
                    json={"hello": "world"},
                )
                assert resp.status == 202
        finally:
            await listener.stop()

        triggers = log.replay()
        assert len(triggers) == 1
        assert triggers[0].source == "/legacy"


# ---------------------------------------------------------------------------
# Per-prefix ``secrets`` map
# ---------------------------------------------------------------------------


class TestPerPrefixSecrets:
    async def test_longest_prefix_wins(self, tmp_path: Path):
        """A request under ``/github/issue`` uses the ``/github`` secret.

        ``/github/v2`` is intentionally a longer prefix that *also*
        matches; verify that it wins over the shorter ``/github``.
        """
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(
            log,
            port=port,
            secrets={
                "/github": "short-secret",
                "/github/v2": "long-secret",
            },
        )
        await listener.start()
        try:
            async with aiohttp.ClientSession() as session:
                body = b'{"x": 1}'
                # Long prefix path requires the long secret.
                long_sig = WebhookListener.compute_signature(
                    "long-secret", body
                )
                resp_long = await session.post(
                    f"http://127.0.0.1:{port}/github/v2/issue",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        SIGNATURE_HEADER: long_sig,
                    },
                )
                assert resp_long.status == 202

                # Using the short secret on a long-prefix path is rejected.
                wrong_sig = WebhookListener.compute_signature(
                    "short-secret", body
                )
                resp_wrong = await session.post(
                    f"http://127.0.0.1:{port}/github/v2/other",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        SIGNATURE_HEADER: wrong_sig,
                    },
                )
                assert resp_wrong.status == 401

                # Short prefix path uses the short secret.
                short_sig = WebhookListener.compute_signature(
                    "short-secret", body
                )
                resp_short = await session.post(
                    f"http://127.0.0.1:{port}/github/issue",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        SIGNATURE_HEADER: short_sig,
                    },
                )
                assert resp_short.status == 202
        finally:
            await listener.stop()

        sources = sorted(t.source for t in log.replay())
        assert sources == ["/github/issue", "/github/v2/issue"]

    async def test_non_matching_prefix_no_verification(self, tmp_path: Path):
        """Path that no prefix matches is unverified (legacy behaviour)."""
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(
            log,
            port=port,
            secrets={"/github": "abc", "/slack": "xyz"},
        )
        await listener.start()
        try:
            async with aiohttp.ClientSession() as session:
                # /random matches no prefix → no verification required.
                resp = await session.post(
                    f"http://127.0.0.1:{port}/random",
                    json={"x": 1},
                )
                assert resp.status == 202
        finally:
            await listener.stop()

        triggers = log.replay()
        assert len(triggers) == 1
        assert triggers[0].source == "/random"


# ---------------------------------------------------------------------------
# compare_digest is wired through
# ---------------------------------------------------------------------------


class TestCompareDigestUsed:
    async def test_compare_digest_called_during_verification(
        self, tmp_path: Path
    ):
        """The verifier MUST go through :func:`hmac.compare_digest`.

        We patch the module-level ``hmac.compare_digest`` reference used
        inside :mod:`duh.duhwave.ingress.webhook` and verify the patched
        version is invoked. Constant-time comparison is the whole point
        of this code path; if a future refactor reaches for ``==`` we
        catch it here.
        """
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(log, port=port, secret="abc")
        await listener.start()
        try:
            with patch(
                "duh.duhwave.ingress.webhook.hmac.compare_digest",
                wraps=hmac.compare_digest,
            ) as spy:
                async with aiohttp.ClientSession() as session:
                    body = b'{"k": "v"}'
                    sig = WebhookListener.compute_signature("abc", body)
                    resp = await session.post(
                        f"http://127.0.0.1:{port}/x",
                        data=body,
                        headers={
                            "Content-Type": "application/json",
                            SIGNATURE_HEADER: sig,
                        },
                    )
                    assert resp.status == 202
                # Exactly one verification per request — and it MUST
                # have routed through compare_digest.
                assert spy.call_count == 1
        finally:
            await listener.stop()
