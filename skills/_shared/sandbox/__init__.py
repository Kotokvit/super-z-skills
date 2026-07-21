"""
sandbox — LLM-powered Agent Sandbox for Super-Z.

v2 (DEFAULT): Observer + POLER + Sandbox
    Observer   — бинарные решения (нужна ли генерация? хватит ли? ~200 токенов/вызов)
    POLER      — динамическая обработка текста (без чтения всего текста, через резонанс)
    SandboxV2  — генерация контента (1 вызов LLM, не 4)

v1 (LEGACY): 4-agent chain
    PlannerAgent → ExecutorAgent → ReviewerAgent → CriticAgent
    4 полных LLM-генерации, ~5000 токенов, 25-80s — ПЕРЕБОР

Architecture v2 (token-efficient):
    1. POLER extracts resonant context from SKILL.md (0 LLM calls)
    2. Observer decides if generation is needed (1 LLM call, ~200 tokens)
    3. If yes: 1 LLM call generates content with POLER-filtered context
    4. Observer checks if result is sufficient (1 LLM call, ~200 tokens)
    5. If no: 1 more LLM call with focus instruction (loop max 2 times)

Usage (v2 — default):
    from sandbox import SandboxV2
    from sandbox.llm_provider import HostLLMProvider

    provider = HostLLMProvider.from_zai_cli()
    backend = SandboxV2(llm_provider=provider, use_observer=True)
    result = backend.chat(
        messages=[{"role": "user", "content": "Write a blog post about AI"}],
    )

Usage (v1 — legacy, kept for compatibility):
    from sandbox import SandboxBackend
    from sandbox.llm_provider import HostLLMProvider

    provider = HostLLMProvider.from_zai_cli()
    backend = SandboxBackend(llm_provider=provider)
"""

# v2 — Observer + POLER (DEFAULT, token-efficient)
from sandbox.sandbox_v2 import SandboxV2, Observer, PolerContextExtractor

# v1 — Legacy 4-agent chain (kept for backward compatibility)
from sandbox.backend import SandboxBackend
from sandbox.agents import (
    Agent,
    PlannerAgent,
    ExecutorAgent,
    ReviewerAgent,
    CriticAgent,
    AgentMessage,
    AgentRole,
)

# Shared components
from sandbox.llm_provider import (
    LLMProvider,
    HostLLMProvider,
    MockLLMProvider,
)

__all__ = [
    # v2 (default)
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
