from __future__ import annotations

import json
import os
from typing import Any

from .base import LLMBackend
from ..poler_edit import PolerEdit


class SandboxAgentBackend(LLMBackend):
    """Local agentic backend for in-process sandbox execution.

    This backend does not require OpenAI/Anthropic or a CLI. It simulates a
    lightweight multi-agent orchestration layer by routing the prompt through
    a small set of local roles: planner, executor, reviewer. The output is a
    deterministic structured response that can be used by the wrapper and the
    CLI in environments where the host wants to embed Super-Z directly into its
    own agent runtime.
    """

    name = "sandbox"

    def __init__(self, role_map: dict[str, str] | None = None) -> None:
        self.role_map = role_map or {
            "planner": "decompose the request into a concise execution plan",
            "executor": "carry out the requested action with concrete steps",
            "reviewer": "validate the result and add a short quality note",
        }

    def chat(self, system_prompt: str, user_prompt: str, timeout: int = 120, **kwargs) -> str:
        analysis = PolerEdit().process(system_prompt, user_prompt, kwargs.get("text", ""))
        roles = self._build_roles(system_prompt, user_prompt, analysis)
        payload = {
            "backend": self.name,
            "mode": "local-sandbox",
            "roles": roles,
            "user_request": user_prompt.strip()[:400],
            "system_hint": system_prompt.strip()[:400],
            "poler": analysis,
            "notes": [
                "No external provider required",
                "Multi-agent responsibilities are handled in-process",
                "Suitable for embedded sandbox deployment",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def health_check(self) -> bool:
        return True

    def _build_roles(self, system_prompt: str, user_prompt: str, analysis: dict[str, Any]) -> list[dict[str, Any]]:
        request = user_prompt.strip() or "general task"
        system_hint = system_prompt.strip() or "follow the skill methodology"
        return [
            {
                "role": "planner",
                "task": self.role_map.get("planner", "decompose the request into a concise execution plan"),
                "input": request,
                "output": f"Plan for: {request[:120]}",
                "poler_summary": analysis["summary"],
            },
            {
                "role": "executor",
                "task": self.role_map.get("executor", "carry out the requested action with concrete steps"),
                "input": system_hint,
                "output": f"Execution draft for: {request[:120]}",
            },
            {
                "role": "reviewer",
                "task": self.role_map.get("reviewer", "validate the result and add a short quality note"),
                "input": request,
                "output": f"Reviewed draft for: {request[:120]}",
            },
        ]
