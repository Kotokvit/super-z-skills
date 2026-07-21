#!/usr/bin/env python3
"""
planner.py — Builds a DAG of skills to execute for a given user query.

Combines rule-based matching with manifest triggers to choose which skills
to run, and in what order based on declared dependencies.

Pipeline stages (mirrors DeepSeek audit):
  1. Parse user query → extract intent keywords, file extensions, MIME types
  2. Match skills via registry.find_by_*()
  3. Resolve dependencies (transitively)
  4. Topologically sort by dependencies → DAG order
  5. (Optional) Split into parallel branches when independent

Usage (as a module):
    from planner import Planner
    from registry import SkillRegistry
    reg = SkillRegistry("/home/z/my-project/skills")
    planner = Planner(reg)
    plan = planner.plan("Проанализируй PDF и сделай отчёт", input_path="x.pdf")

CLI:
    python3 planner.py "query text" [--input FILE]
    python3 planner.py "query text" --json

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Make registry importable when run as script
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from registry import SkillRegistry  # noqa: E402


# Intent patterns: regex → list of skills to consider.
# These are HEURISTICS — the long-term plan is to let an LLM parse the
# intent, but rules are faster and require zero LLM calls.
INTENT_RULES: List[Tuple[re.Pattern, List[str]]] = [
    # "проанализируй PDF и сделай отчёт" → pdf-ocr → poler-toolkit → docx
    (re.compile(r"\b(проанализируй|analyze|анализ)\b.*\b(pdf|пдф)\b", re.IGNORECASE),
     ["pdf-ocr", "poler-toolkit"]),
    (re.compile(r"\b(отчёт|отчет|report)\b.*\b(график|chart|diagram)\b", re.IGNORECASE),
     ["poler-toolkit", "charts", "docx"]),
    (re.compile(r"\b(отчёт|отчет|report)\b", re.IGNORECASE),
     ["poler-toolkit", "docx"]),
    (re.compile(r"\b(презентация|presentation|слайды|slides|deck)\b", re.IGNORECASE),
     ["poler-toolkit", "pptx"]),
    (re.compile(r"\b(презентация|presentation|слайды|slides)\b.*\b(график|chart)\b", re.IGNORECASE),
     ["poler-toolkit", "charts", "pptx"]),
    (re.compile(r"\b(таблица|table|excel|spreadsheet|xlsx)\b", re.IGNORECASE),
     ["poler-toolkit", "xlsx"]),
    (re.compile(r"\b(поиск|search|find|найди)\b.*\b(онлайн|online|web|интернет)\b", re.IGNORECASE),
     ["web-search"]),
    (re.compile(r"\b(chat|чат|assistant|ассистент)\b", re.IGNORECASE),
     ["LLM"]),
    (re.compile(r"\b(image|картинк|фото|picture)\b.*\b(analyze|опис|describe)\b", re.IGNORECASE),
     ["VLM"]),
    (re.compile(r"\b(извлеки|extract)\b.*\b(pdf|пдф)\b", re.IGNORECASE),
     ["pdf-ocr"]),
    (re.compile(r"\b(браузер|browser|visit|open url)\b", re.IGNORECASE),
     ["agent-browser"]),
]


class Planner:
    """Builds a DAG of skills based on a user query and optional input file."""

    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        # v2.0 — capability-aware routing
        try:
            from capability_registry import CapabilityRegistry
            self.cap_registry = CapabilityRegistry(registry.skills_dir)
            self.cap_registry.load()
        except Exception:
            self.cap_registry = None
        try:
            from runtime_learning import RuntimeLearning
            self.tracker = RuntimeLearning()
        except Exception:
            self.tracker = None

    # -----------------------------------------------------------------
    # v2.0: Capability-aware planning
    # -----------------------------------------------------------------

    def plan_by_capability(
        self,
        capability: str,
        input_type: str = "",
        query: str = "",
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """
        v2.0 entry point: pick the best skill for a capability.

        Ranking factors (highest first):
          1. Runtime learning weight (EMA of past success)
          2. Manifest-declared confidence
          3. Resource cost (low cpu/ram wins)

        Returns a plan dict with a single skill, ready to execute.
        Falls back to the legacy rule-based plan() if no providers found.
        """
        if not self.cap_registry:
            return self.plan(query, verbose=verbose)

        providers = self.cap_registry.providers_for(capability, input_type)
        if not providers:
            return {
                "query": query,
                "capability": capability,
                "error": f"no providers for capability '{capability}'",
                "skills": [],
                "dag": [],
            }

        # Score each provider
        scored = []
        for p in providers:
            score = p.confidence
            if self.tracker:
                # weight from runtime learning (0..1)
                w = self.tracker.weight(p.skill_name, capability)
                # blend: 60% runtime, 40% manifest confidence
                score = 0.6 * w + 0.4 * p.confidence
            scored.append((score, p))
        scored.sort(key=lambda x: -x[0])

        best_score, best = scored[0]
        return {
            "query": query,
            "capability": capability,
            "input_type": input_type,
            "selected_skill": best.skill_name,
            "selected_confidence": round(best_score, 3),
            "alternatives": [
                {"skill": p.skill_name, "score": round(s, 3)}
                for s, p in scored[1:]
            ],
            "skills": [best.skill_name],
            "dag": [best.skill_name],
            "resources": best.resources,
        }

    def capabilities_for_query(self, query: str) -> List[str]:
        """
        Infer which capabilities a query needs, based on keywords.
        Returns a list of capability names (e.g., ['extract_text', 'summarize']).
        """
        q = query.lower()
        caps: List[str] = []
        rules = [
            (r"\b(pdf|пдф)\b",                      "extract_text"),
            (r"\b(image|фото|картинк\w*|png|jpg)\b", "analyze_image"),
            (r"\b(video|видео|mp4|youtube)\b",      "transcribe"),
            (r"\b(audio|аудио|mp3|wav)\b",          "transcribe"),
            (r"\b(переведи|translate)\b",           "translate"),
            (r"\b(реферат|summary|tl;?dr)\b",       "summarize"),
            (r"\b(график|chart|диаграмм\w*)\b",     "render_chart"),
            (r"\b(презентаци\w+|presentation|ppt)\b", "render_presentation"),
            (r"\b(таблиц\w+|spreadsheet|excel)\b",  "render_spreadsheet"),
            (r"\b(напиши\w*|write|сочини|создай)\b.*\b(код|code|скрипт)\b", "generate_code"),
            (r"\b(напиши\w*|write|сочини|создай)\b.*\b(пост|стат\w*|блог|article)\b", "generate_text"),
            (r"\b(поиск|search|find|найди)\b",      "search_web"),
            (r"\b(карт\w*|гео|координат\w*|map)\b", "geocode"),
            (r"\b(подар\w*|gift)\b",                "recommend"),
        ]
        for pattern, cap in rules:
            if re.search(pattern, q):
                caps.append(cap)
        return caps

    # -----------------------------------------------------------------
    # Public API (legacy, still works)
    # -----------------------------------------------------------------

    def plan(self, query: str, input_path: Optional[str] = None,
             verbose: bool = False) -> Dict[str, Any]:
        """Build an execution plan for a user query.

        Returns:
            {
              "query": "...",
              "input_path": "...",
              "intent_matches": ["poler-toolkit", "pdf-ocr"],
              "extension_matches": ["pdf-ocr"],
              "keyword_matches": ["poler-toolkit", "pdf-ocr"],
              "selected_skills": ["pdf-ocr", "poler-toolkit"],  # after dedup
              "dag": ["pdf-ocr", "poler-toolkit"],               # topologically sorted
              "parallel_branches": [["pdf-ocr"], ["poler-toolkit"]],  # if parallelizable
              "query_type": {...},  # Pattern 3 classification
              "rationale": "..."  # human-readable explanation
            }
        """
        # ── Pattern 3: classify FIRST, so routing can constrain the plan ──
        qtype = self.classify_query_type(query)
        routing = qtype["routing"]

        # If "undefined" with ask_user_if_ambiguous → return empty plan.
        # The agent should ask the user to clarify instead of wasting compute.
        if qtype["type"] == "undefined" and routing["ask_user_if_ambiguous"]:
            return {
                "query": query,
                "input_path": input_path,
                "intent_matches": [],
                "extension_matches": [],
                "keyword_matches": [],
                "selected_skills": [],
                "dag": [],
                "parallel_branches": [],
                "query_type": qtype,
                "rationale": (f"Query classified as 'undefined' ({qtype['rationale']}). "
                              f"ASK USER to clarify before running any skills."),
            }

        intent_matches = self._match_intent_rules(query)
        ext_matches: List[str] = []
        if input_path:
            ext = Path(input_path).suffix.lower()
            ext_matches = self.registry.find_by_extension(ext)
        kw_matches = self.registry.find_by_query(query)

        # Union all candidates
        candidates: Set[str] = set()
        candidates.update(intent_matches)
        candidates.update(ext_matches)
        candidates.update(kw_matches)

        # Filter to only those that exist in registry
        candidates = {s for s in candidates if self.registry.get_manifest(s)}

        # If we have an input file with an extension, FILTER OUT skills whose
        # triggers don't include that extension — unless they were matched
        # directly by intent rules or keywords (then keep them).
        # This prevents running pdf-ocr on a .md file just because poler-toolkit
        # declared it as a dependency.
        if input_path:
            ext = Path(input_path).suffix.lower()
            if ext:
                # Skills that explicitly trigger on this extension
                ext_skills = set(self.registry.find_by_extension(ext))
                # Skills matched by intent/keywords (keep regardless)
                intent_kw_skills = set(intent_matches) | set(kw_matches)
                # Filter: keep if skill is in ext_skills OR in intent_kw_skills
                # (we'll re-add dependencies later if needed)
                filtered = {s for s in candidates
                            if s in ext_skills or s in intent_kw_skills}
                if filtered:
                    candidates = filtered

        # ── Pattern 3: Apply routing constraints BEFORE dependency resolution ──
        # 1. allow_llm=False → strip LLM-heavy skills (LLM, gap-detector)
        # 2. allow_creative_pipeline=False → strip creative-generative skills
        #    (image-generation, video-generation, podcast-generate, TTS)
        # 3. After DAG is built, max_skills limits the total count.
        LLM_HEAVY = {"LLM", "gap-detector"}
        CREATIVE_GENERATORS = {"image-generation", "video-generation",
                               "podcast-generate", "TTS"}
        skipped_by_routing: List[str] = []
        if not routing["allow_llm"]:
            removed = {s for s in candidates if s in LLM_HEAVY}
            for s in removed:
                skipped_by_routing.append(f"{s} (LLM not allowed for {qtype['type']})")
            candidates -= removed
        if not routing["allow_creative_pipeline"]:
            removed = {s for s in candidates if s in CREATIVE_GENERATORS}
            for s in removed:
                skipped_by_routing.append(f"{s} (creative pipeline not allowed for {qtype['type']})")
            candidates -= removed

        # If nothing matched, default to poler-toolkit as the "general analyzer"
        if not candidates:
            if "poler-toolkit" in self.registry.list_skills():
                candidates = {"poler-toolkit"}
                rationale = "No specific triggers matched — defaulting to poler-toolkit (general analyzer)."
            else:
                rationale = "No skills matched and poler-toolkit not registered."
        else:
            rationale_parts = []
            if intent_matches:
                rationale_parts.append(f"intent rules → {intent_matches}")
            if ext_matches:
                rationale_parts.append(f"file extension → {ext_matches}")
            if kw_matches:
                rationale_parts.append(f"keywords → {kw_matches}")
            rationale = "Matched via: " + "; ".join(rationale_parts)

        # Sort by priority (desc), then alphabetical
        sorted_candidates = sorted(
            candidates,
            key=lambda s: (-self.registry.get_priority(s), s)
        )

        # Resolve dependencies transitively
        full_set = self._resolve_dependencies(sorted_candidates)

        # Topologically sort
        dag = self._topological_sort(full_set)

        # ── Pattern 3: enforce max_skills AFTER DAG is built ──
        # Topological order puts dependencies first, so trimming the END
        # keeps dependency chains intact.
        max_skills = routing["max_skills"]
        trimmed: List[str] = []
        if max_skills > 0 and len(dag) > max_skills:
            trimmed = dag[max_skills:]
            dag = dag[:max_skills]
            rationale += (f" | Pattern 3 trimmed DAG from {len(dag) + len(trimmed)} "
                          f"to {max_skills} skills (query_type={qtype['type']}). "
                          f"Trimmed: {trimmed}")

        # Identify parallel branches (skills with no inter-dependencies)
        parallel_branches = self._identify_parallel_branches(dag)

        if verbose:
            sys.stderr.write(f"[planner] candidates={sorted_candidates}\n")
            sys.stderr.write(f"[planner] with deps={full_set}\n")
            sys.stderr.write(f"[planner] DAG order={dag}\n")
            sys.stderr.write(f"[planner] parallel_branches={parallel_branches}\n")
            sys.stderr.write(f"[planner] query_type={qtype['type']} routing={routing}\n")
            if skipped_by_routing:
                sys.stderr.write(f"[planner] skipped_by_routing={skipped_by_routing}\n")

        return {
            "query": query,
            "input_path": input_path,
            "intent_matches": intent_matches,
            "extension_matches": ext_matches,
            "keyword_matches": kw_matches,
            "selected_skills": sorted_candidates,
            "dag": dag,
            "parallel_branches": parallel_branches,
            "query_type": qtype,
            "rationale": rationale,
        }

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    # Pattern 3: Adaptive query router
    # Classifies a user query into one of 4 types, each routed differently.
    #
    #   simple_fact  — "what is X?" / "capital of France?" / "when did Y happen?"
    #                  → 1 skill, poler-toolkit or web-search only, NO LLM
    #   synthesis    — "compare A and B" / "summarize this PDF" / "what are the
    #                  main arguments about X" / "проведи анализ через призму Y"
    #                  → 2-3 skills (extractor + analyzer + maybe LLM)
    #   creative     — "write a story" / "design a poster" / "compose a poem"
    #                  → full pipeline + LLM + Content Studio layer
    #   undefined    — ambiguous, short, or low-information queries
    #                  → ask user to clarify (avoid wasting compute)
    #
    # The classifier uses regex heuristics. Could be upgraded to LLM later,
    # but Pattern 3 from the synthesis doc recommends starting simple.
    #
    # RUSSIAN MORPHOLOGY NOTE: We use prefix-match patterns (without trailing
    # \b) for Russian words because Russian has 6+ declensions per noun.
    # "анализ" → "анализа/анализу/анализом/анализе/анализы/анализов/...". Using
    # \bанализ\b would miss all of these. Prefix match `\bанализ` catches them.

    SIMPLE_FACT_PATTERNS = [
        # "что такое X" / "what is X" / "define X" / "capital of X"
        re.compile(r"\b(?:что такое|what is|what's|определи|define|"
                   r"capital of|столица|когда|when did|где находится|where is|"
                   r"how many|how much)\b", re.IGNORECASE),
        # NOTE: Russian "сколько" is intentionally excluded — it's too
        # ambiguous. "Не столько X, сколько Y" is a comparison construct,
        # not a quantitative question. Quantitative Russian questions are
        # rare in this system's use cases.
        re.compile(r"\b\d{4}\b"),  # year — likely a "when" question
    ]
    SYNTHESIS_PATTERNS = [
        # Direct synthesis verbs — Russian prefix-match to handle declensions.
        # "сравни/сопоставь/проанализируй/анализ/анализа/анализу/..."
        re.compile(r"\b(?:сравни|сопостав|проанализ|анализ|"
                   r"compare|contrast|summarize|суммируй|резюмируй)\w*",
                   re.IGNORECASE),
        # "через призму X" / "в контексте Y" — academic framing markers
        re.compile(r"\b(?:через призму|в контексте|в рамках|"
                   r"сквозь призму|под углом|with respect to|in light of)\b",
                   re.IGNORECASE),
        # "междисциплинарн / многоуровнев / гетерогенн / теоретическ / эпистемолог"
        # — academic / interdisciplinary markers, all prefix-matched
        re.compile(r"\b(?:междисциплинарн|многоуровнев|гетерогенн|"
                   r"теоретическ|эпистемолог|архетип|семиотическ|"
                   r"феноменолог|онтолог|методолог)\w*", re.IGNORECASE),
        # "A and B and C" → comparison pattern (3+ conjunctions = synthesis)
        re.compile(r"\b(?:и|and|vs|versus)\b.{5,}\b(?:и|and|vs|versus)\b.{5,}\b(?:и|and|vs|versus)\b",
                   re.IGNORECASE),
        # "main arguments" / "key points" / "conclusions"
        re.compile(r"\b(?:главные|ключевые|выводы|итого|main arguments|"
                   r"key points|conclusions)\b", re.IGNORECASE),
    ]
    CREATIVE_PATTERNS = [
        # Creative verbs — Russian prefix-match
        re.compile(r"\b(?:напиши|write|compose|сочини|create|создай|"
                   r"design|спроектируй|draw|нарисуй|сгенерируй|generate|"
                   r"придумай|изобрети)\w*", re.IGNORECASE),
        # Creative output nouns
        re.compile(r"\b(?:story|poem|song|poster|presentation|slides|"
                   r"история|стих|песня|постер|презентация|слайды|"
                   r"сценарий|script|novel|роман)\w*", re.IGNORECASE),
    ]

    def classify_query_type(self, query: str) -> Dict[str, Any]:
        """Pattern 3: classify a query into simple_fact/synthesis/creative/undefined.

        Returns:
            {
              "type": "simple_fact" | "synthesis" | "creative" | "undefined",
              "confidence": 0.0-1.0,
              "matched_patterns": ["simple_fact:0", ...],
              "routing": {
                "max_skills": int,
                "allow_llm": bool,
                "allow_creative_pipeline": bool,
                "ask_user_if_ambiguous": bool
              },
              "rationale": "..."
            }
        """
        q = query.strip()
        matches: List[str] = []
        scores = {"simple_fact": 0, "synthesis": 0, "creative": 0}

        for i, pat in enumerate(self.SIMPLE_FACT_PATTERNS):
            if pat.search(q):
                scores["simple_fact"] += 1
                matches.append(f"simple_fact:{i}")
        for i, pat in enumerate(self.SYNTHESIS_PATTERNS):
            if pat.search(q):
                scores["synthesis"] += 1
                matches.append(f"synthesis:{i}")
        for i, pat in enumerate(self.CREATIVE_PATTERNS):
            if pat.search(q):
                scores["creative"] += 1
                matches.append(f"creative:{i}")

        # Length-based ambiguity check
        if len(q) < 10 or len(q.split()) < 2:
            # Very short / single-word query → undefined
            return {
                "type": "undefined",
                "confidence": 0.6,
                "matched_patterns": matches,
                "routing": {
                    "max_skills": 0,
                    "allow_llm": False,
                    "allow_creative_pipeline": False,
                    "ask_user_if_ambiguous": True,
                },
                "rationale": f"Query too short ({len(q)} chars) — ask user to clarify",
            }

        # Pick highest scoring type
        max_score = max(scores.values())
        if max_score == 0:
            return {
                "type": "undefined",
                "confidence": 0.4,
                "matched_patterns": matches,
                "routing": {
                    "max_skills": 1,
                    "allow_llm": False,
                    "allow_creative_pipeline": False,
                    "ask_user_if_ambiguous": False,
                },
                "rationale": "No patterns matched — defaulting to undefined (light routing)",
            }

        # Tie-breaking: when multiple types have equal max score, use query
        # length as a signal. Long queries (>80 chars) are almost never
        # simple_fact — they usually carry academic/synthesis/creative intent.
        # Tie order: creative > synthesis > simple_fact for long queries;
        #           simple_fact > synthesis > creative for short queries.
        is_long_query = len(q) > 80
        if is_long_query:
            priority_order = ["creative", "synthesis", "simple_fact"]
        else:
            priority_order = ["simple_fact", "synthesis", "creative"]

        # Find the highest-priority type among those tied at max_score
        qtype = next((t for t in priority_order if scores[t] == max_score), None)
        if qtype is None:
            qtype = "simple_fact"  # fallback (should not happen)

        # Routing per type
        if qtype == "simple_fact":
            routing = {
                "max_skills": 1,
                "allow_llm": False,
                "allow_creative_pipeline": False,
                "ask_user_if_ambiguous": False,
            }
            rationale = "simple_fact: route to single cheap skill (poler-toolkit/web-search), no LLM"
        elif qtype == "synthesis":
            routing = {
                "max_skills": 3,
                "allow_llm": True,
                "allow_creative_pipeline": False,
                "ask_user_if_ambiguous": False,
            }
            rationale = "synthesis: route to extractor + analyzer + LLM (max 3 skills)"
        else:  # creative
            routing = {
                "max_skills": 5,
                "allow_llm": True,
                "allow_creative_pipeline": True,
                "ask_user_if_ambiguous": False,
            }
            rationale = "creative: full pipeline + LLM + Content Studio layer"

        confidence = 0.5 + 0.15 * (max_score - 1) if max_score > 0 else 0.5
        confidence = min(0.95, confidence)

        return {
            "type": qtype,
            "confidence": round(confidence, 2),
            "matched_patterns": matches,
            "routing": routing,
            "rationale": rationale,
        }

    def _match_intent_rules(self, query: str) -> List[str]:
        """Try each INTENT_RULES pattern; collect all matches."""
        matches: List[str] = []
        for pattern, skills in INTENT_RULES:
            if pattern.search(query):
                for s in skills:
                    if s not in matches:
                        matches.append(s)
        return matches

    def _resolve_dependencies(self, skills: List[str]) -> Set[str]:
        """Transitively resolve dependencies. Returns the full closure."""
        result: Set[str] = set()
        queue = list(skills)
        while queue:
            s = queue.pop(0)
            if s in result:
                continue
            result.add(s)
            for dep in self.registry.get_dependencies(s):
                if dep not in result:
                    queue.append(dep)
        return result

    def _topological_sort(self, skills: Set[str]) -> List[str]:
        """Kahn's algorithm — order skills so dependencies come first.

        Adds two ordering heuristics beyond declared dependencies:
          1. INPUT skills (extractors like pdf-ocr) run before ANALYSIS skills
             (poler-toolkit, contentanalysis) — because analysis needs text.
          2. ANALYSIS skills run before OUTPUT skills (charts, docx, pdf, pptx,
             xlsx) — because output producers consume analysis results.
        """
        # Categories of skills (in pipeline order)
        INPUT_SKILLS = {"pdf-ocr", "agent-browser", "web-search", "web-reader",
                        "image-search", "ASR", "video-understand"}
        ANALYSIS_SKILLS = {"poler-toolkit", "poler-psi", "contentanalysis",
                           "VLM", "image-understand", "LLM"}
        OUTPUT_SKILLS = {"charts", "docx", "pdf", "pptx", "xlsx",
                         "TTS", "image-generation", "video-generation",
                         "podcast-generate"}

        def stage_of(s: str) -> int:
            if s in INPUT_SKILLS: return 0
            if s in ANALYSIS_SKILLS: return 1
            if s in OUTPUT_SKILLS: return 2
            return 3  # uncategorized

        # Build adjacency: dep → dependents
        in_degree: Dict[str, int] = {s: 0 for s in skills}
        adj: Dict[str, List[str]] = {s: [] for s in skills}
        for s in skills:
            for dep in self.registry.get_dependencies(s):
                if dep in skills:
                    adj[dep].append(s)
                    in_degree[s] += 1

        # Start with skills that have no in-degree
        queue = [s for s in skills if in_degree[s] == 0]

        result: List[str] = []
        while queue:
            # Re-sort the queue each iteration: stage first, then priority desc
            queue.sort(key=lambda s: (stage_of(s), -self.registry.get_priority(s), s))
            s = queue.pop(0)
            result.append(s)
            for dependent in adj[s]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # If there's a cycle, just append remaining skills
        if len(result) < len(skills):
            remaining = skills - set(result)
            sys.stderr.write(
                f"[planner] WARNING: dependency cycle detected among {remaining}; "
                f"appending in arbitrary order\n"
            )
            result.extend(sorted(remaining))

        return result

    def _identify_parallel_branches(self, dag: List[str]) -> List[List[str]]:
        """Group skills that can run in parallel based on dependencies.

        Simple approach: each skill starts a new "level" unless it depends on
        the immediately preceding skill. Returns list of levels (each level
        is a list of skills that can run in parallel).
        """
        if not dag:
            return []

        levels: List[List[str]] = []
        completed: Set[str] = set()

        # Greedy: at each step, find all skills whose deps are all in completed
        remaining = list(dag)
        while remaining:
            # Find all skills in remaining whose deps are satisfied
            ready = []
            for s in remaining:
                deps = self.registry.get_dependencies(s)
                if all(d in completed for d in deps):
                    ready.append(s)
            if not ready:
                # Cycle: just take the next one
                ready = [remaining[0]]
            levels.append(ready)
            for s in ready:
                remaining.remove(s)
                completed.add(s)

        return levels


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[3]
    default_skills = project_root / "skills"
    ap = argparse.ArgumentParser(description="Plan skill execution DAG")
    ap.add_argument("query", help="User query in natural language")
    ap.add_argument("--input", help="Input file path (for extension-based matching)")
    ap.add_argument("--skills-dir", default=str(default_skills))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--classify", action="store_true",
                    help="Pattern 3: classify query type (simple_fact/synthesis/creative/undefined) and exit")
    args = ap.parse_args()

    reg = SkillRegistry(args.skills_dir)
    planner = Planner(reg)

    if args.classify:
        result = planner.classify_query_type(args.query)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"\n  Query: {args.query!r}")
            print(f"  Type: {result['type']} (confidence={result['confidence']})")
            print(f"  Rationale: {result['rationale']}")
            print(f"  Routing: {result['routing']}")
            if result["matched_patterns"]:
                print(f"  Matched patterns: {result['matched_patterns']}")
            print()
        return 0

    plan = planner.plan(args.query, input_path=args.input, verbose=args.verbose)

    # Pattern 3: also classify and include in plan
    plan["query_type"] = planner.classify_query_type(args.query)

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(f"\n  Query: {plan['query']}")
        if plan["input_path"]:
            print(f"  Input: {plan['input_path']}")
        print(f"\n  Query type: {plan['query_type']['type']} (confidence={plan['query_type']['confidence']})")
        print(f"  Rationale: {plan['query_type']['rationale']}")
        print(f"\n  Plan rationale: {plan['rationale']}")
        print(f"\n  DAG order ({len(plan['dag'])} skills):")
        for i, s in enumerate(plan["dag"], 1):
            m = reg.get_manifest(s)
            print(f"    {i}. {s:30s} pri={m['priority']:3d}  [{m['category']}]")
        print(f"\n  Parallel branches: {len(plan['parallel_branches'])} levels")
        for i, branch in enumerate(plan["parallel_branches"], 1):
            print(f"    Level {i}: {branch}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
