"""
bridge.py — Bridge between llm_wrapper.py and the SandboxBackend.

This module provides the `call_sandbox_chat()` function that is a
drop-in replacement for `call_z_ai_chat()` in llm_wrapper.py.

When the user selects backend="sandbox", the wrapper calls this
function instead of z-ai CLI. The function routes the query through
the internal agent chain (planner→executor→reviewer→critic) and
returns a text string in the same format as an LLM response.

Configuration:
    Set SUPER_Z_BACKEND env var to "sandbox" to activate.
    Or pass backend="sandbox" to run_skill().
    Or set backend = "sandbox" in config.toml.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Import sandbox backend
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from sandbox.backend import SandboxBackend


# ─── Module-level singleton ─────────────────────────────────────────────

_backend_instance: Optional[SandboxBackend] = None


def get_backend(skill_context: Optional[Dict] = None,
                verbose: bool = False) -> SandboxBackend:
    """Get or create the sandbox backend singleton."""
    global _backend_instance
    if _backend_instance is None or skill_context:
        _backend_instance = SandboxBackend(
            skill_context=skill_context or {},
            verbose=verbose,
        )
    return _backend_instance


def call_sandbox_chat(system_prompt: str,
                      user_prompt: str,
                      skill_name: str = "",
                      skill_md: str = "",
                      timeout_sec: int = 60,
                      verbose: bool = False) -> Optional[str]:
    """Drop-in replacement for call_z_ai_chat() in llm_wrapper.py.

    Args:
        system_prompt: The system prompt (usually SKILL.md content).
        user_prompt: The user's query.
        skill_name: Name of the skill being executed.
        skill_md: Full SKILL.md content for methodology extraction.
        timeout_sec: Timeout (ignored in sandbox, agents are in-process).
        verbose: Print debug information.

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
                  f"avg score: {stats['average_review_score']}", 
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
