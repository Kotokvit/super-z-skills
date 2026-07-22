from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

# Use local PolerEdit implementation to avoid circular import with src/super_z/poler_edit.py
# (which itself imports from this package). The fallback below is API-compatible.
from .poler_enhanced_lite import POLEREnhanced


class PolerEdit:
    """Local PolerEdit used by SandboxV2Backend. Mirrors the API of src/super_z/poler_edit.py."""

    def process(self, system_prompt: str = "", user_prompt: str = "", text: str = ""):
        content = "\n\n".join(
            part.strip() for part in (system_prompt, user_prompt, text) if part and part.strip()
        )
        return POLEREnhanced(content, user_prompt, source="request").analyze()


class SandboxV2Backend:
    """Hybrid sandbox backend: Observer + POLER + Sandbox.

    It uses a lightweight resonance-based POLER pass to select relevant text
    fragments and then emits a compact structured plan. The observer is optional
    and disabled by default for speed.
    """

    name = "sandbox_v2"

    def __init__(self, enable_observer: bool = False, max_llm_calls: int = 3) -> None:
        self.enable_observer = enable_observer
        self.max_llm_calls = max_llm_calls

    def chat(self, system_prompt: str, user_prompt: str, timeout: int = 120, **kwargs) -> str:
        text = kwargs.get("text") or ""
        query = user_prompt or ""
        role_plan = self._build_role_plan(system_prompt, query, text)
        payload = {
            "backend": self.name,
            "mode": "hybrid-sandbox",
            "enable_observer": self.enable_observer,
            "estimated_llm_calls": self._estimate_llm_calls(),
            "roles": role_plan,
            "notes": [
                "Observer is optional and disabled by default",
                "POLER works via resonance over relevant fragments",
                "No external provider required",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def health_check(self) -> bool:
        return True

    def _estimate_llm_calls(self) -> int:
        return min(self.max_llm_calls, 3)

    def _build_role_plan(self, system_prompt: str, query: str, text: str) -> List[Dict[str, Any]]:
        poler = PolerEdit()
        analysis = poler.process(system_prompt, query, text)
        roles = [
            {
                "role": "planner",
                "task": "decompose the request into a concise plan",
                "input": query[:200],
                "output": self._summarize_query(query),
            }
        ]
        roles.append(
            {
                "role": "poler",
                "task": "process the request and find relevant fragments using resonance scoring",
                "input": query[:200],
                "fragments": analysis["fragments"],
                "summary": analysis["summary"],
                "scores": analysis["scores"],
            }
        )
        roles.append(
            {
                "role": "executor",
                "task": "produce the action-oriented result",
                "input": system_prompt[:200],
                "output": "action plan generated locally",
            }
        )
        if self.enable_observer:
            roles.append(
                {
                    "role": "observer",
                    "task": "optionally review the context for hidden gaps",
                    "input": query[:200],
                    "output": "observer pass enabled",
                }
            )
        return roles

    def _summarize_query(self, query: str) -> str:
        words = re.findall(r"[a-zA-Z0-9_]+", query)
        return " ".join(words[:8]) if words else "general request"


class SandboxV1Backend(SandboxV2Backend):
    name = "sandbox_v1"

    def __init__(self, enable_observer: bool = True, max_llm_calls: int = 4) -> None:
        super().__init__(enable_observer=enable_observer, max_llm_calls=max_llm_calls)


class SandboxBridge:
    def __init__(self, backend: Optional[str] = None) -> None:
        self.backend = backend or os.getenv("SUPER_Z_SANDBOX_BACKEND", "v2")

    def get_backend(self, backend: Optional[str] = None):
        selected = (backend or self.backend or "v2").strip().lower()
        if selected in {"v2", "sandbox_v2", "sandbox-v2"}:
            return SandboxV2Backend(enable_observer=False)
        if selected in {"v1", "sandbox_v1", "sandbox-v1"}:
            return SandboxV1Backend(enable_observer=True)
        return SandboxV2Backend(enable_observer=False)


def create_backend(name: str, **kwargs):
    bridge = SandboxBridge()
    return bridge.get_backend(name)


__all__ = ["SandboxV2Backend", "SandboxV1Backend", "SandboxBridge", "create_backend", "PolerEdit"]
