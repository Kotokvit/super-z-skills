"""
bridge.py — Bridge between llm_wrapper.py and the Sandbox Backend.

DEFAULT (v2): Observer + POLER + Sandbox — token-efficient
    1 LLM call for generation + optional Observer binary checks
    Total: 1-3 LLM calls, ~500-1500 tokens, 3-15s

LEGACY (v1): 4-agent chain — Planner→Executor→Reviewer→Critic
    4 full LLM calls per round, up to 3 rounds
    Total: 4-12 LLM calls, ~5000-15000 tokens, 25-80s

Configuration:
    Set SUPER_Z_BACKEND env var:
        "sandbox-v2" or "sandbox" → Observer + POLER (default, token-efficient)
        "sandbox-v1" or "sandbox-legacy" → 4-agent chain (legacy)
        "mock" → mock responses (testing)

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

from sandbox.sandbox_v2 import SandboxV2
from sandbox.backend import SandboxBackend
from sandbox.llm_provider import (
    LLMProvider, HostLLMProvider, MockLLMProvider,
)


# ─── Module-level singletons ─────────────────────────────────────────────

_backend_v2_instance: Optional[SandboxV2] = None
_backend_v1_instance: Optional[SandboxBackend] = None


def get_backend(
    backend_type: str = "auto",
    skill_context: Optional[Dict] = None,
    llm_provider: Optional[LLMProvider] = None,
    verbose: bool = False,
    use_observer: bool = False,
) -> SandboxV2 | SandboxBackend:
    """Get or create the appropriate sandbox backend.

    Args:
        backend_type: "auto", "v2", "v1", or "mock"
        skill_context: Skill metadata dict
        llm_provider: Optional LLM provider override
        verbose: Print debug info
        use_observer: Enable Observer binary checks (v2 only)

    Returns:
        SandboxV2 (default) or SandboxBackend (legacy)
    """
    if backend_type == "auto":
        backend_type = _detect_backend_type()

    if backend_type in ("v2", "sandbox-v2", "sandbox"):
        return _get_v2_backend(skill_context, llm_provider, verbose, use_observer)
    elif backend_type in ("v1", "sandbox-v1", "sandbox-legacy"):
        return _get_v1_backend(skill_context, llm_provider, verbose)
    elif backend_type == "mock":
        return _get_v2_backend(
            skill_context,
            MockLLMProvider(),
            verbose,
            use_observer=False,
        )
    else:
        return _get_v2_backend(skill_context, llm_provider, verbose, use_observer)


def _get_v2_backend(
    skill_context: Optional[Dict],
    llm_provider: Optional[LLMProvider],
    verbose: bool,
    use_observer: bool,
) -> SandboxV2:
    """Get or create the v2 backend singleton."""
    global _backend_v2_instance
    if llm_provider is None:
        llm_provider = _detect_host_llm_provider()

    _backend_v2_instance = SandboxV2(
        llm_provider=llm_provider,
        skill_context=skill_context or {},
        verbose=verbose,
        use_observer=use_observer,
    )
    return _backend_v2_instance


def _get_v1_backend(
    skill_context: Optional[Dict],
    llm_provider: Optional[LLMProvider],
    verbose: bool,
) -> SandboxBackend:
    """Get or create the v1 backend singleton (legacy)."""
    global _backend_v1_instance
    if llm_provider is None:
        llm_provider = _detect_host_llm_provider()

    _backend_v1_instance = SandboxBackend(
        llm_provider=llm_provider,
        skill_context=skill_context or {},
        verbose=verbose,
    )
    return _backend_v1_instance


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


def _detect_backend_type() -> str:
    """Detect backend type from env var.

    Returns:
        "v2" (default), "v1", or "mock"
    """
    env_backend = os.environ.get("SUPER_Z_BACKEND", "").lower()
    if env_backend in ("sandbox-v1", "v1", "legacy", "sandbox-legacy"):
        return "v1"
    if env_backend in ("mock", "test", "dry"):
        return "mock"
    # Default to v2 (token-efficient)
    return "v2"


# ─── Main API functions ──────────────────────────────────────────────────

def call_sandbox_chat(
    system_prompt: str,
    user_prompt: str,
    skill_name: str = "",
    skill_md: str = "",
    timeout_sec: int = 120,
    verbose: bool = False,
    llm_provider: Optional[LLMProvider] = None,
    backend: str = "v2",
    use_observer: bool = False,
) -> Optional[str]:
    """Drop-in replacement for call_z_ai_chat() in llm_wrapper.py.

    v2 (default): Observer + POLER — 1-3 LLM calls, ~500-1500 tokens
    v1 (legacy): 4-agent chain — 4-12 LLM calls, ~5000-15000 tokens

    Args:
        system_prompt: The system prompt (usually SKILL.md content).
        user_prompt: The user's query.
        skill_name: Name of the skill being executed.
        skill_md: Full SKILL.md content for methodology extraction.
        timeout_sec: Timeout per LLM call.
        verbose: Print debug information.
        llm_provider: Optional LLMProvider override.
        backend: "v2" (default), "v1", or "mock".
        use_observer: Enable Observer binary checks (v2 only).

    Returns:
        Text string (same format as LLM response), or None on failure.
    """
    try:
        skill_context = {
            "skill_name": skill_name,
            "skill_md": skill_md or system_prompt,
        }

        be = get_backend(
            backend_type=backend,
            skill_context=skill_context,
            llm_provider=llm_provider,
            verbose=verbose,
            use_observer=use_observer,
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

        result = be.chat(messages)

        if result and verbose:
            stats = be.stats()
            if isinstance(be, SandboxV2):
                print(f"[sandbox-v2] Stats: {stats.get('total_llm_calls', '?')} LLM calls, "
                      f"{stats.get('observer_decisions', 0)} observer decisions, "
                      f"provider: {stats.get('llm_provider', '?')}",
                      file=sys.stderr)
            else:
                print(f"[sandbox-v1] Stats: {stats.get('total_rounds', '?')} rounds, "
                      f"avg score: {stats.get('average_review_score', '?')}, "
                      f"LLM calls: {stats.get('llm_calls', '?')}, "
                      f"provider: {stats.get('llm_provider', '?')}",
                      file=sys.stderr)

        return result

    except Exception as e:
        if verbose:
            print(f"[sandbox] Error: {e}", file=sys.stderr)
        return None


# Backward-compatible alias
call_sandbox_v2_chat = call_sandbox_chat


def get_current_backend_type() -> str:
    """Determine which backend to use from environment/config.

    Priority:
        1. SUPER_Z_BACKEND env var
        2. config.toml [llm] backend setting
        3. Default: "v2" (token-efficient)

    Returns:
        One of: "v2", "v1", "mock", "zai_cli"
    """
    # Check environment variable first
    env_backend = os.environ.get("SUPER_Z_BACKEND", "").lower()
    if env_backend in ("sandbox-v1", "v1", "sandbox-legacy", "legacy"):
        return "v1"
    if env_backend in ("sandbox-v2", "v2", "sandbox", "local", "internal"):
        return "v2"
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

                if be.lower() in ("sandbox-v1", "v1", "legacy"):
                    return "v1"
                if be.lower() in ("sandbox-v2", "v2", "sandbox", "local", "internal"):
                    return "v2"
                if be.lower() in ("mock", "test"):
                    return "mock"
            except Exception:
                pass

    # Default: v2 (token-efficient)
    return "v2"
