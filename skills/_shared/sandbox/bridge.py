"""
bridge.py — Bridge between llm_wrapper.py and the SandboxBackend.

This module provides the `call_sandbox_chat()` function that is a
drop-in replacement for `call_z_ai_chat()` in llm_wrapper.py.

When the user selects backend="sandbox", the wrapper calls this
function instead of z-ai CLI. The function:
1. Creates an LLMProvider (defaults to z-ai CLI as the host LLM)
2. Creates a SandboxBackend with that provider
3. Routes the query through the agent chain (planner→executor→reviewer→critic)
4. Each agent calls the SAME LLM with a DIFFERENT role prompt
5. Returns a text string in the same format as an LLM response

Configuration:
    Set SUPER_Z_BACKEND env var to "sandbox" to activate.
    Or pass backend="sandbox" to run_skill().
    Or set backend = "sandbox" in config.toml.

    Set SUPER_Z_HOST_LLM env var to override the host LLM command.
    Default: z-ai CLI
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Import sandbox backend
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from sandbox.backend import SandboxBackend
from sandbox.llm_provider import (
    LLMProvider, HostLLMProvider, MockLLMProvider,
)


# ─── Module-level singleton ─────────────────────────────────────────────

_backend_instance: Optional[SandboxBackend] = None


def get_backend(skill_context: Optional[Dict] = None,
                llm_provider: Optional[LLMProvider] = None,
                verbose: bool = False) -> SandboxBackend:
    """Get or create the sandbox backend singleton."""
    global _backend_instance
    if _backend_instance is None or skill_context:
        # Determine the LLM provider
        if llm_provider is None:
            llm_provider = _detect_host_llm_provider()

        _backend_instance = SandboxBackend(
            llm_provider=llm_provider,
            skill_context=skill_context or {},
            verbose=verbose,
        )
    return _backend_instance


def _detect_host_llm_provider() -> LLMProvider:
    """Detect which LLM provider to use for sandbox agents.

    Priority:
        1. SUPER_Z_HOST_LLM_CALLBACK env var (Python import path)
        2. SUPER_Z_HOST_LLM env var (CLI command)
        3. Default: z-ai CLI

    Returns:
        LLMProvider instance.
    """
    # Check for callback (Python import path like "mymodule.my_llm_func")
    callback_path = os.environ.get("SUPER_Z_HOST_LLM_CALLBACK", "")
    if callback_path:
        try:
            module_path, func_name = callback_path.rsplit(".", 1)
            import importlib
            module = importlib.import_module(module_path)
            callback = getattr(module, func_name)
            return HostLLMProvider(callback=callback)
        except Exception as e:
            sys.stderr.write(f"[bridge] Failed to load callback {callback_path}: {e}\n")

    # Check for CLI command
    cli_command = os.environ.get("SUPER_Z_HOST_LLM", "")
    if cli_command:
        return HostLLMProvider(cli_command=cli_command)

    # Default: z-ai CLI
    return HostLLMProvider.from_zai_cli()


def call_sandbox_chat(system_prompt: str,
                      user_prompt: str,
                      skill_name: str = "",
                      skill_md: str = "",
                      timeout_sec: int = 120,
                      verbose: bool = False,
                      llm_provider: Optional[LLMProvider] = None) -> Optional[str]:
    """Drop-in replacement for call_z_ai_chat() in llm_wrapper.py.

    Instead of one LLM call, this runs the query through a chain of
    LLM-powered agents, each with a different role-specific prompt.

    Args:
        system_prompt: The system prompt (usually SKILL.md content).
        user_prompt: The user's query.
        skill_name: Name of the skill being executed.
        skill_md: Full SKILL.md content for methodology extraction.
        timeout_sec: Timeout per LLM call (agents may make multiple calls).
        verbose: Print debug information.
        llm_provider: Optional LLMProvider override.

    Returns:
        Text string (same format as LLM response), or None on failure.
    """
    try:
        skill_context = {
            "skill_name": skill_name,
            "skill_md": skill_md or system_prompt,
        }

        backend = get_backend(
            skill_context=skill_context,
            llm_provider=llm_provider,
            verbose=verbose,
        )

        # Build messages in OpenAI-compatible format
        messages = []
        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt,
            })
        messages.append({
            "role": "user",
            "content": user_prompt,
        })

        result = backend.chat(messages)

        if result and verbose:
            stats = backend.stats()
            print(f"[sandbox] Stats: {stats['total_rounds']} rounds, "
                  f"avg score: {stats['average_review_score']}, "
                  f"LLM calls: {stats['llm_calls']}, "
                  f"provider: {stats['llm_provider']}",
                  file=sys.stderr)

        return result

    except Exception as e:
        if verbose:
            print(f"[sandbox] Error: {e}", file=sys.stderr)
        return None


def get_current_backend_type() -> str:
    """Determine which backend to use from environment/config.

    Priority:
        1. SUPER_Z_BACKEND env var
        2. config.toml [llm] backend setting
        3. Default: "zai_cli"

    Returns:
        One of: "zai_cli", "sandbox", "mock"
    """
    # Check environment variable first
    env_backend = os.environ.get("SUPER_Z_BACKEND", "").lower()
    if env_backend in ("sandbox", "local", "internal"):
        return "sandbox"
    if env_backend in ("mock", "test", "dry"):
        return "mock"
    if env_backend in ("zai", "zai_cli", "z-ai"):
        return "zai_cli"

    # Check config file
    config_paths = [
        Path.home() / ".config" / "super-z" / "config.toml",
        Path.home() / ".config" / "super-z" / "config.json",
        Path("/etc/super-z/config.toml"),
    ]
    for cp in config_paths:
        if cp.exists():
            try:
                content = cp.read_text(encoding="utf-8")
                if cp.suffix == ".json":
                    import json
                    cfg = json.loads(content)
                    be = cfg.get("llm", {}).get("backend", "")
                else:
                    # Simple TOML parsing (no dependency)
                    for line in content.split("\n"):
                        if "backend" in line and "=" in line:
                            be = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
                    else:
                        be = ""

                if be.lower() in ("sandbox", "local", "internal"):
                    return "sandbox"
                if be.lower() in ("mock", "test"):
                    return "mock"
            except Exception:
                pass

    # Default
    return "zai_cli"
