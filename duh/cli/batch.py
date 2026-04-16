"""Batch mode runner for D.U.H. CLI (ADR-071 P1).

Processes multiple prompts from a file, one per line.
Each prompt is run through the engine independently and results are
written to stdout or to individual files in an output directory.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from duh.adapters.approvers import AutoApprover
from duh.adapters.file_store import FileStore
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.simple_compactor import SimpleCompactor
from duh.cli import exit_codes
from duh.constitution import build_system_prompt
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.providers.registry import build_model_backend, resolve_provider_name
from duh.tools.registry import get_all_tools

logger = logging.getLogger("duh")


def _wrap_batch_prompt(value: str) -> UntrustedStr:
    """Tag a batch-file prompt as USER_INPUT."""
    if isinstance(value, UntrustedStr):
        return value
    return UntrustedStr(value, TaintSource.USER_INPUT)


async def run_batch(args: argparse.Namespace) -> int:
    """Process multiple prompts from a file, one per line.

    Each non-empty, non-comment line in the file is treated as a separate
    prompt.  Results are printed to stdout (separated by markers) or, when
    ``--output-dir`` is given, written to individual numbered files.

    Returns the worst (highest) exit code seen across all prompts.
    """
    debug = getattr(args, "debug", False)
    if debug:
        logging.basicConfig(
            level=logging.DEBUG, stream=sys.stderr,
            format="[%(levelname)s] %(name)s: %(message)s",
        )

    # --- Read prompts from file ---
    batch_file = Path(args.file)
    if not batch_file.exists():
        sys.stderr.write(f"Error: Batch file not found: {batch_file}\n")
        return exit_codes.ERROR

    try:
        raw_lines = batch_file.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        sys.stderr.write(f"Error reading batch file: {exc}\n")
        return exit_codes.ERROR

    # Filter: skip blank lines and comment lines (starting with #)
    prompts = [
        line.strip() for line in raw_lines
        if line.strip() and not line.strip().startswith("#")
    ]

    if not prompts:
        sys.stderr.write("Error: No prompts found in batch file.\n")
        return exit_codes.ERROR

    # --- Prepare output directory if requested ---
    output_dir: Path | None = None
    if getattr(args, "output_dir", None):
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    # --- Resolve provider ---
    def _check_ollama() -> bool:
        try:
            import httpx
            r = httpx.get("http://localhost:11434/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    provider_name = resolve_provider_name(
        explicit_provider=getattr(args, "provider", None),
        model=getattr(args, "model", None),
        check_ollama=_check_ollama,
    )

    if not provider_name:
        sys.stderr.write(
            "Error: No provider available.\n"
            "  Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or start Ollama.\n"
        )
        return exit_codes.PROVIDER_ERROR

    backend = build_model_backend(provider_name, getattr(args, "model", None))
    if not backend.ok:
        sys.stderr.write(f"Error: {backend.error}\n")
        return exit_codes.PROVIDER_ERROR
    model = backend.model
    call_model = backend.call_model

    if debug:
        sys.stderr.write(f"[DEBUG] provider={provider_name} model={model}\n")

    cwd = os.getcwd()

    # --- Build tools ---
    tools = list(get_all_tools())

    # --- Build system prompt ---
    system_prompt = getattr(args, "system_prompt", None) or build_system_prompt()

    # --- Build executor and deps ---
    executor = NativeExecutor(tools=tools, cwd=cwd)
    compactor = SimpleCompactor()
    store = FileStore(cwd=cwd)

    # Batch mode always auto-approves (non-interactive)
    approver = AutoApprover()

    deps = Deps(
        call_model=call_model,
        run_tool=executor.run,
        approve=approver.check,
        compact=compactor.compact,
    )

    max_turns = getattr(args, "max_turns", 10) or 10

    engine_config = EngineConfig(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        max_turns=max_turns,
    )

    # --- Process each prompt ---
    worst_exit = exit_codes.SUCCESS
    total = len(prompts)

    for idx, prompt_text in enumerate(prompts, start=1):
        # Each prompt gets a fresh engine (independent conversation)
        engine = Engine(deps=deps, config=engine_config, session_store=store)

        sys.stderr.write(f"[batch {idx}/{total}] {prompt_text[:60]}...\n")

        output_parts: list[str] = []
        prompt_exit = exit_codes.SUCCESS

        async for event in engine.run(prompt_text):
            event_type = event.get("type", "")

            if event_type == "text_delta":
                output_parts.append(event.get("text", ""))

            elif event_type == "error":
                error_text = event.get("error", "unknown")
                sys.stderr.write(f"  Error: {error_text[:200]}\n")
                prompt_exit = exit_codes.classify_error(error_text)

            elif event_type == "assistant":
                msg = event.get("message")
                if isinstance(msg, Message) and msg.metadata.get("is_error"):
                    prompt_exit = exit_codes.classify_error(msg.text)

            elif event_type == "budget_exceeded":
                prompt_exit = exit_codes.BUDGET_EXCEEDED

        result_text = "".join(output_parts)

        # Write output
        if output_dir is not None:
            out_path = output_dir / f"{idx:04d}.txt"
            out_path.write_text(result_text, encoding="utf-8")
            sys.stderr.write(f"  -> {out_path}\n")
        else:
            sys.stdout.write(f"--- prompt {idx}/{total} ---\n")
            sys.stdout.write(result_text)
            if result_text and not result_text.endswith("\n"):
                sys.stdout.write("\n")

        # Track worst exit code
        if prompt_exit > worst_exit:
            worst_exit = prompt_exit

    sys.stderr.write(f"[batch] Completed {total} prompt(s).\n")
    return worst_exit
