#!/usr/bin/env python3
"""
context_builder.py — Pattern 1 Brief Builder (v2.0)

Assembles a unified context brief for the LLM by merging outputs from multiple
skills, querying the memory graph, and ranking by relevance/confidence.

This is what the LLM reads BEFORE composing its answer.

Pipeline:
    user_message
        ↓
    [watcher]  → detects signals → dispatches skills in parallel
        ↓
    [skill outputs]  → each one a SkillOutput (skill_schema.py)
        ↓
    [context_builder]
        ├── merge entities/relations into memory_graph
        ├── rank skill outputs by confidence + relevance
        ├── query memory_graph for related context
        ├── detect contradictions (confidence voting)
        └── produce final brief
        ↓
    [LLM]  reads brief → composes answer

Output brief format:
    {
      "schema": "context_brief/v2.0",
      "user_message": "...",
      "detected_signals": [...],
      "skills_used": [...],
      "summary": "...",
      "entities": [...],          # from all skills, deduplicated
      "relations": [...],
      "memory": {                 # what we already knew
        "related_entities": [...],
        "related_relations": [...]
      },
      "sources": [...],           # all citations (Pattern 1)
      "confidence": 0.91,         # aggregate
      "contradictions": [...],    # if any skills disagreed
      "warnings": [...],
      "artifacts": [...]
    }
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Import sibling modules
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from memory_graph import MemoryGraph


@dataclass
class SkillResult:
    """Lightweight wrapper around a finished skill execution."""
    skill_name: str
    output: dict                  # SkillOutput dict
    started_at: float
    finished_at: float

    @property
    def confidence(self) -> float:
        return float(self.output.get("confidence", 0.0))

    @property
    def status(self) -> str:
        return self.output.get("status", "unknown")

    @property
    def duration_ms(self) -> int:
        return int((self.finished_at - self.started_at) * 1000)


class ContextBuilder:
    """Merges skill outputs into a single LLM-ready brief."""

    def __init__(self, db_path: Path | str = ".context/memory_graph.db"):
        self.graph = MemoryGraph(db_path)

    # ── Main API ───────────────────────────────────────────────────────

    def build(
        self,
        user_message: str,
        detected_signals: list[dict],
        skill_results: list[SkillResult],
        topic: Optional[str] = None,
    ) -> dict:
        """
        Build the unified context brief.

        Args:
            user_message:    raw user input
            detected_signals: what the watcher detected [{signal, skill, match}]
            skill_results:   finished skill executions
            topic:           optional topic to extract from memory graph
        """
        # 1. Ingest all skill outputs into the memory graph
        for r in skill_results:
            if r.status in ("ok", "partial"):
                self.graph.ingest_skill_output(r.output, skill_name=r.skill_name)

        # 2. Merge entities/relations across skills (deduplicate by name+type)
        merged_entities = self._merge_entities(skill_results)
        merged_relations = self._merge_relations(skill_results)
        merged_sources = self._merge_sources(skill_results)
        merged_artifacts = self._merge_artifacts(skill_results)
        merged_warnings = self._merge_warnings(skill_results)

        # 3. Query memory for related context (what we already knew)
        memory_context = {"related_entities": [], "related_relations": []}
        if topic:
            mem = self.graph.context_for(topic, max_entities=5, max_relations=10)
            memory_context["related_entities"] = mem.get("entities", [])
            memory_context["related_relations"] = mem.get("relations", [])
            memory_context["summary"] = mem.get("summary", "")

        # 4. Detect contradictions (same entity, conflicting properties)
        contradictions = self._detect_contradictions(skill_results)

        # 5. Compose summary — pick top-k skill summaries by confidence
        top_summaries = sorted(
            [(r.confidence, r.output.get("summary", "")) for r in skill_results if r.status in ("ok", "partial")],
            key=lambda x: -x[0],
        )[:3]
        summary = " | ".join(s for _, s in top_summaries if s)

        # 6. Aggregate confidence — weighted by per-skill confidence and status
        successful = [r for r in skill_results if r.status in ("ok", "partial")]
        if successful:
            agg_conf = sum(r.confidence for r in successful) / len(successful)
        else:
            agg_conf = 0.0

        return {
            "schema": "context_brief/v2.0",
            "brief_id": uuid.uuid4().hex[:12],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "user_message": user_message,
            "detected_signals": detected_signals,
            "skills_used": [
                {
                    "name": r.skill_name,
                    "status": r.status,
                    "confidence": r.confidence,
                    "duration_ms": r.duration_ms,
                }
                for r in skill_results
            ],
            "summary": summary,
            "entities": merged_entities,
            "relations": merged_relations,
            "memory": memory_context,
            "sources": merged_sources,
            "artifacts": merged_artifacts,
            "warnings": merged_warnings,
            "contradictions": contradictions,
            "confidence": round(agg_conf, 3),
        }

    # ── Mergers ────────────────────────────────────────────────────────

    @staticmethod
    def _merge_entities(results: list[SkillResult]) -> list[dict]:
        seen: dict[tuple[str, str], dict] = {}
        for r in results:
            for e in r.output.get("entities", []):
                if not isinstance(e, dict):
                    continue
                key = (e.get("name", "").lower(), e.get("type", "unknown"))
                if key in seen:
                    # merge — bump confidence, extend aliases
                    existing = seen[key]
                    existing["confidence"] = max(existing["confidence"], e.get("confidence", 1.0))
                    existing_aliases = set(existing.get("aliases", []))
                    existing_aliases.update(e.get("aliases", []))
                    existing["aliases"] = list(existing_aliases)
                    existing.setdefault("origins", []).append(r.skill_name)
                else:
                    e = dict(e)
                    e["origins"] = [r.skill_name]
                    seen[key] = e
        return list(seen.values())

    @staticmethod
    def _merge_relations(results: list[SkillResult]) -> list[dict]:
        seen: dict[tuple[str, str, str], dict] = {}
        for r in results:
            for rel in r.output.get("relations", []):
                if not isinstance(rel, dict):
                    continue
                key = (
                    rel.get("subject", "").lower(),
                    rel.get("predicate", "").lower(),
                    rel.get("object", "").lower(),
                )
                if key in seen:
                    existing = seen[key]
                    existing["confidence"] = max(existing["confidence"], rel.get("confidence", 1.0))
                    existing.setdefault("origins", []).append(r.skill_name)
                else:
                    rel = dict(rel)
                    rel["origins"] = [r.skill_name]
                    seen[key] = rel
        return list(seen.values())

    @staticmethod
    def _merge_sources(results: list[SkillResult]) -> list[dict]:
        out = []
        seen_uris = set()
        for r in results:
            for s in r.output.get("sources", []):
                if not isinstance(s, dict):
                    continue
                uri = s.get("uri", "")
                if uri and uri in seen_uris:
                    continue
                if uri:
                    seen_uris.add(uri)
                s = dict(s)
                s["origin_skill"] = r.skill_name
                out.append(s)
        return out

    @staticmethod
    def _merge_artifacts(results: list[SkillResult]) -> list[dict]:
        out = []
        for r in results:
            for a in r.output.get("artifacts", []):
                if not isinstance(a, dict):
                    continue
                a = dict(a)
                a["origin_skill"] = r.skill_name
                out.append(a)
        return out

    @staticmethod
    def _merge_warnings(results: list[SkillResult]) -> list[str]:
        out = []
        for r in results:
            for w in r.output.get("warnings", []):
                out.append(f"[{r.skill_name}] {w}")
        return out

    @staticmethod
    def _detect_contradictions(results: list[SkillResult]) -> list[dict]:
        """
        Heuristic: find entities with same name+type but conflicting property values
        across different skills. Reports them so the LLM can resolve.
        """
        by_key: dict[tuple[str, str], list[tuple[str, dict]]] = {}
        for r in results:
            for e in r.output.get("entities", []):
                if not isinstance(e, dict):
                    continue
                key = (e.get("name", "").lower(), e.get("type", "unknown"))
                by_key.setdefault(key, []).append((r.skill_name, e))

        contradictions = []
        for key, instances in by_key.items():
            if len(instances) < 2:
                continue
            # Compare property values
            prop_sets = [(skill, e.get("properties", {})) for skill, e in instances]
            for i, (s1, p1) in enumerate(prop_sets):
                for s2, p2 in prop_sets[i+1:]:
                    for k in set(p1.keys()) & set(p2.keys()):
                        if p1[k] != p2[k]:
                            contradictions.append({
                                "entity": key[0],
                                "property": k,
                                "values": [
                                    {"skill": s1, "value": p1[k]},
                                    {"skill": s2, "value": p2[k]},
                                ],
                            })
        return contradictions

    # ── Serialization ──────────────────────────────────────────────────

    def save_brief(self, brief: dict, path: Path | str = ".context/context_brief.json"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# ─── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    builder = ContextBuilder()

    # Demo: simulate two skill outputs merging
    skill_a = SkillResult(
        skill_name="web-search",
        output={
            "status": "ok",
            "confidence": 0.9,
            "summary": "OpenAI released GPT-4 in March 2023",
            "entities": [
                {"name": "OpenAI", "type": "organization", "confidence": 0.95},
                {"name": "GPT-4", "type": "model", "confidence": 0.93, "properties": {"released": "2023-03"}},
            ],
            "relations": [
                {"subject": "OpenAI", "predicate": "released", "object": "GPT-4", "confidence": 0.95},
            ],
            "sources": [{"kind": "web", "uri": "https://openai.com/blog/gpt-4"}],
            "warnings": [],
            "artifacts": [],
            "metrics": {},
        },
        started_at=time.time() - 1.2,
        finished_at=time.time(),
    )
    skill_b = SkillResult(
        skill_name="site-context-loader",
        output={
            "status": "ok",
            "confidence": 0.85,
            "summary": "OpenAI is headquartered in San Francisco",
            "entities": [
                {"name": "OpenAI", "type": "organization", "confidence": 0.9},
                {"name": "San Francisco", "type": "location", "confidence": 0.95},
            ],
            "relations": [
                {"subject": "OpenAI", "predicate": "headquartered_in", "object": "San Francisco", "confidence": 0.9},
            ],
            "sources": [{"kind": "geocoder", "uri": "nominatim:San Francisco"}],
            "warnings": [],
            "artifacts": [],
            "metrics": {},
        },
        started_at=time.time() - 0.8,
        finished_at=time.time(),
    )

    brief = builder.build(
        user_message="What do you know about OpenAI and GPT-4?",
        detected_signals=[
            {"signal": "company_name", "match": "OpenAI", "skill": "web-search"},
            {"signal": "model_name", "match": "GPT-4", "skill": "web-search"},
        ],
        skill_results=[skill_a, skill_b],
        topic="OpenAI",
    )
    print(json.dumps(brief, ensure_ascii=False, indent=2, default=str))
    print("\nMemory graph stats:", builder.graph.stats())
