"""Tool-call format adapters for open-weights models (ADR-026).

Frontier closed models (claude-opus-4-7, gpt-5.x, gemini-3.1) emit
tool calls in the OpenAI-shape ``tool_calls=[{name, arguments}]``
structure. Open-weights models served via OpenRouter / vLLM /
llama.cpp / Ollama frequently do not — each family has its own wire
convention:

- Hermes / Llama-3.x finetunes: ``<tool_call>{json}</tool_call>``
- Gemma 3.x / 4.x: ``\`\`\`json {json} \`\`\``
- Mistral with [TOOL_CALLS] template: ``[TOOL_CALLS] [{json}, …]``
- GLM (morph): ``<tool>name</tool><args>{json}</args>``
- Default (passthrough): no transform — model is OpenAI-native

This module provides the parsing and prompt-injection middleware so
the rest of the agent loop sees a uniform OpenAI-shape view regardless
of which family produced the call.

Pattern adopted from the Vercel AI SDK ``@ai-sdk-tool/parser`` package
(used by OpenCode), translated into Python.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ParsedToolCall:
    """A tool call extracted from a model's text response.

    Mirrors the kernel's existing ``tool_use`` block shape so callers
    can splice these into the standard event stream without further
    translation.
    """

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolFormat:
    """A format-specific adapter pair.

    Attributes
    ----------
    name:
        Stable identifier — ``"hermes"``, ``"gemma"``, ``"mistral"``,
        ``"morph_xml"``, or ``"passthrough"``.
    inject_system:
        Returns a system-prompt fragment that teaches the model how to
        emit tool calls in this format. Called once per request.
        ``inject_system(tools, base_system_prompt) -> augmented_prompt``.
    parse_response:
        Extracts tool calls from the model's accumulated text output.
        Returns ``(cleaned_text, [ParsedToolCall, …])`` where
        ``cleaned_text`` is the text with tool-call markup stripped so
        it can still be shown to the user as the assistant's prose
        portion.
    """

    name: str
    inject_system: Callable[[list[Any], str], str]
    parse_response: Callable[[str], tuple[str, list[ParsedToolCall]]]


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def _tools_to_prompt_block(tools: list[Any]) -> str:
    """Serialise a tool list as a JSON-Schema-style instruction block."""
    if not tools:
        return ""
    serialised = []
    for t in tools:
        # Tools may be dicts (OpenAI shape) or dataclass-like objects.
        if isinstance(t, dict):
            name = t.get("name") or t.get("function", {}).get("name", "")
            desc = t.get("description") or t.get("function", {}).get("description", "")
            schema = t.get("input_schema") or t.get("function", {}).get("parameters", {})
        else:
            name = getattr(t, "name", "")
            desc = getattr(t, "description", "")
            schema = getattr(t, "input_schema", {})
        serialised.append({"name": name, "description": desc, "parameters": schema})
    return json.dumps(serialised, indent=2)


def _extract_first_json(text: str) -> dict[str, Any] | None:
    """Brace-counted extraction of the first JSON object in *text*.

    Tolerant of stray prose around the JSON, escaped quotes, and
    trailing commentary. Returns ``None`` on parse failure.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# passthrough — no-op (the OpenAI-native default)
# ---------------------------------------------------------------------------

def _passthrough_inject(tools: list[Any], base: str) -> str:
    return base


def _passthrough_parse(text: str) -> tuple[str, list[ParsedToolCall]]:
    return text, []


_PASSTHROUGH = ToolFormat("passthrough", _passthrough_inject, _passthrough_parse)


# ---------------------------------------------------------------------------
# hermes — <tool_call>{json}</tool_call>
# ---------------------------------------------------------------------------

_HERMES_INSTRUCTION = """\
You may call one or more tools. To call a tool, emit a block of the form:

<tool_call>{{"name": "<tool_name>", "arguments": {{<key>: <value>, …}}}}</tool_call>

Available tools (JSON Schema):

{tools_block}

Always emit valid JSON inside the <tool_call> block. Do not include
prose inside the block. After all tool calls in a turn, continue your
prose response normally.
"""

_HERMES_PATTERN = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def _hermes_inject(tools: list[Any], base: str) -> str:
    if not tools:
        return base
    block = _tools_to_prompt_block(tools)
    instr = _HERMES_INSTRUCTION.format(tools_block=block)
    return f"{base}\n\n{instr}".strip() if base else instr


def _hermes_parse(text: str) -> tuple[str, list[ParsedToolCall]]:
    calls: list[ParsedToolCall] = []
    for match in _HERMES_PATTERN.finditer(text):
        payload = _extract_first_json(match.group(1))
        if not payload:
            continue
        name = str(payload.get("name", ""))
        args = payload.get("arguments", {})
        if not name or not isinstance(args, dict):
            continue
        calls.append(ParsedToolCall(name=name, arguments=args))
    cleaned = _HERMES_PATTERN.sub("", text).strip()
    return cleaned, calls


_HERMES = ToolFormat("hermes", _hermes_inject, _hermes_parse)


# ---------------------------------------------------------------------------
# gemma — ```json {json} ``` markdown fences
# ---------------------------------------------------------------------------

_GEMMA_INSTRUCTION = """\
You may call one or more tools. To call a tool, emit a markdown
fenced JSON block:

```json
{{"tool_call": {{"name": "<tool_name>", "arguments": {{<key>: <value>, …}}}}}}
```

Available tools (JSON Schema):

{tools_block}

Each fenced block must contain exactly one ``tool_call`` object.
After all tool calls in a turn, continue your prose normally.
"""

_GEMMA_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _gemma_inject(tools: list[Any], base: str) -> str:
    if not tools:
        return base
    block = _tools_to_prompt_block(tools)
    instr = _GEMMA_INSTRUCTION.format(tools_block=block)
    return f"{base}\n\n{instr}".strip() if base else instr


def _gemma_parse(text: str) -> tuple[str, list[ParsedToolCall]]:
    calls: list[ParsedToolCall] = []
    cleaned_parts: list[str] = []
    last_end = 0
    for match in _GEMMA_PATTERN.finditer(text):
        cleaned_parts.append(text[last_end:match.start()])
        payload = _extract_first_json(match.group(1))
        last_end = match.end()
        if not payload:
            # Not a tool-call fence — keep it as prose.
            cleaned_parts.append(match.group(0))
            continue
        # Gemma format wraps under ``tool_call`` key; tolerate flat shape too.
        spec = payload.get("tool_call") if "tool_call" in payload else payload
        if not isinstance(spec, dict):
            cleaned_parts.append(match.group(0))
            continue
        name = str(spec.get("name", ""))
        args = spec.get("arguments", {})
        if not name or not isinstance(args, dict):
            cleaned_parts.append(match.group(0))
            continue
        calls.append(ParsedToolCall(name=name, arguments=args))
    cleaned_parts.append(text[last_end:])
    return "".join(cleaned_parts).strip(), calls


_GEMMA = ToolFormat("gemma", _gemma_inject, _gemma_parse)


# ---------------------------------------------------------------------------
# mistral — [TOOL_CALLS] [{...}, ...]
# ---------------------------------------------------------------------------

_MISTRAL_INSTRUCTION = """\
You may call one or more tools. To call tools, emit:

[TOOL_CALLS] [{{"name": "<tool_name>", "arguments": {{<key>: <value>}}}}, …]

The list contains one entry per tool call. Available tools (JSON Schema):

{tools_block}

After the [TOOL_CALLS] block, do not emit additional prose in the same
turn — the runtime will execute the calls and return their results in
the next turn.
"""

# Match [TOOL_CALLS] followed by a JSON array (possibly with surrounding ws).
_MISTRAL_PATTERN = re.compile(
    r"\[TOOL_CALLS\]\s*(\[.*?\])",
    re.DOTALL,
)


def _mistral_inject(tools: list[Any], base: str) -> str:
    if not tools:
        return base
    block = _tools_to_prompt_block(tools)
    instr = _MISTRAL_INSTRUCTION.format(tools_block=block)
    return f"{base}\n\n{instr}".strip() if base else instr


def _mistral_parse(text: str) -> tuple[str, list[ParsedToolCall]]:
    calls: list[ParsedToolCall] = []
    for match in _MISTRAL_PATTERN.finditer(text):
        try:
            arr = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(arr, list):
            continue
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", ""))
            args = entry.get("arguments", {})
            # Mistral sometimes inlines arguments as a JSON string.
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            if not name or not isinstance(args, dict):
                continue
            calls.append(ParsedToolCall(name=name, arguments=args))
    cleaned = _MISTRAL_PATTERN.sub("", text).strip()
    return cleaned, calls


_MISTRAL = ToolFormat("mistral", _mistral_inject, _mistral_parse)


# ---------------------------------------------------------------------------
# morph_xml — <tool>name</tool><args>{json}</args>
# ---------------------------------------------------------------------------

_MORPH_INSTRUCTION = """\
You may call one or more tools. To call a tool, emit:

<tool>tool_name</tool>
<args>{{<key>: <value>}}</args>

One pair per call. Available tools (JSON Schema):

{tools_block}
"""

_MORPH_PATTERN = re.compile(
    r"<tool>\s*([^<]+?)\s*</tool>\s*<args>\s*(.*?)\s*</args>",
    re.DOTALL,
)


def _morph_inject(tools: list[Any], base: str) -> str:
    if not tools:
        return base
    block = _tools_to_prompt_block(tools)
    instr = _MORPH_INSTRUCTION.format(tools_block=block)
    return f"{base}\n\n{instr}".strip() if base else instr


def _morph_parse(text: str) -> tuple[str, list[ParsedToolCall]]:
    calls: list[ParsedToolCall] = []
    for match in _MORPH_PATTERN.finditer(text):
        name = match.group(1).strip()
        args_payload = _extract_first_json(match.group(2)) or {}
        if not name:
            continue
        calls.append(ParsedToolCall(name=name, arguments=args_payload))
    cleaned = _MORPH_PATTERN.sub("", text).strip()
    return cleaned, calls


_MORPH_XML = ToolFormat("morph_xml", _morph_inject, _morph_parse)


# ---------------------------------------------------------------------------
# Public registry + per-model lookup
# ---------------------------------------------------------------------------

REGISTRY: dict[str, ToolFormat] = {
    "passthrough": _PASSTHROUGH,
    "hermes":      _HERMES,
    "gemma":       _GEMMA,
    "mistral":     _MISTRAL,
    "morph_xml":   _MORPH_XML,
}


# Conservative built-in patterns. Match against the full model id
# (e.g. ``openrouter/mistralai/mistral-large-2512``) — case-insensitive.
# Only models we have direct evidence for go in here; everything else
# defaults to passthrough (current behaviour).
_BUILTIN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"hermes|nous[-_]?hermes", re.I),               "hermes"),
    (re.compile(r"\bllama-?3(\.\d+)?\b.*\b(instruct|finetune)", re.I), "hermes"),
    (re.compile(r"gemma[-_]?\d", re.I),                          "gemma"),
    (re.compile(r"mistralai/.*(?<!-instruct)$|mistral-large", re.I), "mistral"),
    (re.compile(r"\bglm[-_]?\d", re.I),                          "morph_xml"),
]


def detect_format(model: str) -> str:
    """Return the format-name for a model id, or ``"passthrough"``.

    Matched against the built-in patterns. Users who want a different
    pairing can override at the adapter level (`tool_format=` kwarg).
    """
    if not model:
        return "passthrough"
    for pattern, fmt in _BUILTIN_PATTERNS:
        if pattern.search(model):
            return fmt
    return "passthrough"


def get_format(name: str) -> ToolFormat:
    """Look up a format by name. Falls back to passthrough on unknown."""
    return REGISTRY.get(name, _PASSTHROUGH)
