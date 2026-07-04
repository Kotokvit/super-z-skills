#!/usr/bin/env python3
"""
skill_schema.py — Standardized output schema for all Super-Z skills.

Every skill MUST return a SkillOutput object (or dict with these fields).
This is the contract that makes skills composable, comparable, and
aggregatable by the orchestrator.

Schema v2.0 — aligned with the LLM Integration Spec.

Usage in a skill:
    from _shared.skill_schema import SkillOutput, Entity, Relation, Source

    output = SkillOutput(
        status="ok",
        confidence=0.91,
        summary="Extracted 12 entities from the PDF",
        entities=[Entity(name="OpenAI", type="organization"), ...],
        relations=[Relation(subject="OpenAI", predicate="released", object="GPT-4")],
        sources=[Source(kind="pdf", uri="file:///doc.pdf", page=3)],
        artifacts=[{"kind": "json", "uri": "file:///out.json"}],
        warnings=["page 5 was unreadable"],
        metrics={"chars_extracted": 4521, "time_ms": 1240},
    )
    output.dump()  # → JSON dict
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ─── Primitives ─────────────────────────────────────────────────────────

@dataclass
class Entity:
    """A named thing extracted from a skill's output."""
    name: str
    type: str = "unknown"          # person|organization|location|concept|date|...
    aliases: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    origin: str = ""               # skill name that produced this entity

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Relation:
    """A directed edge between two entities."""
    subject: str
    predicate: str                 # released|founded|located_in|...
    object: str
    confidence: float = 1.0
    origin: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Source:
    """Citation for a claim or piece of data."""
    kind: str = "unknown"          # pdf|web|image|audio|video|knowledge_graph|...
    uri: str = ""
    title: str = ""
    page: Optional[int] = None
    retrieved_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Artifact:
    """A side-output file produced by the skill."""
    kind: str = "json"             # json|csv|image|audio|pdf|...
    uri: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Main output envelope ───────────────────────────────────────────────

@dataclass
class SkillOutput:
    """
    The standardized contract every skill returns.

    Fields:
      status:        ok | partial | error | skipped
      confidence:    0.0–1.0 — how sure the skill is about its result
      summary:       1–3 sentence human-readable summary (for the LLM)
      entities:      structured things extracted (for Knowledge Graph)
      relations:     edges between entities
      sources:       citations (Pattern 1 source-grounding)
      artifacts:     side files produced
      warnings:      non-fatal issues
      metrics:       skill-specific counters (chars, time, tokens, ...)
      skill_name:    auto-filled by executor
      run_id:        auto-filled by executor
      timestamp:     auto-filled
      raw:           optional opaque blob (skill-specific) — discouraged
    """
    status: str = "ok"
    confidence: float = 0.0
    summary: str = ""
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    skill_name: str = ""
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    raw: Any = None

    # ── Serialization ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "schema": "skill_output/v2.0",
            "status": self.status,
            "confidence": round(self.confidence, 3),
            "summary": self.summary,
            "entities": [e.to_dict() if isinstance(e, Entity) else e for e in self.entities],
            "relations": [r.to_dict() if isinstance(r, Relation) else r for r in self.relations],
            "sources": [s.to_dict() if isinstance(s, Source) else s for s in self.sources],
            "artifacts": [a.to_dict() if isinstance(a, Artifact) else a for a in self.artifacts],
            "warnings": self.warnings,
            "metrics": self.metrics,
            "skill_name": self.skill_name,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "raw": self.raw,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    # ── Convenience ─────────────────────────────────────────────────────

    @classmethod
    def ok(cls, summary: str, **kw) -> "SkillOutput":
        return cls(status="ok", confidence=0.9, summary=summary, **kw)

    @classmethod
    def partial(cls, summary: str, **kw) -> "SkillOutput":
        return cls(status="partial", confidence=0.5, summary=summary, **kw)

    @classmethod
    def error(cls, summary: str, **kw) -> "SkillOutput":
        return cls(status="error", confidence=0.0, summary=summary, **kw)

    @classmethod
    def skipped(cls, summary: str, **kw) -> "SkillOutput":
        return cls(status="skipped", confidence=0.0, summary=summary, **kw)

    @classmethod
    def from_dict(cls, d: dict) -> "SkillOutput":
        """Tolerant constructor — accepts dicts that may use old/loose formats."""
        if not isinstance(d, dict):
            return cls.error(f"non-dict input: {type(d).__name__}")

        def _entity(x):
            if isinstance(x, Entity): return x
            if isinstance(x, dict): return Entity(**{k: x.get(k) for k in Entity.__dataclass_fields__ if k in x})
            return Entity(name=str(x))

        def _relation(x):
            if isinstance(x, Relation): return x
            if isinstance(x, dict): return Relation(**{k: x.get(k) for k in Relation.__dataclass_fields__ if k in x})
            return Relation(subject=str(x), predicate="?", object="?")

        def _source(x):
            if isinstance(x, Source): return x
            if isinstance(x, dict): return Source(**{k: x.get(k) for k in Source.__dataclass_fields__ if k in x})
            return Source(uri=str(x))

        def _artifact(x):
            if isinstance(x, Artifact): return x
            if isinstance(x, dict): return Artifact(**{k: x.get(k) for k in Artifact.__dataclass_fields__ if k in x})
            return Artifact(uri=str(x))

        return cls(
            status=d.get("status", "ok"),
            confidence=float(d.get("confidence", 0.0)),
            summary=d.get("summary", d.get("message", "")),
            entities=[_entity(x) for x in d.get("entities", [])],
            relations=[_relation(x) for x in d.get("relations", [])],
            sources=[_source(x) for x in d.get("sources", [])],
            artifacts=[_artifact(x) for x in d.get("artifacts", [])],
            warnings=list(d.get("warnings", [])),
            metrics=dict(d.get("metrics", {})),
            skill_name=d.get("skill_name", ""),
            run_id=d.get("run_id", uuid.uuid4().hex[:12]),
            timestamp=d.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S%z")),
            raw=d.get("raw", d.get("raw_output")),
        )


# ─── Validator (used by executor.py) ────────────────────────────────────

def validate_output(payload: Any) -> tuple[bool, str]:
    """
    Returns (ok, message). Called by executor before accepting a skill's result.
    Tolerant: accepts SkillOutput, dict, or JSON string.
    """
    if payload is None:
        return False, "skill returned None"
    if isinstance(payload, SkillOutput):
        return True, "ok"
    if isinstance(payload, str):
        try:
            json.loads(payload)
            return True, "ok (json string)"
        except json.JSONDecodeError as e:
            return False, f"non-JSON string: {e}"
    if isinstance(payload, dict):
        required = {"status"}
        missing = required - set(payload.keys())
        if missing:
            return False, f"missing required fields: {missing}"
        if payload["status"] not in {"ok", "partial", "error", "skipped"}:
            return False, f"invalid status: {payload['status']}"
        return True, "ok"
    return False, f"unsupported type: {type(payload).__name__}"


# ─── Quick self-test ────────────────────────────────────────────────────

if __name__ == "__main__":
    out = SkillOutput.ok(
        summary="Test output",
        entities=[Entity(name="GPT-4", type="model", origin="test")],
        relations=[Relation(subject="OpenAI", predicate="released", object="GPT-4")],
        sources=[Source(kind="web", uri="https://openai.com")],
        metrics={"time_ms": 42},
    )
    print(out.to_json())
    ok, msg = validate_output(out)
    print(f"validate: {ok} — {msg}")
