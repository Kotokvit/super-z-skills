#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
super_z_llm_callback.py — My "Voice" Inside the Core
=====================================================

THE PROBLEM:
    When super_z_core encounters an AI_REASONING skill (like blog-writer,
    content-analysis, etc.), it needs an LLM to generate the response.

    The OLD behavior: always call z-ai CLI (PAID, external subprocess)
    The NEW behavior: call the HOST AI's own reasoning (FREE, in-process)

    But super_z_core doesn't know HOW to call the host AI. It needs a
    callback function: something it can call with (system_prompt, user_prompt)
    and get back a text response.

THE SOLUTION:
    This module provides that callback. It's the "voice" of the host AI
    inside super_z_core. When the core says "I need LLM reasoning for
    this skill", this module answers: "Here, use MY brain."

    Three modes of operation:

    1. DIRECT CALLBACK (preferred):
       The host AI passes a Python callable directly.
       super_z_core calls it. FREE, instant, highest quality.

    2. BRIDGE + POLER (for POLER-heavy skills):
       For skills that primarily use POLER analysis with a thin LLM layer,
       this module can route through super_z_bridge for the POLER part
       and only use the callback for the final synthesis.

    3. CLI FALLBACK (last resort):
       When no callback is available, falls back to z-ai CLI.
       PAID, slower, but works anywhere.

ARCHITECTURE:
    ┌─────────────────┐
    │ super_z_core    │ "I need LLM reasoning for blog-writer"
    └────────┬────────┘
             │
             v
    ┌─────────────────────────┐
    │ super_z_llm_callback    │ "I'll handle it"
    │                         │
    │ Has callback?           │
    │   YES → call it (FREE)  │
    │   NO  → call CLI (PAID) │
    └─────────────────────────┘
         │           │
         v           v
    ┌─────────┐  ┌────────┐
    │ AI's    │  │ z-ai   │
    │ brain   │  │ CLI    │
    │ (FREE)  │  │ (PAID) │
    └─────────┘  └────────┘

USAGE:
    # Inside an AI platform (GLM, Claude, GPT)
    from super_z_llm_callback import LLMCallbackProvider

    # Create with the AI's own reasoning function
    provider = LLMCallbackProvider(callback=my_llm_function)
    result = provider.chat("You are a blog writer", "Write about AI")

    # Or set via environment variable
    os.environ["SUPER_Z_HOST_LLM_CALLBACK"] = "my_module.my_llm_func"

    # Or use the convenience function
    from super_z_llm_callback import execute_with_callback
    result = execute_with_callback(
        skill_name="blog-writer",
        system_prompt="You are a blog writer...",
        user_prompt="Write about AI trends",
    )

Author: Super-Z team + Qwen Coder, 2026-07-21
Version: 1.0.0
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


__version__ = "1.0.0"
__author__ = "Super-Z team + Qwen Coder"


# ═══════════════════════════════════════════════════════════════════════════
# LLM Callback Provider
# ═══════════════════════════════════════════════════════════════════════════

class LLMCallbackProvider:
    """Provider that uses the host AI's callback for LLM reasoning.

    This is the "free" LLM provider. When running inside an AI platform,
    the AI's own reasoning engine IS the LLM. No external call needed.

    The provider supports:
    1. Direct Python callback (highest priority, FREE)
    2. Callback loaded from environment variable (FREE)
    3. CLI fallback (PAID, last resort)

    Usage:
        # With direct callback
        provider = LLMCallbackProvider(callback=my_llm)
        result = provider.chat("system", "user")

        # Auto-detect from environment
        provider = LLMCallbackProvider.from_env()
        result = provider.chat("system", "user")
    """

    def __init__(
        self,
        callback: Optional[Callable[[str, str], Optional[str]]] = None,
        cli_command: Optional[str] = None,
        verbose: bool = False,
    ):
        """Initialize the callback provider.

        Args:
            callback: Python callable(system_prompt, user_prompt) -> response.
            cli_command: CLI command for fallback (paid).
            verbose: Print debug information.
        """
        self._callback = callback
        self._cli_command = cli_command
        self._verbose = verbose
        self._call_count = 0
        self._free_calls = 0
        self._paid_calls = 0
        self._failed_calls = 0
        self._total_time = 0.0

    @classmethod
    def from_env(cls, verbose: bool = False) -> "LLMCallbackProvider":
        """Create provider from environment variables.

        Detection priority:
            1. SUPER_Z_HOST_LLM_CALLBACK (Python import path) → FREE
            2. SUPER_Z_HOST_LLM (CLI command) → PAID
            3. z-ai CLI (auto-detected) → PAID

        Args:
            verbose: Print debug information.

        Returns:
            LLMCallbackProvider instance.
        """
        # Priority 1: Python callback from env
        callback_path = os.environ.get("SUPER_Z_HOST_LLM_CALLBACK", "")
        if callback_path:
            try:
                module_path, func_name = callback_path.rsplit(".", 1)
                import importlib
                module = importlib.import_module(module_path)
                callback = getattr(module, func_name)
                if verbose:
                    print(f"[llm_callback] Loaded callback from env: "
                          f"{callback_path}", file=sys.stderr)
                return cls(callback=callback, verbose=verbose)
            except Exception as e:
                if verbose:
                    print(f"[llm_callback] Failed to load callback: {e}",
                          file=sys.stderr)

        # Priority 2: Custom CLI command from env
        cli_command = os.environ.get("SUPER_Z_HOST_LLM", "")
        if cli_command:
            if verbose:
                print(f"[llm_callback] Using CLI from env: {cli_command}",
                      file=sys.stderr)
            return cls(cli_command=cli_command, verbose=verbose)

        # Priority 3: Auto-detect z-ai CLI
        z_ai = shutil.which("z-ai")
        if z_ai:
            if verbose:
                print(f"[llm_callback] Using z-ai CLI: {z_ai} (PAID)",
                      file=sys.stderr)
            return cls(cli_command=z_ai, verbose=verbose)

        # No provider available
        if verbose:
            print("[llm_callback] No LLM provider available",
                  file=sys.stderr)
        return cls(verbose=verbose)

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout_sec: int = 60,
    ) -> Optional[str]:
        """Call the LLM with system + user prompts.

        Routes to callback (FREE) or CLI (PAID) based on availability.

        Args:
            system_prompt: System-level instructions.
            user_prompt: User's query/message.
            timeout_sec: Timeout for CLI calls.

        Returns:
            LLM response text, or None on failure.
        """
        self._call_count += 1
        start_time = time.time()

        # Priority 1: Direct callback (FREE)
        if self._callback:
            try:
                result = self._callback(system_prompt, user_prompt)
                elapsed = time.time() - start_time
                self._free_calls += 1
                self._total_time += elapsed
                if self._verbose:
                    print(f"[llm_callback] FREE call #{self._call_count} "
                          f"({elapsed:.2f}s)", file=sys.stderr)
                return result
            except Exception as e:
                self._failed_calls += 1
                if self._verbose:
                    print(f"[llm_callback] Callback failed: {e}",
                          file=sys.stderr)
                return None

        # Priority 2: CLI command (PAID)
        if self._cli_command:
            result = self._call_cli(system_prompt, user_prompt, timeout_sec)
            elapsed = time.time() - start_time
            if result is not None:
                self._paid_calls += 1
            else:
                self._failed_calls += 1
            self._total_time += elapsed
            if self._verbose:
                cost = "PAID" if result else "FAILED"
                print(f"[llm_callback] {cost} call #{self._call_count} "
                      f"({elapsed:.2f}s)", file=sys.stderr)
            return result

        # No provider available
        self._failed_calls += 1
        if self._verbose:
            print(f"[llm_callback] No provider for call #{self._call_count}",
                  file=sys.stderr)
        return None

    def _call_cli(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout_sec: int,
    ) -> Optional[str]:
        """Call LLM via CLI subprocess (PAID path)."""
        try:
            cmd = [
                self._cli_command, "chat",
                "--prompt", user_prompt,
                "--system", system_prompt,
            ]
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_sec,
            )
            if r.returncode != 0:
                if self._verbose:
                    print(f"[llm_callback] CLI failed: {r.stderr[:200]}",
                          file=sys.stderr)
                return None

            # Try to parse OpenAI-style JSON envelope
            out = r.stdout
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
            if self._verbose:
                print(f"[llm_callback] CLI timed out ({timeout_sec}s)",
                      file=sys.stderr)
            return None
        except Exception as e:
            if self._verbose:
                print(f"[llm_callback] CLI error: {e}", file=sys.stderr)
            return None

    @property
    def is_free(self) -> bool:
        """Whether this provider uses free (callback) calls."""
        return self._callback is not None

    @property
    def name(self) -> str:
        """Provider name for logging."""
        if self._callback:
            return "callback (FREE)"
        if self._cli_command:
            return f"cli:{Path(self._cli_command).name} (PAID)"
        return "none"

    def stats(self) -> Dict[str, Any]:
        """Return call statistics."""
        return {
            "total_calls": self._call_count,
            "free_calls": self._free_calls,
            "paid_calls": self._paid_calls,
            "failed_calls": self._failed_calls,
            "total_time": round(self._total_time, 3),
            "avg_time": round(self._total_time / max(self._call_count, 1), 3),
            "provider": self.name,
            "money_saved_estimate": self._free_calls,  # Each free call saves ~$0.01-0.05
        }


# ═══════════════════════════════════════════════════════════════════════════
# POLER + LLM Combined Executor
# ═══════════════════════════════════════════════════════════════════════════

def execute_poler(
    text: str,
    query: str = "",
    llm_callback: Optional[Callable] = None,
    use_llm_synthesis: bool = True,
    verbose: bool = False,
) -> str:
    """Execute POLER analysis, optionally with LLM synthesis.

    This is the FAST PATH for POLER-based skills:
    1. Run POLER locally (FREE, no LLM)
    2. If use_llm_synthesis=True, add a thin LLM layer to interpret results
    3. Return the combined result

    Without LLM synthesis: pure POLER output (data, metrics, patterns)
    With LLM synthesis: POLER data + natural language interpretation

    Args:
        text: Text to analyze.
        query: Optional focus query.
        llm_callback: Optional LLM callback for synthesis.
        use_llm_synthesis: Whether to add LLM interpretation.
        verbose: Print debug information.

    Returns:
        Analysis result (JSON string or text).
    """
    # Step 1: Run POLER locally (FREE)
    try:
        from super_z_bridge import AIBridge
        bridge = AIBridge(verbose=verbose)
        poler_result = bridge.execute_poler(text, query)
    except ImportError:
        # Bridge not available, try direct POLER import
        try:
            sandbox_dir = Path(__file__).resolve().parent
            if str(sandbox_dir) not in sys.path:
                sys.path.insert(0, str(sandbox_dir))
            from poler_enhanced import PolerAnalyzer
            analyzer = PolerAnalyzer()
            raw = analyzer.analyze_text(text, query)
            poler_result = json.dumps(raw, ensure_ascii=False, indent=2)
        except ImportError:
            poler_result = json.dumps({
                "status": "fallback",
                "message": "POLER not available",
                "text_length": len(text),
                "query": query[:200],
            }, ensure_ascii=False, indent=2)

    # Step 2: Optional LLM synthesis
    if use_llm_synthesis and llm_callback:
        try:
            provider = LLMCallbackProvider(
                callback=llm_callback,
                verbose=verbose,
            )
            synthesis_prompt = (
                "You are a text analysis assistant. You have received POLER "
                "resonance analysis data below. Provide a clear, concise "
                "interpretation of the key findings. Focus on the user's "
                "query if provided.\n\n"
                f"User query: {query or 'General analysis'}\n\n"
                f"POLER analysis data:\n{poler_result[:3000]}"
            )
            synthesis = provider.chat(
                system_prompt="You interpret text analysis results clearly and concisely.",
                user_prompt=synthesis_prompt,
            )
            if synthesis:
                # Combine POLER data with LLM interpretation
                combined = {
                    "poler_analysis": json.loads(poler_result) if poler_result.startswith("{") else poler_result,
                    "llm_interpretation": synthesis,
                    "method": "poler+llm_callback",
                    "cost": "free",
                }
                return json.dumps(combined, ensure_ascii=False, indent=2)
        except Exception as e:
            if verbose:
                print(f"[llm_callback] LLM synthesis failed: {e}",
                      file=sys.stderr)

    # Return POLER result without LLM synthesis
    return poler_result


# ═══════════════════════════════════════════════════════════════════════════
# Convenience Functions
# ═══════════════════════════════════════════════════════════════════════════

# Module-level singleton
_callback_provider: Optional[LLMCallbackProvider] = None


def get_callback_provider(
    callback: Optional[Callable] = None,
    verbose: bool = False,
) -> LLMCallbackProvider:
    """Get or create the callback provider singleton.

    Args:
        callback: Python callable for LLM interaction.
        verbose: Print debug info.

    Returns:
        LLMCallbackProvider instance.
    """
    global _callback_provider
    if _callback_provider is None or callback is not None:
        if callback:
            _callback_provider = LLMCallbackProvider(
                callback=callback,
                verbose=verbose,
            )
        else:
            _callback_provider = LLMCallbackProvider.from_env(verbose=verbose)
    return _callback_provider


def execute_with_callback(
    skill_name: str,
    system_prompt: str,
    user_prompt: str,
    llm_callback: Optional[Callable] = None,
    timeout_sec: int = 60,
    verbose: bool = False,
) -> Optional[str]:
    """Execute a skill using the LLM callback (convenience function).

    This is the main entry point for AI_REASONING skills. It:
    1. Gets or creates a callback provider
    2. Calls the LLM with the skill's system prompt + user query
    3. Returns the result

    Args:
        skill_name: Name of the skill (for context/logging).
        system_prompt: Skill-specific system instructions.
        user_prompt: User's query.
        llm_callback: Optional callback override.
        timeout_sec: Timeout for CLI calls.
        verbose: Print debug info.

    Returns:
        LLM response text, or None on failure.
    """
    provider = get_callback_provider(callback=llm_callback, verbose=verbose)

    if verbose:
        print(f"[llm_callback] Executing '{skill_name}' via {provider.name}",
              file=sys.stderr)

    return provider.chat(system_prompt, user_prompt, timeout_sec)


def create_llm_callback_for_core(
    callback: Optional[Callable] = None,
    verbose: bool = False,
) -> Optional[Callable[[str, str], Optional[str]]]:
    """Create an llm_callback function suitable for SuperZCore.

    This wraps the LLMCallbackProvider in a simple callable interface
    that SuperZCore.run_skill() expects.

    Args:
        callback: Python callable for LLM interaction.
        verbose: Print debug info.

    Returns:
        Callable(system_prompt, user_prompt) -> response, or None.
    """
    provider = get_callback_provider(callback=callback, verbose=verbose)

    if provider.is_free or provider._cli_command:
        return provider.chat

    return None


# ═══════════════════════════════════════════════════════════════════════════
# CLI — for testing
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Super Z LLM Callback — Free AI Reasoning Provider"
    )
    parser.add_argument(
        "command",
        choices=["test", "poler", "stats"],
        help="Command to execute",
    )
    parser.add_argument("--text", default="", help="Text for POLER analysis")
    parser.add_argument("--query", default="", help="Query/prompt")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.command == "test":
        # Test the callback provider
        provider = LLMCallbackProvider.from_env(verbose=args.verbose)
        print(f"Provider: {provider.name}")
        print(f"Is free: {provider.is_free}")
        result = provider.chat(
            "You are a helpful assistant.",
            "Say 'Hello from Super Z LLM Callback!'",
        )
        if result:
            print(f"Result: {result}")
        else:
            print("(no result — no LLM provider available)")

    elif args.command == "poler":
        if not args.text:
            args.text = "This is a test text for POLER analysis with LLM synthesis. " * 5
        result = execute_poler(
            args.text, args.query,
            use_llm_synthesis=False,  # No LLM in standalone mode
            verbose=args.verbose,
        )
        print(result)

    elif args.command == "stats":
        provider = LLMCallbackProvider.from_env(verbose=args.verbose)
        print(json.dumps(provider.stats(), indent=2))
