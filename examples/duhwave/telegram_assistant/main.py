#!/usr/bin/env python3
"""telegram_assistant — duhwave + real OpenAI + mock Telegram bus.

A runnable demo that wires three message flows through one persistent
duhwave process:

    1. INBOUND  — a (mock) Telegram webhook posts an "update" → ingress
                  trigger → real OpenAI agent → reply written to a mock
                  outbox file.
    2. SCHEDULED — a timer fires every N seconds; the agent drafts a
                   short "tip of the day"; tip lands in the outbox.
    3. ON-DEMAND — a manual seam fires; the agent produces a one-off
                   message; it lands in the outbox.

Everything except the Telegram boundary is real:
  - duhwave RLMRepl / TriggerLog / WebhookListener / Trigger
  - OpenAI gpt-4o-mini via D.U.H.'s native adapter (requires
    OPENAI_API_KEY)
  - asyncio scheduler, real wall-clock

The Telegram boundary is mocked because we don't want to depend on a
live bot token: inbound updates are synthesised locally, outbound
replies are appended to ``<tmpdir>/telegram_outbox.jsonl`` instead of
being POSTed to ``api.telegram.org``.

Usage::

    export OPENAI_API_KEY=sk-proj-...
    /Users/nomind/Code/duh/.venv/bin/python3 \\
        examples/duhwave/telegram_assistant/main.py [--cycles 2]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

# Path bootstrap so this runs as a script.
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.ingress.triggers import Trigger, TriggerKind, TriggerLog  # noqa: E402
from duh.duhwave.ingress.webhook import WebhookListener  # noqa: E402
from duh.duhwave.rlm.repl import RLMRepl  # noqa: E402


# ---------------------------------------------------------------------------
# Mock Telegram boundary
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MockOutbox:
    """File-based stand-in for ``POST api.telegram.org/bot<token>/sendMessage``.

    Each ``send`` call appends one JSON line to ``path``. Real bots would
    HTTP POST a chat_id + text payload; the shape we record here is the
    same so a real driver could swap out by changing the transport.
    """

    path: Path

    def send(self, *, chat_id: int, text: str, kind: str) -> None:
        record = {
            "ts": time.time(),
            "kind": kind,
            "chat_id": chat_id,
            "text": text,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def make_telegram_update(*, chat_id: int, user_text: str) -> dict[str, object]:
    """Synthesize the minimal subset of a Telegram ``Update`` payload.

    Real updates carry many more fields; this is what our agent reads.
    """
    return {
        "update_id": int(time.time() * 1000) % 10_000_000,
        "message": {
            "message_id": int(time.time() * 100) % 1_000_000,
            "from": {"id": chat_id, "is_bot": False, "first_name": "User"},
            "chat": {"id": chat_id, "type": "private"},
            "date": int(time.time()),
            "text": user_text,
        },
    }


# ---------------------------------------------------------------------------
# Real OpenAI agent
# ---------------------------------------------------------------------------


_BOT_PERSONA = (
    "You are a helpful Telegram bot for software developers. Your replies "
    "are concise (≤2 short paragraphs), markdown-friendly, and never "
    "include preambles like 'Sure!' or 'Of course!'. If you don't know, "
    "say so in one sentence."
)


async def call_openai(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int = 400,
) -> tuple[str, dict[str, int]]:
    """Single-turn streaming call to OpenAI; return (text, usage_dict)."""
    from duh.adapters.openai import OpenAIProvider

    provider = OpenAIProvider(model=model)
    chunks: list[str] = []
    usage = {"in": 0, "out": 0, "cached": 0}
    async for ev in provider.stream(
        messages=[{"role": "user", "content": user}],
        system_prompt=system,
        model=model,
        max_tokens=max_tokens,
    ):
        et = ev.get("type")
        if et == "text_delta":
            chunks.append(ev.get("text", ""))
        elif et in ("usage", "usage_delta"):
            usage["in"] = ev.get("input_tokens", usage["in"])
            usage["out"] = ev.get("output_tokens", usage["out"])
            usage["cached"] = ev.get("cached_tokens", usage["cached"])
        elif et == "error":
            raise RuntimeError(f"openai error: {ev.get('error')}")
    text = "".join(chunks).strip()
    return text, usage


# ---------------------------------------------------------------------------
# Three flows
# ---------------------------------------------------------------------------


@dataclass
class Stats:
    inbound: int = 0
    scheduled: int = 0
    on_demand: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    wall_s: float = 0.0


async def handle_inbound(
    repl: RLMRepl,
    trigger: Trigger,
    outbox: MockOutbox,
    model: str,
    stats: Stats,
) -> None:
    """Wake up on a Telegram-shape webhook → reply via OpenAI → send to outbox."""
    payload = trigger.payload
    msg = payload.get("message", {})
    user_text = (msg.get("text") or "").strip()
    chat_id = int(msg.get("chat", {}).get("id") or 0)
    if not user_text or not chat_id:
        return

    # Bind the inbound text into the REPL as a real handle (RLM substrate).
    handle_name = f"inbound_{trigger.correlation_id[:8]}"
    await repl.bind(handle_name, user_text)
    print(f"  [inbound  ] chat={chat_id} text={user_text!r}")

    t0 = time.monotonic()
    reply, usage = await call_openai(
        system=_BOT_PERSONA,
        user=user_text,
        model=model,
        max_tokens=350,
    )
    dt = time.monotonic() - t0

    outbox.send(chat_id=chat_id, text=reply, kind="inbound_reply")
    stats.inbound += 1
    stats.in_tokens += usage["in"]
    stats.out_tokens += usage["out"]
    stats.wall_s += dt
    print(f"  [reply    ] {dt:5.2f}s  in={usage['in']:>4} out={usage['out']:>4}")
    print(f"             → {reply[:140]!r}")


async def emit_scheduled_tip(
    outbox: MockOutbox,
    chat_id: int,
    model: str,
    stats: Stats,
) -> None:
    """Cron-style: agent drafts a short tip → outbox."""
    print("  [cron     ] firing scheduled tip")
    t0 = time.monotonic()
    tip, usage = await call_openai(
        system=_BOT_PERSONA,
        user=(
            "Write a single-paragraph tip for a Python developer. "
            "Be specific, novel, and ≤200 chars."
        ),
        model=model,
        max_tokens=200,
    )
    dt = time.monotonic() - t0
    outbox.send(chat_id=chat_id, text=tip, kind="scheduled_tip")
    stats.scheduled += 1
    stats.in_tokens += usage["in"]
    stats.out_tokens += usage["out"]
    stats.wall_s += dt
    print(f"  [tip      ] {dt:5.2f}s  → {tip[:120]!r}")


async def emit_on_demand(
    outbox: MockOutbox,
    chat_id: int,
    prompt: str,
    model: str,
    stats: Stats,
) -> None:
    """Manual-seam fired one-off message → agent → outbox."""
    print(f"  [manual   ] on-demand send: {prompt!r}")
    t0 = time.monotonic()
    text, usage = await call_openai(
        system=_BOT_PERSONA,
        user=prompt,
        model=model,
        max_tokens=200,
    )
    dt = time.monotonic() - t0
    outbox.send(chat_id=chat_id, text=text, kind="on_demand")
    stats.on_demand += 1
    stats.in_tokens += usage["in"]
    stats.out_tokens += usage["out"]
    stats.wall_s += dt
    print(f"  [on-demand] {dt:5.2f}s  → {text[:120]!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def _post_json(host: str, port: int, path: str, body: dict[str, object]) -> int:
    """POST body as JSON via stdlib http.client in a thread (don't block loop)."""
    import http.client

    def _do() -> int:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request(
                "POST",
                path,
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )
            r = conn.getresponse()
            r.read()
            return r.status
        finally:
            conn.close()

    return await asyncio.to_thread(_do)


def _section(s: str) -> None:
    bar = "─" * 70
    print(f"\n{bar}\n  {s}\n{bar}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _amain(args: argparse.Namespace) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.stderr.write("error: OPENAI_API_KEY not set\n")
        return 1

    chat_id = 1234567
    model = args.model
    cycles = max(1, args.cycles)
    schedule_interval = max(2.0, args.interval)

    print("══════════════════════════════════════════════════════════════════════")
    print("  telegram_assistant — mock Telegram bus + real OpenAI + duhwave")
    print("══════════════════════════════════════════════════════════════════════")
    print(f"  model={model}  cycles={cycles}  interval={schedule_interval}s")

    with tempfile.TemporaryDirectory(prefix="telegram-demo-") as td:
        tmp = Path(td)
        outbox = MockOutbox(path=tmp / "telegram_outbox.jsonl")
        outbox.path.touch()
        trigger_log = TriggerLog(tmp / "triggers.jsonl")
        port = _free_port()

        # 1. Listener — accepts mock Telegram webhooks.
        listener = WebhookListener(log=trigger_log, port=port, host="127.0.0.1")
        await listener.start()
        print(f"  webhook listening on http://127.0.0.1:{port}/telegram/webhook")
        print(f"  outbox            → {outbox.path}")
        print(f"  trigger log       → {trigger_log._path}")

        # 2. RLM substrate — real subprocess REPL.
        repl = RLMRepl()
        await repl.start()

        stats = Stats()
        started = time.monotonic()

        try:
            # Inbound flow ------------------------------------------------
            _section("Flow A — INBOUND (Telegram → agent → reply)")
            user_questions = [
                "What's the difference between asyncio.gather and asyncio.wait?",
                "Recommend a python rate limiter library.",
            ]
            for q in user_questions:
                update = make_telegram_update(chat_id=chat_id, user_text=q)
                status = await _post_json(
                    "127.0.0.1", port, "/telegram/webhook", update
                )
                # Drain — listener writes to log async; we read the latest entry.
                # Give the listener a moment to write the trigger.
                await asyncio.sleep(0.2)
                triggers = trigger_log.replay()
                latest = triggers[-1]
                # Sanity check: our webhook arrived as a TriggerKind.WEBHOOK
                assert latest.kind == TriggerKind.WEBHOOK
                await handle_inbound(repl, latest, outbox, model, stats)

            # Scheduled flow ----------------------------------------------
            _section(f"Flow B — SCHEDULED (every {schedule_interval}s, {cycles}×)")
            for i in range(cycles):
                if i:
                    await asyncio.sleep(schedule_interval)
                await emit_scheduled_tip(outbox, chat_id, model, stats)

            # On-demand flow ----------------------------------------------
            _section("Flow C — ON-DEMAND (manual seam → one-off)")
            await emit_on_demand(
                outbox,
                chat_id,
                prompt="Wish me good luck on my deploy at 5pm.",
                model=model,
                stats=stats,
            )

        finally:
            await listener.stop()
            await repl.shutdown()

        wall = time.monotonic() - started

        # Final report ----------------------------------------------------
        _section("Outbox dump")
        with outbox.path.open() as f:
            for i, line in enumerate(f, 1):
                rec = json.loads(line)
                snippet = rec["text"].replace("\n", " ")[:100]
                print(f"  [{i}] {rec['kind']:14}  → {snippet!r}")

        # gpt-4o-mini list-price (April 2026): $0.15/M in, $0.60/M out
        cost = (stats.in_tokens / 1_000_000) * 0.15 + (
            stats.out_tokens / 1_000_000
        ) * 0.60
        _section("Ledger")
        print(f"  inbound replies:   {stats.inbound}")
        print(f"  scheduled tips:    {stats.scheduled}")
        print(f"  on-demand:         {stats.on_demand}")
        print(f"  total tokens:      in={stats.in_tokens:,}  out={stats.out_tokens:,}")
        print(f"  estimated cost:    ${cost:.4f}")
        print(f"  api wall:          {stats.wall_s:.2f}s")
        print(f"  total wall:        {wall:.2f}s")

        # Sanity check: the trigger log captured every inbound update.
        n_triggers = len(trigger_log.replay())
        assert n_triggers == len(user_questions), (
            f"expected {len(user_questions)} trigger(s), got {n_triggers}"
        )
        print(f"\n  ✓ trigger log captured {n_triggers}/{len(user_questions)} inbound webhooks")

    print("\ndemo complete.")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="telegram_assistant", description=__doc__)
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument(
        "--cycles", type=int, default=2, help="Number of scheduled tips to fire."
    )
    p.add_argument(
        "--interval", type=float, default=4.0, help="Seconds between scheduled tips."
    )
    return p.parse_args(argv)


def main() -> int:
    args = _parse_args(sys.argv[1:])
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("\n[telegram_assistant] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
