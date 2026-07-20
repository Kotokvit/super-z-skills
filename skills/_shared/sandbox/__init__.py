"""
sandbox — Internal Agent Sandbox for Super-Z.

Provides a self-contained LLM backend that distributes work between
internal agent roles (planner → executor → reviewer → critic) WITHOUT
any external LLM provider. Designed for embedding into AI sandboxes
where the AI itself acts as the "LLM backend" through pseudo-API calls.

Architecture:
    SandboxBackend — LLMProvider that routes to internal agents
    Agent          — Base class for all agent roles
    PlannerAgent   — Decomposes query into subtasks
    ExecutorAgent  — Generates content for each subtask
    ReviewerAgent  — Checks quality and completeness
    CriticAgent    — Final verdict: accept, reject, or iterate

Usage:
    from sandbox import SandboxBackend

    backend = SandboxBackend()
    result = backend.chat(
        messages=[{"role": "user", "content": "Write a blog post about AI"}],
        skill_context={"skill_name": "blog-writer", "skill_md": "..."},
    )
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

__all__ = [
    "SandboxBackend",
    "Agent",
    "PlannerAgent",
    "ExecutorAgent",
    "ReviewerAgent",
    "CriticAgent",
    "AgentMessage",
    "AgentRole",
]
