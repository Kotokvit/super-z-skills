#!/usr/bin/env python3
"""
capability_registry.py — Capability Registry (v2.0)

The big shift: instead of asking "which skill should I run?", the orchestrator
asks "which capability do I need?" and the registry returns all skills that
provide it. The planner then picks the best one based on resources, confidence,
and historical performance.

A capability is a verb:
    extract_text, summarize, transcribe, search, embed, translate,
    extract_entities, analyze_image, generate_image, generate_audio,
    render_chart, render_pdf, fetch_url, ...

A skill declares which capabilities it provides in its manifest.json:

    {
      "name": "pdf-ocr",
      "capabilities": [
        {"name": "extract_text", "from": ["pdf", "image"], "confidence": 0.85}
      ],
      "resources": {"cpu": "medium", "ram": "medium", "network": false, "latency_ms": 3000}
    }

The registry builds two indexes:
    capability -> [providers]          (for routing)
    skill      -> [capabilities]       (for inverse lookup)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CapabilityProvider:
    """One skill that provides one capability."""
    skill_name: str
    capability: str
    input_types: list[str] = field(default_factory=list)   # pdf|image|audio|url|text|...
    confidence: float = 0.8
    resources: dict = field(default_factory=dict)          # cpu/ram/network/latency

    def to_dict(self) -> dict:
        return {
            "skill": self.skill_name,
            "capability": self.capability,
            "input_types": self.input_types,
            "confidence": self.confidence,
            "resources": self.resources,
        }


class CapabilityRegistry:
    """Indexes skills by capability, not by name."""

    # Sensible defaults — used when a manifest doesn't declare capabilities
    DEFAULT_CAPABILITIES = {
        "blog-writer":         [{"name": "generate_text", "from": ["text"], "confidence": 0.8}],
        "seo-content-writer":  [{"name": "generate_text", "from": ["text"], "confidence": 0.8}],
        "resume-builder":      [{"name": "generate_document", "from": ["text"], "confidence": 0.85}],
        "pdf":                 [{"name": "render_pdf", "from": ["text"], "confidence": 0.95}],
        "docx":                [{"name": "render_document", "from": ["text"], "confidence": 0.95}],
        "xlsx":                [{"name": "render_spreadsheet", "from": ["data"], "confidence": 0.95}],
        "pptx":                [{"name": "render_presentation", "from": ["text"], "confidence": 0.95}],
        "charts":              [{"name": "render_chart", "from": ["data"], "confidence": 0.92}],
        "LLM":                 [{"name": "chat", "from": ["text"], "confidence": 0.95}],
        "TTS":                 [{"name": "generate_audio", "from": ["text"], "confidence": 0.93}],
        "ASR":                 [{"name": "transcribe", "from": ["audio"], "confidence": 0.92}],
        "VLM":                 [{"name": "analyze_image", "from": ["image"], "confidence": 0.9}],
        "image-generation":    [{"name": "generate_image", "from": ["text"], "confidence": 0.88}],
        "image-edit":          [{"name": "edit_image", "from": ["image"], "confidence": 0.85}],
        "image-search":        [{"name": "search_image", "from": ["text"], "confidence": 0.85}],
        "image-understand":    [{"name": "analyze_image", "from": ["image"], "confidence": 0.88}],
        "video-understand":    [{"name": "analyze_video", "from": ["video"], "confidence": 0.87}],
        "web-search":          [{"name": "search_web", "from": ["text"], "confidence": 0.88}],
        "web-reader":          [{"name": "fetch_url", "from": ["url"], "confidence": 0.9}],
        "media-triage":        [{"name": "transcribe", "from": ["video", "audio"], "confidence": 0.9},
                                {"name": "extract_entities", "from": ["video", "audio"], "confidence": 0.8}],
        "site-context-loader": [{"name": "geocode", "from": ["text"], "confidence": 0.85},
                                {"name": "extract_entities", "from": ["text"], "confidence": 0.75}],
        "contentanalysis":     [{"name": "analyze_text", "from": ["text"], "confidence": 0.85}],
        "gap-detector":        [{"name": "verify_claims", "from": ["text"], "confidence": 0.9}],
        "agent-browser":       [{"name": "browse", "from": ["url"], "confidence": 0.85}],
        "coding-agent":        [{"name": "generate_code", "from": ["text"], "confidence": 0.88}],
        "marketing-mode":      [{"name": "generate_strategy", "from": ["text"], "confidence": 0.82}],
        "gift-evaluator":      [{"name": "recommend", "from": ["text"], "confidence": 0.8}],
        "ui-ux-pro-max":       [{"name": "design", "from": ["text"], "confidence": 0.85}],
        "design":              [{"name": "design", "from": ["text"], "confidence": 0.82}],
        "anti-pua":            [{"name": "analyze_text", "from": ["text"], "confidence": 0.78}],
        "cheat-sheet":         [{"name": "summarize", "from": ["text"], "confidence": 0.85}],
        "finance":             [{"name": "analyze_data", "from": ["data"], "confidence": 0.82}],
        "content-strategy":    [{"name": "generate_strategy", "from": ["text"], "confidence": 0.8}],
    }

    def __init__(self, skills_root: Path):
        self.skills_root = Path(skills_root)
        self._by_capability: dict[str, list[CapabilityProvider]] = {}
        self._by_skill: dict[str, list[str]] = {}
        self._loaded = False

    # ── Loading ────────────────────────────────────────────────────────

    def load(self) -> None:
        """Scan skills/*/manifest.json and build the index."""
        self._by_capability.clear()
        self._by_skill.clear()

        if not self.skills_root.exists():
            self._loaded = True
            return

        for skill_dir in sorted(self.skills_root.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
                continue
            manifest_path = skill_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            skill_name = manifest.get("name", skill_dir.name)
            capabilities = manifest.get("capabilities") or self.DEFAULT_CAPABILITIES.get(skill_name, [])
            resources = manifest.get("resources", {"cpu": "low", "ram": "low", "network": False, "latency_ms": 2000})

            cap_list = []
            for cap in capabilities:
                if isinstance(cap, str):
                    cap = {"name": cap}
                provider = CapabilityProvider(
                    skill_name=skill_name,
                    capability=cap.get("name", "unknown"),
                    input_types=cap.get("from", []),
                    confidence=float(cap.get("confidence", 0.8)),
                    resources=resources,
                )
                self._by_capability.setdefault(provider.capability, []).append(provider)
                cap_list.append(provider.capability)

            self._by_skill[skill_name] = cap_list

        # Sort each capability's providers by confidence (desc)
        for cap, providers in self._by_capability.items():
            providers.sort(key=lambda p: (-p.confidence, p.skill_name))

        self._loaded = True

    # ── Lookup ─────────────────────────────────────────────────────────

    def providers_for(self, capability: str, input_type: Optional[str] = None) -> list[CapabilityProvider]:
        """Return all skills that can provide this capability, sorted by confidence."""
        if not self._loaded:
            self.load()
        providers = self._by_capability.get(capability, [])
        if input_type:
            providers = [p for p in providers if not p.input_types or input_type in p.input_types]
        return providers

    def best_provider(self, capability: str, input_type: Optional[str] = None) -> Optional[CapabilityProvider]:
        providers = self.providers_for(capability, input_type)
        return providers[0] if providers else None

    def capabilities_of(self, skill_name: str) -> list[str]:
        """Inverse lookup — what can this skill do?"""
        if not self._loaded:
            self.load()
        return self._by_skill.get(skill_name, [])

    def all_capabilities(self) -> list[str]:
        if not self._loaded:
            self.load()
        return sorted(self._by_capability.keys())

    # ── Serialization ──────────────────────────────────────────────────

    def to_dict(self) -> dict:
        if not self._loaded:
            self.load()
        return {
            "schema": "capability_registry/v2.0",
            "capabilities": {
                cap: [p.to_dict() for p in providers]
                for cap, providers in self._by_capability.items()
            },
            "skills": self._by_skill,
            "stats": {
                "total_skills": len(self._by_skill),
                "total_capabilities": len(self._by_capability),
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ─── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    skills_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("skills")
    reg = CapabilityRegistry(skills_root)
    reg.load()

    if len(sys.argv) > 2 and sys.argv[2] == "--list":
        for cap in reg.all_capabilities():
            providers = reg.providers_for(cap)
            print(f"  {cap}  ({len(providers)} provider{'s' if len(providers) != 1 else ''})")
            for p in providers:
                print(f"    → {p.skill_name}  (conf={p.confidence:.2f}, types={p.input_types})")
    else:
        print(reg.to_json())
