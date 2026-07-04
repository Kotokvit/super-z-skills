#!/usr/bin/env python3
"""
source_grounded_brief.py — Pattern 1 (NotebookLM-style source grounding).

Provides a single shared helper that every Enricher skill calls to format its
output. Instead of returning loose `keywords: [...]`, skills return:

    {
      "brief": "1-3 line human-readable summary",
      "claims": [
        {
          "text": "claim in plain text",
          "source": "name of skill/source that produced it",
          "span": "human-readable locator (path:lines, URL, chunk id)",
          "confidence": 0.0-1.0,
          "tags": ["topic", "entity", ...]   # optional
        },
        ...
      ],
      "coverage": {
        "sources_used": 2,
        "sources_total": 1,
        "aspects_queried": ["theme", "keywords"],   # what the skill tried to extract
        "aspects_covered": ["theme", "keywords"],   # what actually got a citation
        "unanswered_aspects": [],                   # gap-detector feeds on this
        "transient": false                          # Pattern 5: privacy flag
      }
    }

Constraint built in (not algorithm):
  - Every claim MUST have source + span — otherwise the brief builder refuses it.
  - coverage.unanswered_aspects MUST be set — gap detector reads this.
  - If `transient=True`, the watcher will purge this entry when the session ends.

Usage:
    from patterns.source_grounded_brief import build_brief, Claim

    brief = build_brief(
        summary="🎧 media-triage: 3:33 YouTube video on commitment themes",
        claims=[
            Claim(text="Song's main theme is commitment", source="poler-toolkit",
                  span="transcript.txt:keywords[0]", confidence=0.95),
            ...
        ],
        aspects_queried=["theme", "keywords", "entities"],
        aspects_covered=["theme", "keywords"],
    )
    # → dict ready to drop into the skill's output envelope under `data`
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Claim:
    """One source-grounded claim. `source` and `span` are REQUIRED."""

    text: str
    source: str                  # which skill/source produced this claim
    span: str                    # human-readable locator (path:lines, url, index)
    confidence: float = 0.7      # 0.0-1.0
    tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.text or not self.text.strip():
            raise ValueError("Claim.text cannot be empty")
        if not self.source:
            raise ValueError("Claim.source is required (which skill/source produced it?)")
        if not self.span:
            raise ValueError(
                "Claim.span is required — every claim must point to a locator "
                "(path:lines, url, chunk_id, etc.)"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Claim.confidence must be 0.0-1.0, got {self.confidence}")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d.get("tags"):
            d.pop("tags", None)
        return d


def build_brief(
    summary: str,
    claims: List[Claim],
    aspects_queried: List[str],
    aspects_covered: Optional[List[str]] = None,
    sources_used: Optional[int] = None,
    sources_total: Optional[int] = None,
    transient: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a NotebookLM-style source-grounded brief dict.

    Args:
        summary: 1-3 line human-readable summary (will appear in agent's context).
        claims: list of Claim objects (each must have source+span).
        aspects_queried: what the skill tried to extract ("theme", "keywords", ...).
        aspects_covered: what actually got at least one citation. If None,
                         derived from claims' tags.
        sources_used: how many distinct sources contributed (default: 1).
        sources_total: total sources available (default: same as sources_used).
        transient: Pattern 5 — if True, watcher will purge after session ends.
        extra: any skill-specific extra fields to merge in (e.g., duration_sec,
               theme_name, language).

    Returns:
        Dict with keys: brief, claims, coverage, [extra fields...]
    """
    if not summary or not summary.strip():
        raise ValueError("summary cannot be empty — agent needs a 1-3 line digest")

    # Validate claims
    claim_dicts = [c.to_dict() if isinstance(c, Claim) else c for c in claims]

    # Derive aspects_covered from claim tags if not provided
    if aspects_covered is None:
        covered = set()
        for c in claim_dicts:
            for t in c.get("tags", []):
                covered.add(t)
        aspects_covered = sorted(covered)

    # Compute unanswered aspects — this is what the gap-detector reads
    unanswered = sorted(set(aspects_queried) - set(aspects_covered))

    if sources_used is None:
        # Count distinct sources across claims
        sources_used = len({c.get("source") for c in claim_dicts}) or 1
    if sources_total is None:
        sources_total = sources_used

    coverage = {
        "sources_used": sources_used,
        "sources_total": sources_total,
        "aspects_queried": list(aspects_queried),
        "aspects_covered": list(aspects_covered),
        "unanswered_aspects": unanswered,
        "transient": bool(transient),
    }

    out: Dict[str, Any] = {
        "brief": summary,
        "claims": claim_dicts,
        "coverage": coverage,
    }
    if extra:
        for k, v in extra.items():
            # Don't allow extra to clobber the canonical fields
            if k in ("brief", "claims", "coverage"):
                continue
            out[k] = v
    return out


def merge_briefs(briefs: List[Dict[str, Any]],
                 summary: Optional[str] = None) -> Dict[str, Any]:
    """Merge multiple source-grounded briefs into one.

    Useful when one skill calls several sub-skills (e.g., doc-triage calls
    pdf-ocr then poler-toolkit) and wants a single combined brief.
    """
    all_claims: List[Dict[str, Any]] = []
    aspects_q: set = set()
    aspects_c: set = set()
    sources_used = 0
    sources_total = 0
    transient_any = False
    extra_merged: Dict[str, Any] = {}

    for b in briefs:
        if not isinstance(b, dict):
            continue
        all_claims.extend(b.get("claims", []))
        cov = b.get("coverage", {})
        aspects_q.update(cov.get("aspects_queried", []))
        aspects_c.update(cov.get("aspects_covered", []))
        sources_used += cov.get("sources_used", 0)
        sources_total += cov.get("sources_total", 0)
        if cov.get("transient"):
            transient_any = True
        # Collect extra fields (anything not in canonical keys)
        for k, v in b.items():
            if k in ("brief", "claims", "coverage"):
                continue
            # Last-writer-wins for extras; could be more clever
            extra_merged[k] = v

    if summary is None:
        summaries = [b.get("brief", "") for b in briefs if isinstance(b, dict)]
        summary = " | ".join(s for s in summaries if s)

    return build_brief(
        summary=summary,
        claims=[Claim(**{k: v for k, v in c.items() if k in
                         ("text", "source", "span", "confidence", "tags")})
                for c in all_claims],
        aspects_queried=sorted(aspects_q),
        aspects_covered=sorted(aspects_c),
        sources_used=max(sources_used, 1),
        sources_total=max(sources_total, sources_used),
        transient=transient_any,
        extra=extra_merged or None,
    )


def validate_brief(brief: Dict[str, Any]) -> List[str]:
    """Return list of error messages (empty = valid).

    Used by validators to enforce the source-grounding constraint.
    """
    errs = []
    if not isinstance(brief, dict):
        return ["brief is not a dict"]
    if not brief.get("brief"):
        errs.append("missing top-level 'brief' (1-3 line summary)")
    claims = brief.get("claims")
    if not isinstance(claims, list) or not claims:
        errs.append("claims[] is empty — every brief must have ≥1 source-grounded claim")
    else:
        for i, c in enumerate(claims):
            if not isinstance(c, dict):
                errs.append(f"claims[{i}] is not a dict")
                continue
            for f in ("text", "source", "span"):
                if not c.get(f):
                    errs.append(f"claims[{i}].{f} is missing/empty — every claim MUST cite a source")
            conf = c.get("confidence", 1.0)
            if not (0.0 <= conf <= 1.0):
                errs.append(f"claims[{i}].confidence out of [0,1]: {conf}")
    cov = brief.get("coverage")
    if not isinstance(cov, dict):
        errs.append("coverage{} is missing")
    else:
        if "unanswered_aspects" not in cov:
            errs.append("coverage.unanswered_aspects missing — gap-detector needs this")
        if "transient" not in cov:
            errs.append("coverage.transient missing (Pattern 5)")
    return errs


# ─────────────────────────────────────────────────────────────────────
# CLI smoke test
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    b = build_brief(
        summary="🎧 demo brief",
        claims=[
            Claim(text="Test claim", source="test", span="test:1-2",
                  confidence=0.9, tags=["topic"]),
        ],
        aspects_queried=["topic", "entities"],
        aspects_covered=["topic"],
    )
    print(json.dumps(b, ensure_ascii=False, indent=2))

    errs = validate_brief(b)
    if errs:
        print("VALIDATION ERRORS:", errs, file=sys.stderr)
        sys.exit(1)
    print("\n✓ brief is valid", file=sys.stderr)
