#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
super_z_core.py — Super Z Core: Local-First Routing Engine
=========================================================

THE PROBLEM IT SOLVES:
    When an AI (GLM, Claude, GPT) runs inside a platform, it ALREADY has:
    - File system access (Read, Write)
    - Terminal access (Bash)
    - Built-in reasoning (it IS an LLM)

    But the old architecture always called external `z-ai CLI` as the default
    LLM provider — even from inside an AI that could do the work itself.
    This is like hiring a courier to deliver a message to your neighbor
    when you're already standing at their door.

THE SOLUTION — "Local First" Routing:
    1. Check: Can this task be done locally? (Python/Bash, no LLM needed)
       → YES → Execute directly. Free. Instant.
    2. Check: Are we running inside an AI platform? (callback available)
       → YES → Use the AI's own LLM callback. Same quality, zero extra cost.
    3. Check: Is an external CLI available? (z-ai, claude, gpt)
       → YES → Use it. Paid, but only when truly necessary.
    4. Fallback: Return None. Task cannot be completed.

CLASSIFICATION OF SKILLS:
    LOCAL_SKILLS   — Can be done with Python/Bash alone (file ops, text
                     processing, analysis, parsing, data transformation)
    AI_SKILLS      — Need LLM reasoning (writing, planning, reviewing)
    EXTERNAL_SKILLS — Need external APIs (web-search, image-generation,
                      TTS, ASR, VLM)

ENVIRONMENT DETECTION:
    The module auto-detects its runtime environment:
    - "ai_platform" → Running inside an AI (callback available)
    - "local_cli"   → Running standalone with z-ai CLI
    - "standalone"  → No AI, no CLI (local skills only)

USAGE:
    from super_z_core import SuperZCore

    core = SuperZCore()

    # Auto-detect environment, run with optimal routing
    result = core.run_skill(
        skill_name="blog-writer",
        user_query="Write about AI",
        skill_md=skill_md_text,
    )

    # Or with explicit callback (from AI platform)
    core = SuperZCore(llm_callback=my_llm_function)
    result = core.run_skill(...)

    # Check what environment was detected
    env = core.detect_environment()
    print(env)  # {"type": "ai_platform", "provider": "host_callback", ...}

Author: Super-Z team, 2026-07-21
Version: 1.0.0
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


__version__ = "1.0.0"
__author__ = "Super-Z team"


# ═══════════════════════════════════════════════════════════════════════════
# Skill Classification — which skills need what?
# ═══════════════════════════════════════════════════════════════════════════

class SkillCategory(Enum):
    """Classification of skill resource requirements."""
    LOCAL = "local"          # Pure Python/Bash, no LLM needed
    AI_REASONING = "ai"      # Needs LLM reasoning (writing, analysis)
    EXTERNAL_API = "external"  # Needs external API (web-search, image-gen, TTS)


# Skills that can be executed with Python/Bash alone — FREE
LOCAL_SKILLS: set = {
    "poler-psi",        # POLER text analysis engine
    "poler-toolkit",    # POLER toolkit utilities
    "doc-triage",       # Document classification (rule-based)
    "cheat-sheet",      # Template generation (structured output)
    "version-management",  # Version tracking (file ops)
    "pdf-ocr",          # OCR extraction (local tool)
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
    "LLM",                  # Direct LLM chat
}

# Everything else = AI_REASONING (needs LLM but not external API)
# This includes: blog-writer, contentanalysis, finance, design, etc.


def classify_skill(skill_name: str) -> SkillCategory:
    """Classify a skill by its resource requirements.

    Args:
        skill_name: Name of the skill.

    Returns:
        SkillCategory indicating what resources the skill needs.
    """
    if skill_name in LOCAL_SKILLS:
        return SkillCategory.LOCAL
    if skill_name in EXTERNAL_SKILLS:
        return SkillCategory.EXTERNAL_API
    return SkillCategory.AI_REASONING


# ═══════════════════════════════════════════════════════════════════════════
# Environment Detection — where are we running?
# ═══════════════════════════════════════════════════════════════════════════

class EnvironmentType(Enum):
    """Runtime environment types."""
    AI_PLATFORM = "ai_platform"    # Inside an AI (GLM, Claude, GPT)
    LOCAL_CLI = "local_cli"        # Standalone with z-ai CLI available
    STANDALONE = "standalone"      # No AI, no CLI (local skills only)


@dataclass
class EnvironmentInfo:
    """Detected environment details."""
    type: EnvironmentType = EnvironmentType.STANDALONE
    has_callback: bool = False       # LLM callback available?
    has_zai_cli: bool = False        # z-ai CLI binary found?
    has_other_cli: bool = False      # Other CLI (claude, gpt) found?
    cli_path: str = ""               # Path to available CLI
    provider_name: str = "none"      # Name of the detected provider
    hostname: str = ""               # Machine hostname
    is_container: bool = False       # Running in Docker/container?

    def to_dict(self) -> Dict:
        return asdict(self)


def detect_environment(
    llm_callback: Optional[Callable] = None,
) -> EnvironmentInfo:
    """Auto-detect the runtime environment.

    Priority:
        1. If an LLM callback is provided → AI_PLATFORM
        2. If SUPER_Z_HOST_LLM_CALLBACK env → AI_PLATFORM
        3. If z-ai CLI exists → LOCAL_CLI
        4. Otherwise → STANDALONE

    Args:
        llm_callback: Optional Python callable for LLM interaction.

    Returns:
        EnvironmentInfo with detected details.
    """
    import socket

    info = EnvironmentInfo()
    info.hostname = socket.gethostname() if hasattr(socket, 'gethostname') else "unknown"

    # Check if running in a container
    info.is_container = (
        Path("/.dockerenv").exists() or
        Path("/run/.containerenv").exists() or
        os.environ.get("container", "") != "" or
        os.environ.get("KUBERNETES_SERVICE_HOST", "") != ""
    )

    # Priority 1: Explicit callback (AI platform mode)
    if llm_callback is not None:
        info.type = EnvironmentType.AI_PLATFORM
        info.has_callback = True
        info.provider_name = "host_callback"
        return info

    # Priority 2: Callback from environment variable
    callback_path = os.environ.get("SUPER_Z_HOST_LLM_CALLBACK", "")
    if callback_path:
        try:
            module_path, func_name = callback_path.rsplit(".", 1)
            import importlib
            module = importlib.import_module(module_path)
            callback = getattr(module, func_name)
            info.type = EnvironmentType.AI_PLATFORM
            info.has_callback = True
            info.provider_name = f"callback:{callback_path}"
            return info
        except Exception:
            pass  # Failed to load, continue checking

    # Priority 3: Check for z-ai CLI
    z_ai_path = shutil.which("z-ai")
    if z_ai_path:
        info.type = EnvironmentType.LOCAL_CLI
        info.has_zai_cli = True
        info.cli_path = z_ai_path
        info.provider_name = "z-ai-cli"
        return info

    # Priority 4: Check for other CLIs
    for cli_name in ["claude", "gpt", "ai"]:
        cli_path = shutil.which(cli_name)
        if cli_path:
            info.type = EnvironmentType.LOCAL_CLI
            info.has_other_cli = True
            info.cli_path = cli_path
            info.provider_name = f"{cli_name}-cli"
            return info

    # Fallback: standalone
    info.type = EnvironmentType.STANDALONE
    info.provider_name = "standalone"
    return info


# ═══════════════════════════════════════════════════════════════════════════
# Local Executor — runs Python/Bash tasks without any LLM
# ═══════════════════════════════════════════════════════════════════════════

class LocalExecutor:
    """Execute tasks that don't need an LLM — pure Python/Bash.

    This is the "free tier" of Super Z. No API calls, no tokens,
    no latency beyond computation time.

    Supported operations:
    - File read/write/transform
    - Text analysis (regex, counters, parsers)
    - Data processing (JSON, CSV, etc.)
    - POLER text resonance analysis
    - Document diffing
    - Script execution (subprocess)
    """

    def can_handle(self, skill_name: str, query: str) -> bool:
        """Check if this skill+query can be handled locally.

        Args:
            skill_name: Name of the skill.
            query: User's query text.

        Returns:
            True if the task can be done without LLM.
        """
        category = classify_skill(skill_name)
        if category == SkillCategory.LOCAL:
            return True

        # Even AI_REASONING skills can sometimes be handled locally
        # if the query is simple enough (e.g., "list files", "count words")
        local_patterns = [
            r'\b(count|list|show|display|extract|parse|convert|diff|compare)\b',
            r'\b(сколько|перечисл|покаж|извлек|парс|конверт|сравн)\b',
        ]
        import re
        for pattern in local_patterns:
            if re.search(pattern, query.lower()):
                return True

        return False

    def execute(
        self,
        skill_name: str,
        query: str,
        skill_md: str = "",
        skill_dir: Optional[Path] = None,
        timeout_sec: int = 60,
    ) -> Optional[str]:
        """Execute a local task.

        Args:
            skill_name: Name of the skill.
            query: User's query text.
            skill_md: Skill methodology document.
            skill_dir: Path to the skill directory.
            timeout_sec: Timeout for subprocess calls.

        Returns:
            Result text, or None if the task cannot be done locally.
        """
        # Try to find and run the skill's own script
        if skill_dir:
            run_py = skill_dir / "scripts" / "run.py"
            if run_py.exists():
                try:
                    cmd = [sys.executable, str(run_py), query]
                    r = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=timeout_sec,
                        cwd=str(skill_dir),
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        return r.stdout.strip()
                except (subprocess.TimeoutExpired, Exception):
                    pass

        # POLER analysis (built-in, no external deps)
        if skill_name in ("poler-psi", "poler-toolkit"):
            return self._poler_analysis(query, skill_md)

        # Generic text operations
        return self._generic_local(skill_name, query, skill_md)

    def _poler_analysis(self, query: str, skill_md: str) -> str:
        """Run POLER text analysis locally."""
        try:
            from poler_enhanced import PolerAnalyzer
            analyzer = PolerAnalyzer()
            result = analyzer.analyze_text(skill_md or query, query)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except ImportError:
            # POLER not available, basic analysis
            return json.dumps({
                "status": "local_fallback",
                "message": "POLER engine not available, using basic text analysis",
                "text_length": len(skill_md or query),
                "query": query[:200],
            }, ensure_ascii=False, indent=2)

    def _generic_local(self, skill_name: str, query: str,
                       skill_md: str) -> Optional[str]:
        """Handle generic local tasks."""
        # If we have skill_md and a simple query, return relevant sections
        if skill_md and len(query) < 100:
            import re
            # Find sections matching query keywords
            keywords = re.findall(r'\w+', query.lower())
            sections = skill_md.split('\n\n')
            relevant = []
            for section in sections:
                section_lower = section.lower()
                if any(kw in section_lower for kw in keywords):
                    relevant.append(section.strip())

            if relevant:
                return "\n\n".join(relevant[:5])

        # Cannot handle locally
        return None


# ═══════════════════════════════════════════════════════════════════════════
# LLM Provider Factory — creates the RIGHT provider for the environment
# ═══════════════════════════════════════════════════════════════════════════

def create_llm_provider(
    env_info: Optional[EnvironmentInfo] = None,
    llm_callback: Optional[Callable] = None,
    cli_command: Optional[str] = None,
) -> Optional[Any]:
    """Create an LLM provider based on the detected environment.

    LOCAL FIRST priority:
        1. Python callback (free, in-process) → for AI platform mode
        2. Environment variable callback (free, in-process)
        3. Custom CLI command (if specified)
        4. z-ai CLI (paid, subprocess) → LAST RESORT
        5. None (standalone, no LLM available)

    This is the OPPOSITE of the old behavior which defaulted to z-ai CLI.

    Args:
        env_info: Pre-detected environment info (or None to auto-detect).
        llm_callback: Python callable for LLM interaction.
        cli_command: Override CLI command.

    Returns:
        LLMProvider instance, or None if no provider is available.
    """
    if env_info is None:
        env_info = detect_environment(llm_callback=llm_callback)

    # Import the provider class
    try:
        from llm_provider import HostLLMProvider, MockLLMProvider
    except ImportError:
        # llm_provider.py not in path, try sandbox dir
        _sandbox_dir = Path(__file__).resolve().parent
        if str(_sandbox_dir) not in sys.path:
            sys.path.insert(0, str(_sandbox_dir))
        try:
            from llm_provider import HostLLMProvider, MockLLMProvider
        except ImportError:
            sys.stderr.write("[super_z_core] Cannot import llm_provider\n")
            return None

    # Priority 1: Python callback (AI platform mode — FREE)
    if llm_callback:
        return HostLLMProvider(callback=llm_callback)

    # Priority 2: Callback from environment variable
    callback_path = os.environ.get("SUPER_Z_HOST_LLM_CALLBACK", "")
    if callback_path:
        try:
            module_path, func_name = callback_path.rsplit(".", 1)
            import importlib
            module = importlib.import_module(module_path)
            callback = getattr(module, func_name)
            return HostLLMProvider(callback=callback)
        except Exception as e:
            sys.stderr.write(f"[super_z_core] Failed to load callback: {e}\n")

    # Priority 3: Custom CLI command
    if cli_command:
        return HostLLMProvider(cli_command=cli_command)

    # Priority 4: Environment variable CLI command
    env_cli = os.environ.get("SUPER_Z_HOST_LLM", "")
    if env_cli:
        return HostLLMProvider(cli_command=env_cli)

    # Priority 5: z-ai CLI — LAST RESORT (paid!)
    if env_info.has_zai_cli:
        sys.stderr.write(
            "[super_z_core] WARNING: Falling back to z-ai CLI (paid calls). "
            "For free execution, set SUPER_Z_HOST_LLM_CALLBACK or pass llm_callback.\n"
        )
        return HostLLMProvider.from_zai_cli()

    # No provider available
    sys.stderr.write(
        "[super_z_core] No LLM provider available. "
        "Set SUPER_Z_HOST_LLM_CALLBACK or install z-ai CLI.\n"
    )
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Super Z Core — The Main Engine
# ═══════════════════════════════════════════════════════════════════════════

class SuperZCore:
    """Super Z Core: Local-First Routing Engine.

    The brain of the operation. Routes tasks to the cheapest, fastest
    executor available:

        LOCAL (free) → AI callback (free) → External CLI (paid) → None

    Usage:
        # Auto-detect everything
        core = SuperZCore()
        result = core.run_skill("blog-writer", "Write about AI", skill_md)

        # With explicit callback (from AI platform)
        core = SuperZCore(llm_callback=my_llm_function)
        result = core.run_skill(...)

        # Check environment
        env = core.get_environment()
    """

    def __init__(
        self,
        llm_callback: Optional[Callable[[str, str], Optional[str]]] = None,
        cli_command: Optional[str] = None,
        verbose: bool = False,
    ):
        """Initialize Super Z Core.

        Args:
            llm_callback: Python callable(system_prompt, user_prompt) -> response.
                         Pass this when running inside an AI platform.
            cli_command: Override CLI command (e.g., "claude", "gpt").
            verbose: Print debug information.
        """
        self.verbose = verbose
        self._llm_callback = llm_callback
        self._cli_command = cli_command

        # Detect environment
        self._env_info = detect_environment(llm_callback=llm_callback)
        if self.verbose:
            print(f"[super_z_core] Environment: {self._env_info.type.value}, "
                  f"provider: {self._env_info.provider_name}", file=sys.stderr)

        # Initialize components
        self._local_executor = LocalExecutor()
        self._llm_provider = None  # Lazy initialization
        self._sandbox_v2 = None    # Lazy initialization

        # Stats
        self._stats = {
            "total_calls": 0,
            "local_calls": 0,
            "ai_calls": 0,
            "external_calls": 0,
            "failed_calls": 0,
            "money_saved": 0,  # Estimated calls that would have been paid
        }

    def detect_environment(self) -> EnvironmentInfo:
        """Get environment info (re-detect if needed)."""
        return self._env_info

    def get_environment(self) -> Dict:
        """Get environment info as dict (for logging/API)."""
        return self._env_info.to_dict()

    def run_skill(
        self,
        skill_name: str,
        user_query: str,
        skill_md: str = "",
        skill_dir: Optional[str] = None,
        system_prompt: Optional[str] = None,
        timeout_sec: int = 120,
    ) -> Optional[str]:
        """Run a skill with Local-First routing.

        ROUTING LOGIC:
            1. Classify the skill (LOCAL / AI_REASONING / EXTERNAL_API)
            2. If LOCAL → execute directly (free, instant)
            3. If AI_REASONING → use LLM callback (free) or CLI (paid)
            4. If EXTERNAL_API → must use CLI (paid, no alternative)

        Args:
            skill_name: Name of the skill to run.
            user_query: User's query text.
            skill_md: Skill methodology document (SKILL.md content).
            skill_dir: Path to the skill directory.
            system_prompt: Optional system prompt override.
            timeout_sec: Timeout for LLM/CLI calls.

        Returns:
            Generated text response, or None on failure.
        """
        start_time = time.time()
        self._stats["total_calls"] += 1

        # ── Step 1: Classify the skill ──
        category = classify_skill(skill_name)

        if self.verbose:
            print(f"[super_z_core] Routing: {skill_name} → {category.value}",
                  file=sys.stderr)

        # ── Step 2: LOCAL skills → execute directly (FREE) ──
        if category == SkillCategory.LOCAL:
            result = self._execute_local(
                skill_name, user_query, skill_md,
                Path(skill_dir) if skill_dir else None,
            )
            if result is not None:
                self._stats["local_calls"] += 1
                self._stats["money_saved"] += 1  # Would have been a paid call
                if self.verbose:
                    elapsed = time.time() - start_time
                    print(f"[super_z_core] LOCAL: {skill_name} done in "
                          f"{elapsed:.2f}s (FREE)", file=sys.stderr)
                return result

        # ── Step 3: Check if query can be handled locally ──
        if self._local_executor.can_handle(skill_name, user_query):
            result = self._execute_local(
                skill_name, user_query, skill_md,
                Path(skill_dir) if skill_dir else None,
            )
            if result is not None:
                self._stats["local_calls"] += 1
                self._stats["money_saved"] += 1
                if self.verbose:
                    elapsed = time.time() - start_time
                    print(f"[super_z_core] LOCAL (fallback): {skill_name} done in "
                          f"{elapsed:.2f}s (FREE)", file=sys.stderr)
                return result

        # ── Step 4: AI_REASONING → use LLM callback (free) or CLI (paid) ──
        if category == SkillCategory.AI_REASONING:
            result = self._execute_with_llm(
                skill_name, user_query, skill_md, system_prompt, timeout_sec,
            )
            if result is not None:
                if self._env_info.has_callback:
                    self._stats["ai_calls"] += 1
                    self._stats["money_saved"] += 1  # Free vs paid
                else:
                    self._stats["external_calls"] += 1
                if self.verbose:
                    elapsed = time.time() - start_time
                    cost = "FREE" if self._env_info.has_callback else "PAID"
                    print(f"[super_z_core] AI: {skill_name} done in "
                          f"{elapsed:.2f}s ({cost})", file=sys.stderr)
                return result

        # ── Step 5: EXTERNAL_API → must use CLI (paid) ──
        if category == SkillCategory.EXTERNAL_API:
            result = self._execute_with_llm(
                skill_name, user_query, skill_md, system_prompt, timeout_sec,
            )
            if result is not None:
                self._stats["external_calls"] += 1
                if self.verbose:
                    elapsed = time.time() - start_time
                    print(f"[super_z_core] EXTERNAL: {skill_name} done in "
                          f"{elapsed:.2f}s (PAID)", file=sys.stderr)
                return result

        # ── All methods failed ──
        self._stats["failed_calls"] += 1
        if self.verbose:
            print(f"[super_z_core] FAILED: {skill_name} — no available executor",
                  file=sys.stderr)
        return None

    # ── Private: Local execution ──────────────────────────────────────────

    def _execute_local(
        self,
        skill_name: str,
        query: str,
        skill_md: str,
        skill_dir: Optional[Path],
    ) -> Optional[str]:
        """Execute a task locally using Python/Bash only."""
        return self._local_executor.execute(
            skill_name=skill_name,
            query=query,
            skill_md=skill_md,
            skill_dir=skill_dir,
        )

    # ── Private: LLM execution ────────────────────────────────────────────

    def _execute_with_llm(
        self,
        skill_name: str,
        query: str,
        skill_md: str,
        system_prompt: Optional[str],
        timeout_sec: int,
    ) -> Optional[str]:
        """Execute a task using LLM (callback or CLI).

        Uses SandboxV2 (Observer+POLER) when available for token efficiency.
        Falls back to direct LLM call when sandbox is not available.
        """
        # Try SandboxV2 first (more efficient)
        try:
            sandbox = self._get_sandbox_v2()
            if sandbox:
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                elif skill_md:
                    messages.append({
                        "role": "system",
                        "content": (
                            f"You are the '{skill_name}' skill. "
                            f"Follow the methodology below strictly.\n\n"
                            f"--- SKILL.md ---\n{skill_md}\n--- END SKILL.md ---"
                        ),
                    })
                messages.append({"role": "user", "content": query})

                return sandbox.chat(messages)
        except Exception as e:
            if self.verbose:
                print(f"[super_z_core] SandboxV2 failed: {e}", file=sys.stderr)

        # Direct LLM call (fallback)
        provider = self._get_llm_provider()
        if provider:
            sp = system_prompt or (
                f"You are the '{skill_name}' skill. "
                f"Follow the methodology strictly. "
                f"Respond in the user's language."
            )
            if skill_md and not system_prompt:
                sp += f"\n\n--- SKILL.md ---\n{skill_md[:3000]}\n--- END ---"

            return provider.chat(sp, query, timeout_sec=timeout_sec)

        return None

    # ── Private: Lazy initialization ──────────────────────────────────────

    def _get_llm_provider(self) -> Optional[Any]:
        """Get or create the LLM provider (lazy init)."""
        if self._llm_provider is None:
            self._llm_provider = create_llm_provider(
                env_info=self._env_info,
                llm_callback=self._llm_callback,
                cli_command=self._cli_command,
            )
        return self._llm_provider

    def _get_sandbox_v2(self) -> Optional[Any]:
        """Get or create SandboxV2 instance (lazy init)."""
        if self._sandbox_v2 is not None:
            return self._sandbox_v2

        provider = self._get_llm_provider()
        if provider is None:
            return None

        try:
            from sandbox_v2 import SandboxV2
            self._sandbox_v2 = SandboxV2(
                llm_provider=provider,
                verbose=self.verbose,
            )
            return self._sandbox_v2
        except ImportError:
            if self.verbose:
                print("[super_z_core] SandboxV2 not available, using direct LLM",
                      file=sys.stderr)
            return None

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> Dict:
        """Return execution statistics.

        Includes:
        - Total calls made
        - How many were local (free)
        - How many used AI callback (free)
        - How many used external CLI (paid)
        - Estimated money saved (calls that would have been paid under old system)
        """
        return dict(self._stats)

    def cost_summary(self) -> str:
        """Return a human-readable cost summary."""
        s = self._stats
        total = s["total_calls"] or 1
        local_pct = s["local_calls"] / total * 100
        ai_pct = s["ai_calls"] / total * 100
        ext_pct = s["external_calls"] / total * 100
        saved_pct = s["money_saved"] / total * 100

        return (
            f"Super Z Core — Cost Summary\n"
            f"{'='*40}\n"
            f"Total calls:     {s['total_calls']}\n"
            f"Local (free):    {s['local_calls']} ({local_pct:.0f}%)\n"
            f"AI callback:     {s['ai_calls']} ({ai_pct:.0f}%)\n"
            f"External (paid): {s['external_calls']} ({ext_pct:.0f}%)\n"
            f"Failed:          {s['failed_calls']}\n"
            f"Money saved:     ~{s['money_saved']} calls ({saved_pct:.0f}% of total)\n"
            f"{'='*40}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Drop-in replacement for run_skill_sandbox_v2()
# ═══════════════════════════════════════════════════════════════════════════

# Module-level singleton (same pattern as bridge.py)
_core_instance: Optional[SuperZCore] = None


def get_core(
    llm_callback: Optional[Callable] = None,
    verbose: bool = False,
) -> SuperZCore:
    """Get or create the Super Z Core singleton.

    Args:
        llm_callback: Python callable for LLM interaction.
        verbose: Print debug information.

    Returns:
        SuperZCore instance.
    """
    global _core_instance
    if _core_instance is None or llm_callback is not None:
        _core_instance = SuperZCore(
            llm_callback=llm_callback,
            verbose=verbose,
        )
    return _core_instance


def run_skill_local_first(
    skill_name: str,
    user_query: str,
    skill_md: str = "",
    skill_dir: Optional[str] = None,
    system_prompt: Optional[str] = None,
    llm_callback: Optional[Callable] = None,
    verbose: bool = False,
) -> Optional[str]:
    """Drop-in replacement for the old run_skill_sandbox_v2().

    Uses Local-First routing instead of always calling external CLI.

    Args:
        skill_name: Name of the skill.
        user_query: User's query.
        skill_md: Skill methodology document.
        skill_dir: Path to skill directory.
        system_prompt: System prompt override.
        llm_callback: Python callable(system_prompt, user_prompt) -> response.
        verbose: Print debug information.

    Returns:
        Text response, or None on failure.
    """
    core = get_core(llm_callback=llm_callback, verbose=verbose)
    return core.run_skill(
        skill_name=skill_name,
        user_query=user_query,
        skill_md=skill_md,
        skill_dir=skill_dir,
        system_prompt=system_prompt,
    )


# ═══════════════════════════════════════════════════════════════════════════
# get_backend_type_routing() — replacement for the old routing function
# ═══════════════════════════════════════════════════════════════════════════

def get_backend_type_routing(
    skill_name: str,
    env_info: Optional[EnvironmentInfo] = None,
) -> Dict[str, Any]:
    """Determine the optimal backend type for a skill.

    REPLACES the old function that always returned "zai_cli".
    Now returns "local" when possible, "callback" when in AI platform,
    and "cli" only as last resort.

    Args:
        skill_name: Name of the skill to route.
        env_info: Pre-detected environment info (or None to auto-detect).

    Returns:
        {
            "backend": "local" | "callback" | "cli",
            "category": "local" | "ai" | "external",
            "cost": "free" | "free_callback" | "paid",
            "reason": "Human-readable explanation",
            "env_type": "ai_platform" | "local_cli" | "standalone",
        }
    """
    if env_info is None:
        env_info = detect_environment()

    category = classify_skill(skill_name)

    # LOCAL skills → always free
    if category == SkillCategory.LOCAL:
        return {
            "backend": "local",
            "category": category.value,
            "cost": "free",
            "reason": f"Skill '{skill_name}' can be executed locally with Python/Bash",
            "env_type": env_info.type.value,
        }

    # AI_REASONING skills → callback if available, CLI otherwise
    if category == SkillCategory.AI_REASONING:
        if env_info.has_callback:
            return {
                "backend": "callback",
                "category": category.value,
                "cost": "free_callback",
                "reason": (
                    f"Skill '{skill_name}' needs LLM reasoning. "
                    f"Using host AI callback (free, in-process)."
                ),
                "env_type": env_info.type.value,
            }
        elif env_info.has_zai_cli or env_info.has_other_cli:
            return {
                "backend": "cli",
                "category": category.value,
                "cost": "paid",
                "reason": (
                    f"Skill '{skill_name}' needs LLM reasoning. "
                    f"No callback available, using CLI (paid). "
                    f"Set SUPER_Z_HOST_LLM_CALLBACK for free execution."
                ),
                "env_type": env_info.type.value,
            }
        else:
            return {
                "backend": "none",
                "category": category.value,
                "cost": "unavailable",
                "reason": (
                    f"Skill '{skill_name}' needs LLM reasoning but no provider "
                    f"is available. Install z-ai CLI or set "
                    f"SUPER_Z_HOST_LLM_CALLBACK."
                ),
                "env_type": env_info.type.value,
            }

    # EXTERNAL_API skills → always need CLI
    if category == SkillCategory.EXTERNAL_API:
        if env_info.has_zai_cli or env_info.has_other_cli:
            return {
                "backend": "cli",
                "category": category.value,
                "cost": "paid",
                "reason": (
                    f"Skill '{skill_name}' requires external API. "
                    f"CLI is the only option."
                ),
                "env_type": env_info.type.value,
            }
        else:
            return {
                "backend": "none",
                "category": category.value,
                "cost": "unavailable",
                "reason": (
                    f"Skill '{skill_name}' requires external API but no CLI "
                    f"is available."
                ),
                "env_type": env_info.type.value,
            }

    # Fallback (should not reach here)
    return {
        "backend": "none",
        "category": "unknown",
        "cost": "unavailable",
        "reason": f"Cannot determine routing for skill '{skill_name}'",
        "env_type": env_info.type.value,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLI — for testing and debugging
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Super Z Core — Local-First Routing Engine"
    )
    parser.add_argument(
        "command",
        choices=["env", "route", "run", "stats"],
        help="Command to execute",
    )
    parser.add_argument("--skill", default="blog-writer", help="Skill name")
    parser.add_argument("--query", default="", help="User query")
    parser.add_argument("--skill-md", default=None, help="Path to SKILL.md")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.command == "env":
        # Show environment detection
        env = detect_environment()
        print(json.dumps(env.to_dict(), ensure_ascii=False, indent=2))

    elif args.command == "route":
        # Show routing decision for a skill
        routing = get_backend_type_routing(args.skill)
        print(json.dumps(routing, ensure_ascii=False, indent=2))

    elif args.command == "run":
        # Run a skill with Local-First routing
        if not args.query:
            print("Error: --query is required for 'run' command", file=sys.stderr)
            sys.exit(1)

        skill_md = ""
        if args.skill_md:
            skill_md = Path(args.skill_md).read_text(encoding="utf-8")

        core = SuperZCore(verbose=args.verbose)
        result = core.run_skill(
            skill_name=args.skill,
            user_query=args.query,
            skill_md=skill_md,
        )

        if result:
            print(result)
        else:
            print("No result (skill could not be executed)", file=sys.stderr)
            sys.exit(1)

    elif args.command == "stats":
        # Show stats from a core instance
        core = SuperZCore(verbose=args.verbose)
        # Simulate a few calls to demonstrate
        core.run_skill("poler-psi", "test query")
        core.run_skill("blog-writer", "test query")
        core.run_skill("web-search", "test query")
        print(core.cost_summary())
