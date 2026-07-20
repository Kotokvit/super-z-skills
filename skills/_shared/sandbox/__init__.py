"""
sandbox — LLM-powered Agent Sandbox for Super-Z.

Provides a self-contained backend that distributes work between
LLM-powered agent roles. Each agent calls the SAME LLM with a
DIFFERENT role-specific system prompt, exactly like how multi-agent
AI systems work in ChatGPT, Claude, Kimi 2.0, etc.

Architecture:
    LLMProvider    — Abstract LLM interface (host callback / z-ai CLI / mock)
    SandboxBackend — Orchestrator that routes to internal agents
    Agent          — Base class for all agent roles
    PlannerAgent   — Decomposes query into subtasks (calls LLM as planner)
    ExecutorAgent  — Generates content for each subtask (calls LLM as executor)
    ReviewerAgent  — Checks quality and completeness (calls LLM as reviewer)
    CriticAgent    — Final verdict: accept, reject, or iterate (calls LLM as critic)

Usage:
    from sandbox import SandboxBackend
    from sandbox.llm_provider import HostLLMProvider

    # With z-ai CLI as the host LLM
    provider = HostLLMProvider.from_zai_cli()
    backend = SandboxBackend(llm_provider=provider)
    result = backend.chat(
        messages=[{"role": "user", "content": "Write a blog post about AI"}],
    )

    # With a Python callback as the host LLM
    def my_llm(system_prompt, user_prompt):
        return "response text"

    provider = HostLLMProvider(callback=my_llm)
    backend = SandboxBackend(llm_provider=provider)
"""
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
from sandbox.llm_provider import (
    LLMProvider,
    HostLLMProvider,
    MockLLMProvider,
)

__all__ = [
    "SandboxBackend",
    "Agent",
    "PlannerAgent",
    "ExecutorAgent",
    "ReviewerAgent",
    "CriticAgent",
    "AgentMessage",
    "AgentRole",
    "LLMProvider",
    "HostLLMProvider",
    "MockLLMProvider",
]
