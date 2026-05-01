"""Webhook ingress listener — ADR-031 §B.2.

Bound to ``127.0.0.1`` by default. Each POST is normalised into a single
:class:`~duh.duhwave.ingress.triggers.Trigger` whose ``source`` is the
URL path the request hit (so a topology can subscribe by path glob).

Body handling:
  * If ``Content-Type`` looks like JSON, the body is parsed and stored
    under ``payload``.
  * Otherwise the body is decoded as utf-8 and stored under
    ``payload["body"]``. Bodies > 64 KB are written to a tempfile and
    referenced via :attr:`Trigger.raw_ref`.

HMAC-SHA256 signature verification is supported in two modes:

  * ``secret``: a single shared secret applied to *every* incoming POST.
    Useful for single-tenant hosts.
  * ``secrets``: a ``{url-path-prefix: secret}`` map used when one
    listener serves several swarms. The longest matching prefix wins;
    if no prefix matches the request path, no verification happens
    (legacy behaviour preserved).

Either path verifies ``X-Duh-Signature: sha256=<hex>`` against
``hmac_sha256(secret, raw_body)`` with :func:`hmac.compare_digest` for
constant-time comparison. A failure returns ``401 Unauthorized`` with
``{"error": "bad signature"}`` and *does not* append the trigger to the
log — a spoofed event must not become a Task.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from duh.duhwave.ingress.triggers import (
    MAX_PAYLOAD_BYTES,
    Trigger,
    TriggerKind,
    TriggerLog,
)

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from aiohttp import web as _aiohttp_web

logger = logging.getLogger(__name__)


SIGNATURE_HEADER = "X-Duh-Signature"
SIGNATURE_SCHEME = "sha256"


@dataclass(slots=True)
class _ServerState:
    """Internal aiohttp runner/site pair tracked for clean shutdown."""

    runner: Any  # aiohttp.web.AppRunner
    site: Any  # aiohttp.web.TCPSite


class WebhookListener:
    """HTTP listener that materialises POST requests as Triggers.

    Parameters
    ----------
    log:
        Append-only trigger log (ADR-031 §B.5).
    port:
        TCP port on the local interface. Default ``8728``.
    host:
        Bind address. Default ``127.0.0.1`` — public exposure is opt-in.
    secret:
        Optional single shared HMAC secret. When set, every incoming
        POST must carry a valid ``X-Duh-Signature`` header. ``None``
        means no verification (legacy behaviour).
    secrets:
        Optional ``{url-path-prefix: secret}`` map for per-swarm
        secrets on a shared listener. The longest matching prefix
        wins. Paths that match no prefix and have no global ``secret``
        are unverified.
    """

    def __init__(
        self,
        log: TriggerLog,
        port: int = 8728,
        host: str = "127.0.0.1",
        *,
        secret: str | None = None,
        secrets: dict[str, str] | None = None,
    ) -> None:
        self._log = log
        self._port = port
        self._host = host
        self._secret = secret
        # Sort by descending prefix length once; longest-prefix wins on
        # lookup. Empty-string prefix (matches everything) is allowed.
        self._secrets: list[tuple[str, str]] = sorted(
            (secrets or {}).items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        self._state: _ServerState | None = None

    # ── public helpers ─────────────────────────────────────────────────

    @classmethod
    def compute_signature(cls, secret: str, body: bytes) -> str:
        """Return the canonical signature header value for ``body``.

        Format is ``sha256=<lowercase-hex>``. Same shape clients use to
        sign; the verifier expects an exact match (mod ``compare_digest``).
        """
        digest = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return f"{SIGNATURE_SCHEME}={digest}"

    # ── lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Bind the HTTP server and begin accepting webhooks.

        Idempotent: a second call while running is a no-op.
        """
        if self._state is not None:
            return

        # Lazy-import so importing this module does not require aiohttp.
        from aiohttp import web

        app = web.Application()
        app.router.add_route("POST", "/{tail:.*}", self._handle)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=self._host, port=self._port)
        await site.start()
        self._state = _ServerState(runner=runner, site=site)
        logger.info(
            "WebhookListener bound to http://%s:%d", self._host, self._port
        )

    async def stop(self) -> None:
        """Stop the HTTP server. Idempotent."""
        state = self._state
        if state is None:
            return
        self._state = None
        try:
            await state.site.stop()
        finally:
            await state.runner.cleanup()
        logger.info("WebhookListener stopped")

    # ── secret resolution ──────────────────────────────────────────────

    def _resolve_required_secret(self, path: str) -> str | None:
        """Return the secret required for ``path``, or ``None`` if unverified.

        Resolution order, first match wins:

        1. ``secrets`` map: longest prefix that ``path`` startswith.
        2. ``secret`` (the global single-secret mode).
        3. ``None`` — no verification.
        """
        for prefix, value in self._secrets:
            if path.startswith(prefix):
                return value
        return self._secret

    def _verify_signature(
        self, required_secret: str, body: bytes, header: str | None
    ) -> bool:
        """Constant-time comparison against the expected signature.

        ``compare_digest`` requires equal-length inputs; we pad mismatched
        lengths by comparing on the canonical expected value, which keeps
        the timing-attack property intact.
        """
        if header is None:
            return False
        expected = self.compute_signature(required_secret, body)
        return hmac.compare_digest(expected, header)

    # ── request handler ────────────────────────────────────────────────

    async def _handle(self, request: "_aiohttp_web.Request") -> Any:
        """One request → one :class:`Trigger` (after signature gate)."""
        from aiohttp import web

        body_bytes = await request.read()

        # ── signature gate ──
        required_secret = self._resolve_required_secret(request.path)
        if required_secret is not None:
            header = request.headers.get(SIGNATURE_HEADER)
            if not self._verify_signature(required_secret, body_bytes, header):
                # Do NOT append to the trigger log — a spoofed event
                # must never become a Task.
                logger.warning(
                    "rejected webhook: bad signature path=%s have_header=%s",
                    request.path,
                    header is not None,
                )
                return web.json_response(
                    {"error": "bad signature"}, status=401
                )

        content_type = (request.headers.get("Content-Type") or "").lower()

        payload: dict[str, object]
        raw_ref: str | None = None

        if "json" in content_type and body_bytes:
            try:
                parsed = json.loads(body_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"body": body_bytes.decode("utf-8", errors="replace")}
            else:
                payload = parsed if isinstance(parsed, dict) else {"body": parsed}
        else:
            try:
                text = body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = body_bytes.decode("utf-8", errors="replace")
            if len(body_bytes) > MAX_PAYLOAD_BYTES:
                # Spill to a tempfile and reference by path.
                tmp = tempfile.NamedTemporaryFile(
                    prefix="duhwave-webhook-",
                    suffix=".bin",
                    delete=False,
                )
                try:
                    tmp.write(body_bytes)
                finally:
                    tmp.close()
                raw_ref = str(Path(tmp.name).absolute())
                payload = {"body_size": len(body_bytes)}
            else:
                payload = {"body": text}

        trigger = Trigger(
            kind=TriggerKind.WEBHOOK,
            source=request.path,
            payload=payload,
            raw_ref=raw_ref,
        )
        try:
            self._log.append(trigger)
        except Exception:  # pragma: no cover - log corruption is fatal
            logger.exception("failed to append webhook trigger")
            return web.Response(status=500, text="trigger log unavailable")

        return web.json_response(
            {"correlation_id": trigger.correlation_id},
            status=202,
        )
