from __future__ import annotations

from .base import LLMBackend


class MockLLMBackend(LLMBackend):
    name = "mock"

    def chat(self, system_prompt: str, user_prompt: str, timeout: int = 120, **kwargs) -> str:
        return (
            "Mock LLM response. "
            f"Skill context: {user_prompt.split('USER REQUEST:', 1)[-1].strip()[:200]}"
        )

    def health_check(self) -> bool:
        return True
