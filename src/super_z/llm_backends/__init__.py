from .base import LLMBackend
from .mock import MockLLMBackend
from .cli import ZaiCliBackend
from .sandbox import SandboxAgentBackend


def create_backend(name: str, **kwargs) -> LLMBackend:
    name = (name or "mock").strip().lower()
    if name in {"mock", "fallback"}:
        return MockLLMBackend()
    if name in {"zai_cli", "z-ai", "zai"}:
        return ZaiCliBackend(**kwargs)
    if name in {"sandbox", "local", "local-agent", "agent-sandbox"}:
        return SandboxAgentBackend(**kwargs)
    raise ValueError(f"Unsupported backend: {name}")


__all__ = ["LLMBackend", "MockLLMBackend", "ZaiCliBackend", "SandboxAgentBackend", "create_backend"]
