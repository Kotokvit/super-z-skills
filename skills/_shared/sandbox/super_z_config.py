#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
super_z_config.py — Configuration & Environment Auto-Detection
==============================================================

This module answers ONE question: "Where am I running?"

The answer determines the entire routing strategy:

    LLM_NATIVE  → Inside an AI (GLM, Claude, GPT, Qwen, etc.)
                   Has: built-in reasoning, file system, terminal
                   Strategy: Use AI's own callback (FREE)

    LOCAL_CLI   → Standalone terminal with z-ai CLI available
                   Has: z-ai CLI binary
                   Strategy: Use CLI (PAID, but only when needed)

    HYBRID      → Inside an AI AND has z-ai CLI
                   Has: Both callback AND CLI
                   Strategy: Callback for reasoning, CLI for external APIs

    STANDALONE  → No AI, no CLI
                   Has: Only Python/Bash
                   Strategy: Local skills only (FREE, but limited)

DETECTION PRIORITY (what makes an environment "LLM_NATIVE"):
    1. SUPER_Z_HOST_LLM_CALLBACK env var → someone passed us a Python callback
    2. llm_callback parameter → the caller explicitly gave us a function
    3. Heuristic: running inside known AI containers, specific env vars

This module is the FOUNDATION of Local-First routing. Every other module
(super_z_core, super_z_bridge, super_z_llm_callback) depends on it.

Author: Super-Z team + Qwen Coder, 2026-07-21
Version: 1.0.0
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


__version__ = "1.0.0"
__author__ = "Super-Z team + Qwen Coder"


# ═══════════════════════════════════════════════════════════════════════════
# Environment Types
# ═══════════════════════════════════════════════════════════════════════════

class EnvironmentType(Enum):
    """Runtime environment types.

    LLM_NATIVE — We're INSIDE an AI. It has its own reasoning engine,
                 file access, and terminal. No external call needed.

    LOCAL_CLI  — We're in a terminal with z-ai (or similar) CLI available.
                 External calls are PAID.

    HYBRID     — Inside an AI AND has CLI. Best of both worlds:
                 free reasoning + paid external APIs when needed.

    STANDALONE — No AI, no CLI. Only Python/Bash operations available.
                 Local skills only.
    """
    LLM_NATIVE = "llm_native"    # Inside AI (GLM, Claude, GPT, Qwen)
    LOCAL_CLI = "local_cli"      # Terminal with z-ai CLI
    HYBRID = "hybrid"            # Inside AI + CLI available
    STANDALONE = "standalone"    # Python only, no AI, no CLI


@dataclass
class EnvironmentInfo:
    """Complete environment detection result.

    This is the "passport" that every other module uses to decide
    how to route tasks.
    """
    type: EnvironmentType = EnvironmentType.STANDALONE
    has_callback: bool = False        # LLM callback available?
    has_zai_cli: bool = False         # z-ai CLI binary found?
    has_other_cli: bool = False       # Other CLI (claude, gpt) found?
    cli_path: str = ""                # Path to available CLI
    provider_name: str = "none"       # Name of the detected provider
    hostname: str = ""                # Machine hostname
    is_container: bool = False        # Running in Docker/container?
    is_ai_platform: bool = False      # Confirmed AI platform?
    ai_platform_name: str = ""        # "glm", "claude", "gpt", "qwen", etc.
    callback_source: str = ""         # "parameter", "env_var", "auto_detect"
    python_version: str = ""          # Python version string
    working_dir: str = ""             # Current working directory

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/serialization."""
        result = asdict(self)
        result["type"] = self.type.value
        return result

    @property
    def can_reason_for_free(self) -> bool:
        """Can we do LLM reasoning without paying?"""
        return self.has_callback or self.type in (
            EnvironmentType.LLM_NATIVE,
            EnvironmentType.HYBRID,
        )

    @property
    def can_call_external_apis(self) -> bool:
        """Can we reach external APIs (web-search, TTS, etc.)?"""
        return self.has_zai_cli or self.has_other_cli

    @property
    def is_offline(self) -> bool:
        """Are we in pure offline mode?"""
        return self.type == EnvironmentType.STANDALONE


# ═══════════════════════════════════════════════════════════════════════════
# AI Platform Detection Heuristics
# ═══════════════════════════════════════════════════════════════════════════

# Known environment variables that indicate we're inside an AI platform
AI_PLATFORM_ENV_SIGNATURES: Dict[str, str] = {
    # OpenAI / ChatGPT
    "OPENAI_API_KEY": "gpt",
    # Anthropic / Claude
    "ANTHROPIC_API_KEY": "claude",
    # GLM / Zhipu
    "ZHIPU_API_KEY": "glm",
    "GLM_API_KEY": "glm",
    # Qwen / Alibaba
    "DASHSCOPE_API_KEY": "qwen",
    # Google / Gemini
    "GOOGLE_API_KEY": "gemini",
    "GEMINI_API_KEY": "gemini",
    # Super Z specific
    "SUPER_Z_HOST_LLM_CALLBACK": "super_z",
    # Generic AI platform markers
    "AI_PLATFORM": "generic",
    "LLM_CALLBACK": "generic",
}

# Known container/hostname patterns for AI platforms
AI_HOSTNAME_PATTERNS: List[str] = [
    "glm-", "claude-", "gpt-", "qwen-", "gemini-",
    "ai-", "llm-", "sandbox-", "space-z",
]


def _detect_ai_platform_from_env() -> Optional[str]:
    """Try to identify which AI platform we're running inside.

    Returns:
        Platform name string (e.g., "glm", "claude"), or None.
    """
    for env_var, platform_name in AI_PLATFORM_ENV_SIGNATURES.items():
        if os.environ.get(env_var, "").strip():
            return platform_name
    return None


def _detect_ai_platform_from_hostname() -> Optional[str]:
    """Try to identify AI platform from hostname patterns.

    Returns:
        Platform name string, or None.
    """
    try:
        hostname = socket.gethostname().lower()
        for pattern in AI_HOSTNAME_PATTERNS:
            if pattern in hostname:
                return pattern.rstrip("-")
    except Exception:
        pass
    return None


def _detect_container() -> bool:
    """Detect if running inside a container (Docker, K8s, etc.)."""
    return (
        Path("/.dockerenv").exists() or
        Path("/run/.containerenv").exists() or
        os.environ.get("container", "") != "" or
        os.environ.get("KUBERNETES_SERVICE_HOST", "") != ""
    )


def _detect_zai_cli() -> Optional[str]:
    """Find z-ai CLI binary path.

    Returns:
        Path to z-ai binary, or None.
    """
    return shutil.which("z-ai")


def _detect_other_cli() -> Optional[str]:
    """Find alternative AI CLI tools.

    Returns:
        Path to first found CLI, or None.
    """
    for cli_name in ["claude", "gpt", "ai", "ollama"]:
        path = shutil.which(cli_name)
        if path:
            return path
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Main Detection Function
# ═══════════════════════════════════════════════════════════════════════════

def detect_environment(
    llm_callback: Optional[Callable] = None,
    verbose: bool = False,
) -> EnvironmentInfo:
    """Auto-detect the runtime environment.

    This is the CORE detection function. It determines:
    1. Are we inside an AI? (callback available → FREE reasoning)
    2. Do we have CLI access? (external APIs available → PAID)
    3. Both? (HYBRID → best of both worlds)
    4. Neither? (STANDALONE → local skills only)

    Detection Priority:
        1. Explicit llm_callback parameter → LLM_NATIVE or HYBRID
        2. SUPER_Z_HOST_LLM_CALLBACK env var → LLM_NATIVE or HYBRID
        3. AI platform env signatures → LLM_NATIVE (heuristic)
        4. z-ai CLI available → LOCAL_CLI
        5. Other CLI available → LOCAL_CLI
        6. Fallback → STANDALONE

    Args:
        llm_callback: Python callable(system_prompt, user_prompt) -> response.
                      When provided, we know we're inside an AI.
        verbose: Print detection steps to stderr.

    Returns:
        EnvironmentInfo with complete environment details.
    """
    info = EnvironmentInfo()

    # Basic system info
    try:
        info.hostname = socket.gethostname()
    except Exception:
        info.hostname = "unknown"

    info.is_container = _detect_container()
    info.python_version = sys.version.split()[0] if sys.version else "unknown"
    info.working_dir = os.getcwd()

    # ── Step 1: Check for explicit callback (definitive proof of AI platform) ──
    has_explicit_callback = llm_callback is not None

    # ── Step 2: Check for callback from environment variable ──
    callback_from_env = False
    callback_path = os.environ.get("SUPER_Z_HOST_LLM_CALLBACK", "")
    if callback_path:
        try:
            module_path, func_name = callback_path.rsplit(".", 1)
            import importlib
            module = importlib.import_module(module_path)
            _cb = getattr(module, func_name)
            callback_from_env = True
            info.callback_source = "env_var"
            if verbose:
                print(f"[config] Found callback from env: {callback_path}",
                      file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[config] Failed to load callback from env: {e}",
                      file=sys.stderr)
            callback_from_env = False

    # ── Step 3: Heuristic AI platform detection ──
    ai_platform = _detect_ai_platform_from_env()
    if ai_platform is None:
        ai_platform = _detect_ai_platform_from_hostname()

    # ── Step 4: CLI detection ──
    zai_cli_path = _detect_zai_cli()
    other_cli_path = _detect_other_cli()

    info.has_zai_cli = zai_cli_path is not None
    info.has_other_cli = other_cli_path is not None
    info.cli_path = zai_cli_path or other_cli_path or ""

    # ── Step 5: Determine environment type ──
    has_any_callback = has_explicit_callback or callback_from_env

    if has_any_callback:
        info.has_callback = True
        info.is_ai_platform = True
        info.ai_platform_name = ai_platform or "unknown"

        if has_explicit_callback:
            info.callback_source = info.callback_source or "parameter"
        elif callback_from_env:
            info.callback_source = "env_var"

        # If we also have CLI → HYBRID (best of both worlds)
        if info.has_zai_cli or info.has_other_cli:
            info.type = EnvironmentType.HYBRID
            info.provider_name = f"callback+{Path(info.cli_path).name}" if info.cli_path else "callback"
        else:
            info.type = EnvironmentType.LLM_NATIVE
            info.provider_name = "host_callback"

        if verbose:
            print(f"[config] Detected: {info.type.value}, "
                  f"callback={info.has_callback}, "
                  f"cli={info.cli_path or 'none'}, "
                  f"platform={info.ai_platform_name}",
                  file=sys.stderr)

        return info

    # ── Step 6: AI platform heuristic (no callback, but signatures found) ──
    if ai_platform:
        info.is_ai_platform = True
        info.ai_platform_name = ai_platform
        info.has_callback = False  # No explicit callback, but likely AI

        if info.has_zai_cli or info.has_other_cli:
            info.type = EnvironmentType.HYBRID
            info.provider_name = f"heuristic:{ai_platform}+{Path(info.cli_path).name}"
        else:
            info.type = EnvironmentType.LLM_NATIVE
            info.provider_name = f"heuristic:{ai_platform}"

        if verbose:
            print(f"[config] Heuristic AI detection: {ai_platform}, "
                  f"type={info.type.value}",
                  file=sys.stderr)

        return info

    # ── Step 7: CLI only (no AI) ──
    if info.has_zai_cli:
        info.type = EnvironmentType.LOCAL_CLI
        info.provider_name = "z-ai-cli"
        if verbose:
            print(f"[config] Detected: LOCAL_CLI, z-ai at {zai_cli_path}",
                  file=sys.stderr)
        return info

    if info.has_other_cli:
        info.type = EnvironmentType.LOCAL_CLI
        info.provider_name = f"{Path(other_cli_path).name}-cli"
        if verbose:
            print(f"[config] Detected: LOCAL_CLI, {other_cli_path}",
                  file=sys.stderr)
        return info

    # ── Step 8: Fallback — STANDALONE ──
    info.type = EnvironmentType.STANDALONE
    info.provider_name = "standalone"
    if verbose:
        print("[config] Detected: STANDALONE (no AI, no CLI, local only)",
              file=sys.stderr)
    return info


# ═══════════════════════════════════════════════════════════════════════════
# Skill Classification (moved from super_z_core for modularity)
# ═══════════════════════════════════════════════════════════════════════════

class SkillCategory(Enum):
    """Classification of skill resource requirements.

    LOCAL_EXEC    — Pure Python/Bash, no LLM needed. FREE.
    AI_REASONING  — Needs LLM reasoning. FREE if callback available, PAID otherwise.
    EXTERNAL_API  — Needs external API. Always PAID (no local alternative).
    """
    LOCAL_EXEC = "local_exec"        # poler-psi, doc-triage, etc.
    AI_REASONING = "ai_reasoning"    # blog-writer, analysis, etc.
    EXTERNAL_API = "external_api"    # web-search, TTS, image-gen, etc.


# Skills that can be executed with Python/Bash alone — FREE
LOCAL_SKILLS: set = {
    "poler-psi",            # POLER text analysis engine
    "poler-toolkit",        # POLER toolkit utilities
    "doc-triage",           # Document classification (rule-based)
    "cheat-sheet",          # Template generation (structured output)
    "version-management",   # Version tracking (file ops)
    "pdf-ocr",              # OCR extraction (local tool)
    "file-ops",             # File operations (read/write/transform)
    "text-analysis",        # Text analysis (regex, counters)
    "data-transform",       # Data transformation (JSON, CSV)
}

# Skills that NEED external APIs — always require CLI/network
EXTERNAL_SKILLS: set = {
    "web-search",           # Internet search
    "image-generation",     # AI image generation
    "image-search",         # Image search API
    "image-edit",           # AI image editing
    "ASR",                  # Speech-to-text
    "TTS",                  # Text-to-speech
    "VLM",                  # Vision-language model
    "video-understand",     # Video analysis
    "video-generation",     # Video generation
    "agent-browser",        # Browser automation (network)
    "web-reader",           # Web page extraction
    "LLM",                  # Direct LLM chat (external)
}


def classify_skill(skill_name: str) -> SkillCategory:
    """Classify a skill by its resource requirements.

    Args:
        skill_name: Name of the skill (e.g., "poler-psi", "blog-writer").

    Returns:
        SkillCategory indicating what resources the skill needs.
    """
    if skill_name in LOCAL_SKILLS:
        return SkillCategory.LOCAL_EXEC
    if skill_name in EXTERNAL_SKILLS:
        return SkillCategory.EXTERNAL_API
    return SkillCategory.AI_REASONING


# ═══════════════════════════════════════════════════════════════════════════
# Routing Decision — the output that super_z_core uses
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RoutingDecision:
    """Complete routing decision for a skill execution.

    This is the "flight plan" that tells super_z_core exactly how
    to execute a skill.
    """
    skill_name: str
    category: SkillCategory
    backend: str              # "local", "callback", "cli", "none"
    cost: str                 # "free", "free_callback", "paid", "unavailable"
    reason: str               # Human-readable explanation
    env_type: str             # Environment type string
    can_execute: bool = True  # Is execution possible?
    fallback: str = ""        # What to try if primary fails
    estimated_time: str = ""  # Rough time estimate

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["category"] = self.category.value
        return result


def get_routing_decision(
    skill_name: str,
    env_info: Optional[EnvironmentInfo] = None,
    llm_callback: Optional[Callable] = None,
    verbose: bool = False,
) -> RoutingDecision:
    """Determine the optimal routing for a skill.

    This replaces the old "always call z-ai CLI" approach with
    intelligent routing based on environment and skill type.

    Args:
        skill_name: Name of the skill to route.
        env_info: Pre-detected environment info (or None to auto-detect).
        llm_callback: Optional callback for AI reasoning.
        verbose: Print routing decision details.

    Returns:
        RoutingDecision with complete execution plan.
    """
    if env_info is None:
        env_info = detect_environment(llm_callback=llm_callback, verbose=verbose)

    category = classify_skill(skill_name)

    # Helper: support both config EnvironmentInfo (has can_reason_for_free)
    # and core EnvironmentInfo (has has_callback) - compatibility layer
    _can_reason_free = getattr(env_info, 'can_reason_for_free', None)
    if _can_reason_free is None:
        _can_reason_free = env_info.has_callback or env_info.type in (
            EnvironmentType.LLM_NATIVE, EnvironmentType.HYBRID,
        )
    _can_call_apis = getattr(env_info, 'can_call_external_apis', None)
    if _can_call_apis is None:
        _can_call_apis = env_info.has_zai_cli or env_info.has_other_cli

    # LOCAL skills → always free, always possible
    if category == SkillCategory.LOCAL_EXEC:
        return RoutingDecision(
            skill_name=skill_name,
            category=category,
            backend="local",
            cost="free",
            reason=f"Skill '{skill_name}' executes locally with Python/Bash (FREE)",
            env_type=env_info.type.value,
            can_execute=True,
            fallback="",
            estimated_time="<1s",
        )

    # AI_REASONING skills → callback if available, CLI otherwise
    if category == SkillCategory.AI_REASONING:
        # Support both config EnvironmentInfo (has can_reason_for_free)
        # and core EnvironmentInfo (has has_callback) via getattr
        _can_reason_free = getattr(env_info, 'can_reason_for_free', None)
        if _can_reason_free is None:
            # Fallback for core EnvironmentInfo which lacks this property
            _can_reason_free = env_info.has_callback or env_info.type in (
                EnvironmentType.LLM_NATIVE, EnvironmentType.HYBRID,
            )
        if _can_reason_free:
            return RoutingDecision(
                skill_name=skill_name,
                category=category,
                backend="callback",
                cost="free_callback",
                reason=(
                    f"Skill '{skill_name}' needs LLM reasoning. "
                    f"Using host AI callback (FREE, in-process). "
                    f"Platform: {env_info.ai_platform_name or 'detected'}"
                ),
                env_type=env_info.type.value,
                can_execute=True,
                fallback="cli" if env_info.can_call_external_apis else "",
                estimated_time="1-5s",
            )
        elif _can_call_apis:
            return RoutingDecision(
                skill_name=skill_name,
                category=category,
                backend="cli",
                cost="paid",
                reason=(
                    f"Skill '{skill_name}' needs LLM reasoning. "
                    f"No callback available, using CLI (PAID). "
                    f"Set SUPER_Z_HOST_LLM_CALLBACK for free execution."
                ),
                env_type=env_info.type.value,
                can_execute=True,
                fallback="",
                estimated_time="5-30s",
            )
        else:
            return RoutingDecision(
                skill_name=skill_name,
                category=category,
                backend="none",
                cost="unavailable",
                reason=(
                    f"Skill '{skill_name}' needs LLM reasoning but no provider "
                    f"is available. Install z-ai CLI or set "
                    f"SUPER_Z_HOST_LLM_CALLBACK."
                ),
                env_type=env_info.type.value,
                can_execute=False,
                fallback="",
                estimated_time="N/A",
            )

    # EXTERNAL_API skills → need CLI, always paid
    if category == SkillCategory.EXTERNAL_API:
        if _can_call_apis:
            return RoutingDecision(
                skill_name=skill_name,
                category=category,
                backend="cli",
                cost="paid",
                reason=(
                    f"Skill '{skill_name}' requires external API. "
                    f"CLI is the only option (PAID)."
                ),
                env_type=env_info.type.value,
                can_execute=True,
                fallback="",
                estimated_time="5-60s",
            )
        else:
            return RoutingDecision(
                skill_name=skill_name,
                category=category,
                backend="none",
                cost="unavailable",
                reason=(
                    f"Skill '{skill_name}' requires external API but no CLI "
                    f"is available. Install z-ai CLI."
                ),
                env_type=env_info.type.value,
                can_execute=False,
                fallback="",
                estimated_time="N/A",
            )

    # Unknown category (should not happen)
    return RoutingDecision(
        skill_name=skill_name,
        category=SkillCategory.AI_REASONING,
        backend="none",
        cost="unavailable",
        reason=f"Cannot determine routing for skill '{skill_name}'",
        env_type=env_info.type.value,
        can_execute=False,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CLI — for testing and debugging
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Super Z Config — Environment Detection & Routing"
    )
    parser.add_argument(
        "command",
        choices=["env", "route", "skills"],
        help="Command to execute",
    )
    parser.add_argument("--skill", default="blog-writer", help="Skill name")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.command == "env":
        env = detect_environment(verbose=args.verbose)
        print(json.dumps(env.to_dict(), ensure_ascii=False, indent=2))

    elif args.command == "route":
        decision = get_routing_decision(args.skill, verbose=args.verbose)
        print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))

    elif args.command == "skills":
        print("LOCAL_SKILLS (FREE, no LLM needed):")
        for s in sorted(LOCAL_SKILLS):
            print(f"  - {s}")
        print("\nAI_REASONING (FREE with callback, PAID with CLI):")
        print("  (all skills not in LOCAL or EXTERNAL)")
        print("\nEXTERNAL_SKILLS (always PAID, need CLI):")
        for s in sorted(EXTERNAL_SKILLS):
            print(f"  - {s}")
