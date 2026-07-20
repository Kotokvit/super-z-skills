"""
llm_provider.py — Abstract LLM interface for sandbox agents.

The KEY insight: sandbox agents don't avoid LLM calls — they USE them,
but with different role-specific system prompts. This is exactly how
ChatGPT, Claude, Kimi 2.0 work internally: one LLM calls itself in
different roles (planner, executor, reviewer, critic).

The "pseudo-API" means: instead of calling OpenAI/Anthropic externally,
the host AI calls ITSELF through this provider interface. When the tool
runs inside an AI sandbox, the AI IS the LLM backend.

Three provider implementations:
1. HostLLMProvider  — the host AI calls itself (callback function)
2. ZAICLIProvider   — calls z-ai CLI (existing subprocess approach)
3. MockLLMProvider  — returns structured mock responses (for testing)
"""
from __future__ import annotations

import json
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class LLMProvider(ABC):
    """Abstract interface for LLM calls from agents.

    Each agent constructs its own role-specific prompt and calls
    provider.chat(system_prompt, user_prompt) to get a response.
    The provider handles the actual LLM interaction.
    """

    @abstractmethod
    def chat(self, system_prompt: str, user_prompt: str,
             timeout_sec: int = 60) -> Optional[str]:
        """Call the LLM with system + user prompts, return text response.

        Args:
            system_prompt: Role-specific instructions for this agent.
            user_prompt: The task/context for the agent to process.
            timeout_sec: Maximum time to wait for a response.

        Returns:
            LLM-generated text, or None on failure.
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """Return provider name for logging/metadata."""
        ...


# ─── Host LLM Provider (the main one for sandbox) ─────────────────────

class HostLLMProvider(LLMProvider):
    """The host AI calls itself through a callback.

    This is the core of the sandbox: the AI that runs the tool
    IS the LLM. When a planner agent needs to plan, it calls
    the host AI with a planner system prompt. When an executor
    agent needs to generate, it calls the host AI with an executor
    system prompt.

    Usage:
        # Option 1: Pass a Python callable (e.g., from the AI framework)
        provider = HostLLMProvider(callback=my_llm_function)

        # Option 2: Set SUPER_Z_HOST_LLM env var to a CLI command
        provider = HostLLMProvider.from_env()

        # Option 3: Use z-ai CLI as the host
        provider = HostLLMProvider.from_zai_cli()
    """

    def __init__(self, callback: Optional[Callable[[str, str], Optional[str]]] = None,
                 cli_command: Optional[str] = None):
        self._callback = callback
        self._cli_command = cli_command
        self._call_count = 0

    @classmethod
    def from_zai_cli(cls) -> "HostLLMProvider":
        """Create a provider that uses z-ai CLI as the host LLM."""
        import shutil
        z_ai = shutil.which("z-ai") or "/usr/local/bin/z-ai"
        return cls(cli_command=z_ai)

    @classmethod
    def from_env(cls) -> "HostLLMProvider":
        """Create a provider from SUPER_Z_HOST_LLM env var."""
        import os
        cmd = os.environ.get("SUPER_Z_HOST_LLM", "")
        if cmd:
            return cls(cli_command=cmd)
        # Fallback to z-ai CLI
        return cls.from_zai_cli()

    def chat(self, system_prompt: str, user_prompt: str,
             timeout_sec: int = 60) -> Optional[str]:
        self._call_count += 1

        # Priority 1: Python callback (direct in-process call)
        if self._callback:
            try:
                return self._callback(system_prompt, user_prompt)
            except Exception as e:
                sys.stderr.write(f"[HostLLM] callback error: {e}\n")
                return None

        # Priority 2: CLI command (subprocess call)
        if self._cli_command:
            return self._call_cli(system_prompt, user_prompt, timeout_sec)

        sys.stderr.write("[HostLLM] No callback or CLI command configured\n")
        return None

    def _call_cli(self, system_prompt: str, user_prompt: str,
                  timeout_sec: int) -> Optional[str]:
        """Call the LLM via CLI subprocess."""
        try:
            cmd = [
                self._cli_command, "chat",
                "--prompt", user_prompt,
                "--system", system_prompt,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
            if r.returncode != 0:
                sys.stderr.write(f"[HostLLM] CLI failed: {r.stderr[:300]}\n")
                return None

            out = r.stdout
            # Try to parse OpenAI-style JSON envelope
            envelope_start = out.find("{")
            if envelope_start >= 0:
                try:
                    envelope = json.loads(out[envelope_start:])
                    if isinstance(envelope, dict) and "choices" in envelope:
                        content = (
                            envelope.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                        )
                        if content and content.strip():
                            return content.strip()
                except json.JSONDecodeError:
                    pass
            return out.strip() if out.strip() else None

        except subprocess.TimeoutExpired:
            sys.stderr.write(f"[HostLLM] CLI timed out ({timeout_sec}s)\n")
            return None
        except Exception as e:
            sys.stderr.write(f"[HostLLM] CLI error: {e}\n")
            return None

    def name(self) -> str:
        if self._callback:
            return "host_callback"
        if self._cli_command:
            return f"host_cli:{Path(self._cli_command).name}"
        return "host_none"

    @property
    def call_count(self) -> int:
        return self._call_count


# ─── Mock LLM Provider (for testing) ──────────────────────────────────

class MockLLMProvider(LLMProvider):
    """Returns structured mock responses for testing.

    Mock responses simulate what a real LLM would return for each
    agent role, making tests deterministic without external dependencies.
    """

    def __init__(self, responses: Optional[Dict[str, str]] = None):
        self._responses = responses or {}
        self._call_count = 0
        self._call_log: List[Dict[str, str]] = []

    def chat(self, system_prompt: str, user_prompt: str,
             timeout_sec: int = 60) -> Optional[str]:
        self._call_count += 1
        self._call_log.append({
            "system_prompt": system_prompt[:200],
            "user_prompt": user_prompt[:200],
        })

        # Detect role from the FIRST LINE of system prompt.
        # NOTE: Cannot use simple substring search like "planner" in prompt,
        # because executor's prompt mentions "from the planner" which would
        # cause false matches. Check the role-specific header instead.
        prompt_lower = system_prompt.lower()
        if prompt_lower.startswith("you are a planner") or prompt_lower.startswith("you are the planner"):
            return self._responses.get("planner", self._mock_plan(user_prompt))
        elif prompt_lower.startswith("you are an executor") or prompt_lower.startswith("you are the executor"):
            return self._responses.get("executor", self._mock_execute(user_prompt))
        elif prompt_lower.startswith("you are a reviewer") or prompt_lower.startswith("you are the reviewer"):
            return self._responses.get("reviewer", self._mock_review(user_prompt))
        elif prompt_lower.startswith("you are a critic") or prompt_lower.startswith("you are the critic"):
            return self._responses.get("critic", self._mock_critique(user_prompt))
        else:
            return self._responses.get("default", f"[MOCK] {user_prompt[:100]}")

    def _mock_plan(self, query: str) -> str:
        return json.dumps({
            "subtasks": [
                {"step": 1, "type": "understand", "description": f"Analyze: {query[:100]}"},
                {"step": 2, "type": "generate", "description": f"Generate content for: {query[:100]}"},
                {"step": 3, "type": "synthesize", "description": "Combine into final result"},
            ],
            "methodology_hints": ["structured approach"],
            "output_format": "text",
        }, ensure_ascii=False)

    def _mock_execute(self, query: str) -> str:
        return (f"Сгенерированный контент по запросу: {query[:200]}\n\n"
                f"Это результат работы executor-агента, который получил "
                f"задание от planner-агента и создал структурированный ответ.")

    def _mock_review(self, query: str) -> str:
        return json.dumps({
            "verdict": "pass",
            "average_score": 0.85,
            "issues": [],
            "suggestions": [],
        }, ensure_ascii=False)

    def _mock_critique(self, query: str) -> str:
        return json.dumps({
            "decision": "accept",
            "reason": "Quality threshold met",
            "quality_score": 0.85,
        }, ensure_ascii=False)

    def name(self) -> str:
        return "mock"

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def call_log(self) -> List[Dict[str, str]]:
        return self._call_log
