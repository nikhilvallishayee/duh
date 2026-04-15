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

## LiteLLM

LiteLLM is a universal proxy that supports 100+ LLM providers through a unified interface. Use it to connect D.U.H. to any provider LiteLLM supports (Azure, Bedrock, Vertex AI, Together, Groq, etc.).

### Setup

```bash
pip install litellm
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

When no `--provider` flag is given, D.U.H. checks in order:

1. `ANTHROPIC_API_KEY` is set -- use Anthropic
2. `OPENAI_API_KEY` is set -- use OpenAI
3. Ollama is reachable at `localhost:11434` -- use Ollama
4. None found -- display an actionable error message

You can always override with `--provider <name>`.

## Stub Provider (Testing)

For tests and offline development, D.U.H. includes a deterministic stub provider:

```bash
export DUH_STUB_PROVIDER=1
duh -p "hello"
# Returns: "stub-ok" (deterministic, no API calls)
```

This is used extensively in D.U.H.'s own test suite (4000+ tests run without any real provider calls).
