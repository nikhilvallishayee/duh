# D.U.H. Launch Posts — Final Drafts

## Twitter/X Thread (6 tweets)

**Tweet 1 (hook):**
Anthropic blocked OpenCode, Cline, and RooCode from their API overnight. Zero warning. 147+ reactions on GitHub.

Then accidentally leaked 512K lines of Claude Code source via npm.

claw-code hit 100K stars in 24 hours.

The lesson isn't about the leak. It's about vendor lock-in. Here's what I built instead:

**Tweet 2 (what):**
D.U.H. — D.U.H. is a Universal Harness.

Provider-agnostic AI coding harness. Not a clone. Not a leak derivative. Clean-room, Apache 2.0.

- Claude, GPT-4o, or Ollama local models through ONE interface
- Loads skills from .claude/skills/ natively
- Claude Agent SDK compatible (verified e2e)
- 954 tests, hexagonal architecture

The harness is yours. The model is your choice.

**Tweet 3 (the numbers):**
@geaborel calculated Claude Code's real cost: $130-260/developer/month, not the advertised $20.

Cache bugs silently costing users 10-20x more in tokens (Theo Browne's analysis).

If the code were on GitHub, "issues like these would be trivial to identify and fix."

D.U.H. IS on GitHub. All 6K lines.

**Tweet 4 (SDK compat — the killer feature):**
The killer feature nobody else has: SDK drop-in.

Any app using the Claude Agent SDK switches to D.U.H. with ONE env var:

```
export DUH_CLI_PATH=/path/to/duh-sdk-shim
```

Tested on a production FastAPI app. Server starts, chat works, skills load. One line change.

No other open-source CLI tool does this.

**Tweet 5 (philosophy):**
@georgehotz said it best:

"You will not convert people back to Claude Code — you will convert people to other model providers."

@dhh called the blocking "very customer hostile."

@GergelyOrosz: Anthropic is "happy to have pretty much no ecosystem around Claude."

The moat is model quality. Not the CLI wrapper. Let the harness be open.

**Tweet 6 (CTA):**
D.U.H. is evolving. Not done. Not claiming to replace Claude Code.

But if you believe your AI coding infrastructure should be:
- Open source (Apache 2.0)
- Provider agnostic (3 today, more coming)
- SDK compatible (drop-in for Claude Agent SDK)
- Yours to control

github.com/nikhilvallishayee/duh

PRs welcome. What features matter most to you?

---

## LinkedIn Post

**Why I Built an Open Alternative to Claude Code — And What the Leak Revealed About Our Industry**

In January, Anthropic silently deployed server-side checks blocking OpenCode, Cline, and RooCode from authenticating with Claude. Zero warning. DHH called it "very customer hostile." George Hotz wrote that it would "convert people to other model providers."

Then in March, Anthropic accidentally shipped 512,000 lines of Claude Code source in an npm package. Within 24 hours, clean-room rewrites hit 100K GitHub stars.

But cloning isn't the answer.

I've been building D.U.H. (D.U.H. is a Universal Harness) — a provider-agnostic AI coding harness built on a different thesis:

**The harness should be yours. The model should be your choice.**

Here's what that means in practice:

**3 providers, one interface.** Claude, GPT-4o, or Ollama local models. Set an env var, switch providers. No code changes.

**Claude Agent SDK compatible.** Any app using the SDK can swap to D.U.H. with `DUH_CLI_PATH=/path/to/duh-sdk-shim`. We tested this on a production FastAPI app serving 590+ coaching skills. Server starts, chat works.

**Skill format parity.** D.U.H. loads from `.claude/skills/` directories natively. Skills you've built for Claude Code work without changes. `.duh/skills/` overrides by name if you need to diverge.

**954 tests.** Hexagonal architecture. 19 ADRs. Hooks system. MCP support. Interactive REPL. Not a weekend hack.

Netanel Eliav (CTO, Jam 7) calculated Claude Code's real cost at $130-260/developer/month. That's before the cache bugs Theo Browne identified, where users were silently paying 10-20x more in tokens.

The Pragmatic Engineer's 2026 survey found 70% of developers use 2-4 AI tools simultaneously. 95% use AI tools weekly. The industry is moving toward multi-tool, multi-provider workflows.

**Your AI coding harness is becoming infrastructure.** And infrastructure should be open, interoperable, and under your control.

D.U.H. is evolving — not done. But 954 tests and verified SDK compatibility means it's real.

github.com/nikhilvallishayee/duh

I'd value feedback from engineering leaders: what would it take for you to move your team off a proprietary AI coding tool?

#OpenSource #AIEngineering #DeveloperTools #AICoding #VendorLockin

---

## Reddit Post (r/programming, r/LocalLLaMA, or r/ClaudeCode)

**Title: I built a provider-agnostic alternative to Claude Code — 954 tests, SDK compatible, loads .claude/skills/ natively**

With the Claude Code leak and claw-code hitting 172K stars, there's massive energy around open-source AI coding tools. But most alternatives are either clones (legally grey) or don't interoperate with the existing ecosystem.

I built **D.U.H. (D.U.H. is a Universal Harness)** — a provider-agnostic AI coding harness. Not a clone. Clean-room Python, Apache 2.0.

**What makes it different:**

1. **Provider agnostic** — Claude, GPT-4o, Ollama (local models) through one interface. `duh -p "fix the bug" --provider openai` or `--provider ollama`. Auto-detects from env vars.

2. **SDK compatible (the killer feature)** — The Claude Agent SDK can use D.U.H. as a drop-in backend. One env var: `export DUH_CLI_PATH=/path/to/duh-sdk-shim`. Tested on a production FastAPI app with 590+ skills. Server starts, chat works, skills load.

3. **Skill format parity** — Loads skills from `.claude/skills/` AND `.duh/skills/`. Both flat (`skill.md`) and directory (`skill-name/SKILL.md`) layouts. All Claude Code frontmatter fields supported. Your existing Claude skills work as-is.

4. **Actually tested** — 954 tests, hexagonal architecture (ports & adapters), 19 ADRs, hooks system, MCP support, interactive REPL with /slash commands.

5. **Interactive REPL** — `duh` enters readline mode with `/help`, `/model`, `/status`, `/clear`, `/exit`.

**Comparison:**

| | D.U.H. | Claude Code | claw-code | OpenCode | Aider |
|---|---|---|---|---|---|
| Open source | Yes (Apache 2.0) | No | Yes (grey) | Yes | Yes |
| Multi-provider | 3 built-in | Anthropic only | Multi | 75+ | Multi |
| SDK drop-in | **Yes** | N/A | Partial | No | No |
| .claude/skills/ | **Yes** | Yes | Unknown | No | No |
| Tests | 954 | Internal | Minimal | Unknown | Yes |
| Legal status | Clean | Proprietary | Grey area | Clean | Clean |

**Context on why this matters:**

Anthropic blocked OpenCode, Cline, and RooCode from their API in January — zero warning. George Hotz wrote they'd "convert people to other model providers." Then the March 31 npm leak happened.

The real lesson: your coding harness shouldn't be married to one vendor. The moat is model quality, not the CLI wrapper.

**What it's NOT:**
- Not a full Claude Code replacement (no TUI, no native subagents yet)
- Not production-hardened for enterprise (it's evolving)
- Not a copy of anyone's code

**Try it:**
```bash
git clone https://github.com/nikhilvallishayee/duh
cd duh && pip install -e ".[dev]"
duh -p "what files are here?" --provider ollama
duh  # interactive REPL
```

Feedback welcome. What features matter most to you in an AI coding harness?

github.com/nikhilvallishayee/duh

---

## Suggested Engineering Leaders to Tag/Engage

**Twitter/X:**
- @georgehotz — Already outspoken on Anthropic blocking OpenCode
- @dhh — Called Anthropic "customer hostile"
- @GergelyOrosz — Covered the leak extensively, pragmatic engineering audience
- @swyx — AI engineering thought leader, latent.space
- @simonw — Django co-creator, AI tools analyst
- @wesbos — Found the 187 spinner verbs, massive dev audience
- @t3dotgg (Theo Browne) — Analyzed cache bugs, cost analysis

**LinkedIn:**
- Gergely Orosz (The Pragmatic Engineer)
- DHH (David Heinemeier Hansson)
- Netanel Eliav (CTO, Jam 7) — wrote the $130-260/dev cost analysis
- Sanchit Vir Gogia (Greyhound Research) — enterprise AI security angle

**Reddit:**
- Post to r/programming (broadest reach), r/LocalLLaMA (local model angle), r/ClaudeCode (most relevant community — 4,200+ weekly contributors)

---

## Key Sources for Credibility

When people ask "is this real?":
- 954 tests: `cd duh && python -m pytest -q` (3 seconds)
- SDK verified: `tests/e2e_sdk_smoke.py` (Assistant + Result messages)
- UC API verified: `tests/e2e_uc_chat_smoke.py` (full chat pipeline)
- claude-flow patch: `claude-flow-fork` branch `feat/duh-backend-support`
- GitHub: github.com/nikhilvallishayee/duh (39 commits, public)
