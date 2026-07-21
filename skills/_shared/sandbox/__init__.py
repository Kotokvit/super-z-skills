"""
sandbox — LLM-powered Agent Sandbox for Super-Z.

v3 (LOCAL-FIRST, DEFAULT): Super Z Core + Observer + POLER
    Super Z Core — Local-First routing (free → cheap → paid)
    Observer   — бинарные решения (нужна ли генерация? хватит ли? ~200 токенов/вызов)
    POLER      — динамическая обработка текста (без чтения всего текста, через резонанс)
    SandboxV2  — генерация контента (1 вызов LLM, не 4)

    Routing priority:
        1. LOCAL (Python/Bash) → FREE, instant
        2. AI callback (host LLM) → FREE, in-process
        3. External CLI (z-ai) → PAID, last resort

v2: Observer + POLER + Sandbox (token-efficient)
    Observer decides + 1 LLM call for content = 1-3 calls, ~500-1500 tokens, 3-15s

v1 (LEGACY): 4-agent chain
    PlannerAgent → ExecutorAgent → ReviewerAgent → CriticAgent
    4 полных LLM-генерации, ~5000 токенов, 25-80s — ПЕРЕБОР

Usage (v3 — local-first, DEFAULT):
    from sandbox import SuperZCore

    # With AI callback (FREE)
    core = SuperZCore(llm_callback=my_llm_function)
    result = core.run_skill("blog-writer", "Write about AI", skill_md)

    # Without callback (falls back to CLI, PAID)
    core = SuperZCore()
    result = core.run_skill("blog-writer", "Write about AI", skill_md)

Usage (v2 — Observer + POLER):
    from sandbox import SandboxV2
    from sandbox.llm_provider import HostLLMProvider

    provider = HostLLMProvider(callback=my_llm_function)  # FREE
    backend = SandboxV2(llm_provider=provider, use_observer=True)
    result = backend.chat(
        messages=[{"role": "user", "content": "Write a blog post about AI"}],
    )

Usage (v1 — legacy, kept for compatibility):
    from sandbox import SandboxBackend
    from sandbox.llm_provider import HostLLMProvider

    provider = HostLLMProvider(callback=my_llm_function)  # FREE
    backend = SandboxBackend(llm_provider=provider)
"""

# v3 — Local-First routing (DEFAULT)
try:
    from super_z_core import (
        SuperZCore,
        run_skill_local_first,
        get_backend_type_routing,
        detect_environment,
        EnvironmentInfo,
        EnvironmentType,
        classify_skill,
        SkillCategory,
        LocalExecutor,
        create_llm_provider,
    )
    _HAS_SUPER_Z_CORE = True
except ImportError:
    _HAS_SUPER_Z_CORE = False

# v2 — Observer + POLER (token-efficient)
from sandbox_v2 import SandboxV2, Observer, PolerContextExtractor

# v1 — Legacy 4-agent chain (kept for backward compatibility)
from backend import SandboxBackend
from agents import (
    Agent,
    PlannerAgent,
    ExecutorAgent,
    ReviewerAgent,
    CriticAgent,
    AgentMessage,
    AgentRole,
)

# Shared components
from llm_provider import (
    LLMProvider,
    HostLLMProvider,
    MockLLMProvider,
)

__all__ = [
    # v3 (local-first, default)
    "SuperZCore",
    "run_skill_local_first",
    "get_backend_type_routing",
    "detect_environment",
    "EnvironmentInfo",
    "EnvironmentType",
    "classify_skill",
    "SkillCategory",
    "LocalExecutor",
    "create_llm_provider",
    # v2 (observer+POLER)
    "SandboxV2",
    "Observer",
    "PolerContextExtractor",
    # v1 (legacy)
    "SandboxBackend",
    "Agent",
    "PlannerAgent",
    "ExecutorAgent",
    "ReviewerAgent",
    "CriticAgent",
    "AgentMessage",
    "AgentRole",
    # Shared
    "LLMProvider",
    "HostLLMProvider",
    "MockLLMProvider",
]
