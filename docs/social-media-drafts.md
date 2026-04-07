# D.U.H. Launch Posts — Draft

## Twitter/X Thread

**Tweet 1 (hook):**
Everyone's talking about claw-code (119K stars in 24 hours) and the Claude Code leak.

But the real question isn't "can we clone Claude Code?"

It's: "Why are we locked into one vendor's CLI for AI coding?"

I built D.U.H. to answer that differently. Thread:

**Tweet 2 (what):**
D.U.H. (D.U.H. is a Universal Harness) — provider-agnostic AI coding harness.

Same model? Use Claude, GPT-4o, or Ollama local models.
Same tools? Read, Write, Edit, Bash, Glob, Grep, MCP.
Same skills? Loads from .claude/skills/ natively.
Same protocol? Claude Agent SDK compatible.

Different: open, multi-provider, yours.

**Tweet 3 (proof):**
Not vaporware. Numbers:

- 954 tests passing
- 3 providers (Anthropic, OpenAI, Ollama)
- Full stream-json NDJSON protocol
- Skills load from .claude/ AND .duh/
- Claude Agent SDK e2e verified
- Interactive REPL with /slash commands
- Hooks system for automation

All in ~6K lines of Python.

**Tweet 4 (SDK compat):**
The killer feature: SDK compatibility.

Any app using the Claude Agent SDK can swap to D.U.H. with ONE env var:

```
export DUH_CLI_PATH=/path/to/duh-sdk-shim
```

We tested this with a production FastAPI app (Universal Companion API). Server starts, chat works. Drop-in.

**Tweet 5 (philosophy):**
George Hotz said it well about Anthropic blocking opencode:

"You will not convert people back to Claude Code — you will convert people to other model providers."

D.U.H. isn't anti-Anthropic. Claude is great. But your coding harness shouldn't be married to one vendor.

**Tweet 6 (CTA):**
D.U.H. is evolving. Not done. Not claiming to replace Claude Code.

But if you believe AI coding tools should be:
- Open source
- Provider agnostic
- SDK compatible
- Your infrastructure, not theirs

Try it: github.com/nikhilvallishayee/duh

PRs welcome. Especially from people who've been burned by vendor lock-in.

---

## LinkedIn Post

**Title: Why I Built an Open Alternative to Claude Code (And Why It Matters)**

The Claude Code source leak last week spawned claw-code — 119K GitHub stars in 24 hours. The fastest-growing repo in GitHub history.

But cloning isn't the answer. Clean-room reimplementation still leaves you dependent on one vendor's architecture, one vendor's protocol, one vendor's decisions about what you can and can't do with your own tooling.

I've been building D.U.H. (D.U.H. is a Universal Harness) — a provider-agnostic AI coding harness that takes a fundamentally different approach:

**The harness should be yours. The model should be your choice.**

What D.U.H. does:
- Runs Claude, GPT-4o, or local Ollama models through one interface
- Speaks the same NDJSON protocol as Claude Code (verified with the Claude Agent SDK)
- Loads skills from .claude/skills/ (your existing Claude skills work as-is)
- Has 954 tests, 19 architecture decision records, and hexagonal architecture
- Can be used as a drop-in Claude Code replacement in any Agent SDK app

What it doesn't do:
- It doesn't claim to be "better" than Claude Code
- It doesn't copy anyone's source code
- It doesn't lock you into another vendor

The engineering insight: AI coding tools are becoming infrastructure. And infrastructure should be open, interoperable, and under your control.

George Hotz recently pointed out that Anthropic blocking alternative clients from their API will "convert people to other model providers." He's right. The moat for AI companies isn't the CLI wrapper — it's the model quality. Let the tools be open.

D.U.H. is evolving. It's not done. But 954 passing tests and verified SDK compatibility means it's real.

If you've been thinking about vendor lock-in in your AI coding toolchain, or if you want a harness that works with ANY model, check it out:

github.com/nikhilvallishayee/duh

I'd especially value feedback from engineering leaders who've thought about this at the organizational level. What would it take for you to move your team off a proprietary AI coding tool?

#AIEngineering #OpenSource #DeveloperTools #ClaudeCode #AICoding

---

## Reddit Post (r/programming or r/LocalLLaMA)

**Title: D.U.H. — an open, provider-agnostic alternative to Claude Code with 954 tests and SDK compatibility**

With the Claude Code leak and claw-code going viral (119K stars), there's a lot of energy around open-source AI coding tools. I've been working on something different.

**D.U.H. (D.U.H. is a Universal Harness)** is a provider-agnostic AI coding harness. Not a clone of Claude Code — a clean-room harness that speaks the same protocol.

**What makes it different from claw-code, OpenCode, Aider, etc.:**

1. **Provider agnostic** — Claude, GPT-4o, and Ollama (local models) through one interface. Set an env var, switch providers.

2. **SDK compatible** — The Claude Agent SDK can use D.U.H. as a drop-in backend. We tested this with a production FastAPI app. One env var: `DUH_CLI_PATH=/path/to/duh-sdk-shim`.

3. **Skill parity** — Loads skills from `.claude/skills/` directories. If you've built skills for Claude Code, they work in D.U.H. without changes.

4. **Actually tested** — 954 tests, hexagonal architecture (ports & adapters), 19 ADRs. Not a weekend hack.

5. **Interactive REPL** — `duh` enters a readline REPL with `/help`, `/model`, `/status`, `/clear`, `/exit`.

**Quick comparison:**

| | D.U.H. | Claude Code | claw-code | OpenCode | Aider |
|---|---|---|---|---|---|
| Open source | Yes | No | Yes (grey) | Yes | Yes |
| Multi-provider | 3 built-in | Anthropic | Multi | 75+ | Multi |
| SDK compat | Yes | N/A | Partial | No | No |
| Skills from .claude/ | Yes | Yes | Unknown | No | No |
| Tests | 954 | Internal | Minimal | Unknown | Yes |

**What it's NOT:**
- Not production-hardened yet (it's evolving)
- Not a full Claude Code replacement (no TUI, no native agent subprocesses yet)
- Not a copy of anyone's code (clean-room, Apache 2.0)

**Try it:**
```
pip install anthropic openai  # or just use Ollama
git clone https://github.com/nikhilvallishayee/duh
cd duh && pip install -e .
duh -p "what files are here?" --provider ollama
```

Feedback and PRs welcome. Especially interested in: what features matter most to you in an AI coding harness?

github.com/nikhilvallishayee/duh
