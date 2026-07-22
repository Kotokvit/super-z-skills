from __future__ import annotations

from abc import ABC, abstractmethod


class LLMBackend(ABC):
    name = "base"

    @abstractmethod
    def chat(self, system_prompt: str, user_prompt: str, timeout: int = 120, **kwargs) -> str:
        raise NotImplementedError

    async def achat(self, system_prompt: str, user_prompt: str, timeout: int = 120, **kwargs) -> str:
        return self.chat(system_prompt, user_prompt, timeout=timeout, **kwargs)

    @abstractmethod
    def health_check(self) -> bool:
        raise NotImplementedError

    @property
    def supports_streaming(self) -> bool:
        return False
