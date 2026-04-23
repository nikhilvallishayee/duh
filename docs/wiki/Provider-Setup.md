# Provider Setup

D.U.H. supports multiple LLM providers out of the box. Provider auto-detection means you usually just set an API key and go -- but you can also configure providers explicitly.

## Anthropic

Anthropic's Claude models are the default provider when `ANTHROPIC_API_KEY` is set.

### Setup

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Usage

```bash
# Auto-detected (if ANTHROPIC_API_KEY is set)
duh -p "hello"

# Explicit provider and model
duh --provider anthropic --model claude-sonnet-4-6 -p "hello"
```

### Available Models

| Model | Description |
|-------|-------------|
| `claude-opus-4-6` | Most capable, best for complex reasoning and architecture |
| `claude-sonnet-4-6` | Balanced performance and cost (default) |
| `claude-haiku-4-5` | Fastest and cheapest, good for simple tasks |

### Configuration

In `.duh/settings.json`:

```json
{
    "provider": "anthropic",
    "model": "claude-sonnet-4-6"
}
```

Or via environment variables:

```bash
export DUH_PROVIDER=anthropic
export DUH_MODEL=claude-sonnet-4-6
```

---

## OpenAI

D.U.H. supports two OpenAI connection methods: API key (standard) and ChatGPT OAuth (Codex models).

### API Key Setup

```bash
export OPENAI_API_KEY="sk-..."
```

```bash
duh --provider openai --model gpt-4o -p "hello"
```

### ChatGPT Plus/Pro (Codex Models)

For ChatGPT subscription users with access to Codex models, D.U.H. supports OAuth authentication:

```bash
# Start the OAuth flow in the REPL
duh
> /connect openai
# Follow the browser-based PKCE OAuth flow
# Tokens are stored in ~/.config/duh/auth.json (mode 0600)
```

Once authenticated, you can use Codex models:

```bash
duh --model gpt-5.2-codex -p "refactor the database layer"
```

### Available Models

**API Key models:**

| Model | Description |
|-------|-------------|
| `gpt-4o` | Latest GPT-4o, strong general purpose |
| `gpt-4o-mini` | Smaller, faster, cheaper |
| `o1` | Reasoning model |
| `o3` | Advanced reasoning |

**ChatGPT Codex models (OAuth required):**

| Model | Description |
|-------|-------------|
| `gpt-5.2-codex` | Latest Codex, best coding performance |
| `gpt-5.1-codex` | Previous generation Codex |
| `gpt-5.1-codex-max` | Max-context variant |
| `gpt-5.1-codex-mini` | Smaller, faster Codex |

### Smart Routing

D.U.H. auto-routes based on model name: `--model gpt-5.2-codex` uses the ChatGPT Responses endpoint if OAuth credentials exist, otherwise falls back to the standard Chat Completions API with your API key.

---

## Gemini

D.U.H. ships a native Gemini adapter (`duh/adapters/gemini.py`) built on the `google-genai` SDK. This is preferred over the LiteLLM fallback because it surfaces provider-specific features (`thinking_budget`, explicit cache objects, the system-instructions / system-role distinction) that LiteLLM's OpenAI-shaped normalization flattens.

### Setup

Get a free API key at [aistudio.google.com](https://aistudio.google.com), then:

```bash
export GEMINI_API_KEY="AIza..."
# (GOOGLE_API_KEY also works — see PROVIDER_ENV_VARS in duh/providers/registry.py)
```

### Usage

```bash
# Explicit provider
duh --provider gemini --model gemini-2.5-pro -p "hello"

# Auto-detected from model prefix — gemini/ and gemini- both route to the native adapter
duh --model gemini-2.5-flash -p "hello"
duh --model gemini/gemini-3.1-pro-preview -p "hello"

# In-REPL
duh
> /connect gemini
```

### Available Models

| Model | Description |
|-------|-------------|
| `gemini-2.5-pro` | Strong general-purpose reasoning, 2M context |
| `gemini-2.5-flash` | Fast and cheap, 1M context |
| `gemini-3.1-pro-preview` | Latest preview, 1M context, strongest reasoning |

Run `duh models gemini` (or `/models gemini` in the REPL) to see what your key can actually access.

### Notes

- Native adapter requires `google-genai` (installed by default in `duh-cli`). If it's not importable, D.U.H. falls through to LiteLLM and logs a deprecation notice ([ADR-075](../adrs/ADR-075-drop-litellm-native-adapters.md)).
- Supports `thinking_budget` for the 2.5 / 3.1 reasoning variants.
- Explicit cache objects (`client.caches.create(...)`) are wired through the adapter for long-system-prompt workloads.

---

## Groq

D.U.H. ships a native Groq adapter (`duh/adapters/groq.py`) built on the `groq` SDK. Groq's LPU hardware offers extremely low latency (often <500ms first token on small models) and they host both their own Llama fine-tunes and OpenAI's open-weights 120B reasoning model.

### Setup

Get a free API key at [console.groq.com](https://console.groq.com), then:

```bash
export GROQ_API_KEY="gsk_..."
```

### Usage

```bash
# Explicit provider
duh --provider groq --model llama-3.3-70b-versatile -p "hello"

# Prefix routing
duh --model groq/openai/gpt-oss-120b -p "refactor this function"
```

### Available Models

| Model | Description |
|-------|-------------|
| `llama-3.3-70b-versatile` | Default. Balanced quality and speed |
| `openai/gpt-oss-120b` | OpenAI's open-weights 120B reasoning model hosted on Groq (strongest) |
| `llama-3.1-8b-instant` | Fastest, cheapest. Good for research / search subagents |

### Notes

- Native adapter preserves `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers so the engine can tune batch size and back off gracefully.
- Free tier has generous rate limits but they're enforced; `duh doctor` includes a Groq reachability check.

---

## Ollama

Ollama lets you run models locally with no API keys and no data leaving your machine.

### Setup

1. Install Ollama: [ollama.com](https://ollama.com)
2. Start the daemon:

```bash
ollama serve
```

3. Pull a model:

```bash
ollama pull llama3.1
ollama pull codellama
ollama pull deepseek-coder-v2
```

### Usage

```bash
# Explicit provider
duh --provider ollama --model llama3.1 -p "hello"

# Auto-detected if Ollama is running and no API keys are set
duh --model codellama -p "fix this function"
```

### Configuration

Ollama runs on `localhost:11434` by default. D.U.H. auto-detects it when no API keys are set.

In `.duh/settings.json`:

```json
{
    "provider": "ollama",
    "model": "llama3.1"
}
```

### Notes

- No API key required -- Ollama runs entirely locally
- Model quality varies -- larger models (70B+) give better coding results
- First run downloads the model weights (can be several GB)
- Great for privacy-sensitive work or offline usage

---

## Native vs LiteLLM fallback

As of [ADR-075](../adrs/ADR-075-drop-litellm-native-adapters.md), D.U.H. ships native adapters for every provider that is installed by default. LiteLLM is kept as an **opt-in fallback** for the long tail.

Motivation (see ADR-075 for the full write-up):

- **Supply-chain history**: LiteLLM 1.82.7 / 1.82.8 shipped malicious payloads in March 2026 after the maintainer's PyPI credentials were stolen. Several unrelated RCE / auth-bypass CVEs have followed (CVE-2026-40217, CVE-2026-35029, CVE-2026-35030). D.U.H. now floors LiteLLM at `>=1.83.8` and moves it behind a `[litellm]` extras group so a default install never pulls it in.
- **Feature fidelity**: native SDKs expose Anthropic `cache_control`, Gemini `thinking_budget` + explicit cache objects, and Groq rate-limit headers. LiteLLM's OpenAI-shaped normalization drops most of these.

### Adapter per provider

| Provider | Adapter | SDK package | Installed by default |
|----------|---------|-------------|----------------------|
| Anthropic | `duh/adapters/anthropic.py` (native) | `anthropic` | yes |
| OpenAI (API key) | `duh/adapters/openai.py` (native) | `openai` | yes |
| OpenAI (ChatGPT OAuth) | `duh/adapters/openai_chatgpt.py` (native) | `httpx` | yes |
| Ollama | `duh/adapters/ollama.py` (native) | `httpx` | yes |
| Gemini | `duh/adapters/gemini.py` (native) | `google-genai` | yes |
| Groq | `duh/adapters/groq.py` (native) | `groq` | yes |
| Long-tail (Azure, Bedrock, Vertex, Together, Cohere, Mistral, …) | `duh/adapters/litellm_provider.py` (fallback) | `litellm` | **no** (install `duh-cli[litellm]`) |

Routing (see `duh/providers/registry.py`):

- `gemini/<model>` or `gemini-<model>` → native `GeminiProvider` when `google-genai` is importable; otherwise falls through to LiteLLM.
- `groq/<model>` → native `GroqProvider` when the `groq` SDK is importable; otherwise LiteLLM.
- `--provider litellm` is always honored (override), and emits a single stderr deprecation notice per session.

Run `duh doctor` to see which adapters are available in your environment.

---

## LiteLLM (opt-in fallback)

LiteLLM is a universal proxy that supports 100+ LLM providers through a unified interface. As of ADR-075, it is opt-in — use it to connect D.U.H. to any provider without a native adapter (Azure, Bedrock, Vertex AI, Together, Fireworks, Cohere, Mistral, etc.).

### Setup

```bash
pip install 'duh-cli[litellm]'
# or, to install every optional adapter at once:
pip install 'duh-cli[all]'
```

Set the appropriate API key for your underlying provider:

```bash
# Example: Azure OpenAI
export AZURE_API_KEY="..."
export AZURE_API_BASE="https://your-resource.openai.azure.com"
export AZURE_API_VERSION="2024-02-01"

# Example: AWS Bedrock
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION_NAME="us-east-1"
```

### Usage

```bash
# Use LiteLLM model format: provider/model
duh --provider litellm --model azure/gpt-4o -p "hello"
duh --provider litellm --model bedrock/anthropic.claude-3-5-sonnet -p "hello"
duh --provider litellm --model together_ai/meta-llama/Llama-3-70b -p "hello"
```

### Configuration

In `.duh/settings.json`:

```json
{
    "provider": "litellm",
    "model": "azure/gpt-4o"
}
```

### Supported Providers (via LiteLLM)

LiteLLM supports many providers including Azure OpenAI, AWS Bedrock, Google Vertex AI, Together AI, Groq, Fireworks, Mistral, Cohere, Replicate, and more. See the [LiteLLM documentation](https://docs.litellm.ai/docs/providers) for the full list.

---

## Provider Auto-Detection

When no `--provider` flag is given and the model name does not contain a routing prefix, D.U.H. checks in order:

1. `ANTHROPIC_API_KEY` is set -- use Anthropic
2. `OPENAI_API_KEY` is set (or ChatGPT OAuth exists) -- use OpenAI
3. Ollama is reachable at `localhost:11434` -- use Ollama
4. None found -- display an actionable error message

If the model name itself has a prefix, that takes precedence:

- `gemini/…` / `gemini-…` → native Gemini (requires `google-genai`; falls through to LiteLLM if not importable)
- `groq/…` → native Groq (requires `groq` SDK; falls through to LiteLLM if not importable)
- Anything else with a `/` → LiteLLM (opt-in, install with `pip install 'duh-cli[litellm]'`)

You can always override with `--provider <name>`.

## Stub Provider (Testing)

For tests and offline development, D.U.H. includes a deterministic stub provider:

```bash
export DUH_STUB_PROVIDER=1
duh -p "hello"
# Returns: "stub-ok" (deterministic, no API calls)
```

This is used extensively in D.U.H.'s own test suite (4000+ tests run without any real provider calls).
