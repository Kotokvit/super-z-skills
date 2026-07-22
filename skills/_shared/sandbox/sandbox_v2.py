#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sandbox_v2.py — Observer-POLER-Sandbox Hybrid Backend

ARCHITECTURE:
    Observer  → бинарные решения (какой навык? хватит ли? ~200 токенов/вызов)
    POLER     → динамическая обработка текста (без чтения всего текста, через резонанс)
    Sandbox   → генерация контента (1 вызов LLM, не 4)

KEY DIFFERENCE FROM v1 (4-agent sandbox):
    v1: Planner→Executor→Reviewer→Critic = 4 LLM calls, ~5000 tokens, 25-80s
    v2: Observer decides + 1 LLM call for content = 1-3 calls, ~500-1500 tokens, 3-15s

The POLER engine replaces the "перебор" (brute force) approach:
    - Instead of feeding ENTIRE skill docs to LLM, POLER extracts only resonant fragments
    - Observer decides IF generation is needed (binary: yes/no)
    - Only ONE LLM call generates content, using POLER-filtered context

This matches how modern AI agents work:
    - Claude: decides subtasks → executes each once
    - GPT: function calling → targeted execution
    - Kimi: planner → executor → done (no reviewer/critic loop)

Integration points:
    - Drop-in replacement for sandbox/backend.py
    - Compatible with bridge.py (same call_sandbox_chat() interface)
    - Observer from _orchestrator/scripts/observer.py
    - POLER from _shared/poler_enhanced.py

Author: Super-Z team, 2026-07-21
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# ─── Import POLER engine ────────────────────────────────────────────────
# Try multiple import paths for poler_enhanced
_POLER_IMPORTED = False
try:
    from poler_enhanced import PolerAnalyzer, tokenize, filter_pii, EMOTIONAL_MARKERS
    _POLER_IMPORTED = True
except ImportError:
    pass

# ─── Import LLM provider ───────────────────────────────────────────────
try:
    from llm_provider import LLMProvider, HostLLMProvider, MockLLMProvider
except ImportError:
    # Fallback: define minimal interface
    class LLMProvider:
        def chat(self, system_prompt, user_prompt, timeout_sec=60):
            return None
        def name(self):
            return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# POLER-based context extractor
# ═══════════════════════════════════════════════════════════════════════════

class PolerContextExtractor:
    """Extracts resonant fragments from skill docs using POLER engine.
    
    Instead of feeding the ENTIRE SKILL.md to the LLM (which wastes tokens),
    POLER finds only the fragments that resonate with the user's query.
    This is the "динамическая обработка" — no brute force, no reading everything.
    """
    
    def __init__(self, window_size: int = 3000, top_n: int = 3, phi_decay: float = 0.85):
        self.window_size = window_size
        self.top_n = top_n
        self.phi_decay = phi_decay
        self._analyzer = None
        if _POLER_IMPORTED:
            self._analyzer = PolerAnalyzer(
                window=window_size, phi=phi_decay, top=top_n
            )
    
    def extract(self, text: str, query: str) -> str:
        """Extract resonant fragments from text relevant to query.
        
        Returns a compressed context string with only the most relevant parts.
        If POLER is not available, falls back to keyword-based extraction.
        """
        if not text or not query:
            return text[:2000] if text else ""
        
        # Extract keywords from the query
        keywords = self._extract_keywords(query)
        
        if self._analyzer:
            return self._extract_with_poler(text, keywords)
        else:
            return self._extract_with_keywords(text, keywords)
    
    def _extract_keywords(self, query: str) -> List[str]:
        """Extract meaningful keywords from the user query."""
        # Remove common stopwords
        stop = {'напиши', 'напиши', 'сделай', 'покажи', 'расскажи', 'помоги',
                'создай', 'составь', 'подготовь', 'write', 'make', 'create',
                'про', 'о', 'об', 'на', 'для', 'и', 'в', 'с', 'по', 'к',
                'the', 'a', 'an', 'about', 'for', 'with', 'and', 'or'}
        words = re.findall(r'[\w]+', query.lower(), re.UNICODE)
        keywords = [w for w in words if w not in stop and len(w) > 2]
        return keywords[:5]  # Top 5 keywords
    
    def _extract_with_poler(self, text: str, keywords: List[str]) -> str:
        """Use POLER engine for dynamic extraction."""
        if not keywords:
            return text[:2000]
        
        # Run POLER for each keyword and collect top fragments
        all_fragments = []
        seen_positions = set()
        
        for kw in keywords[:3]:  # Max 3 keywords to avoid over-processing
            try:
                result = self._analyzer.analyze_text(text, kw)
                for w in result.get('top_by_epsilon', []):
                    pos = w.get('position', 0)
                    # Deduplicate by position (within 500 chars tolerance)
                    bucket = pos // 500
                    if bucket not in seen_positions:
                        seen_positions.add(bucket)
                        fragment = w.get('cleaned_text', '')
                        if fragment:
                            all_fragments.append({
                                'text': fragment[:1000],  # Cap each fragment
                                'epsilon': w.get('epsilon', 0),
                                'keyword': kw,
                            })
            except Exception:
                continue
        
        if not all_fragments:
            # Fallback: first 2000 chars
            return text[:2000]
        
        # Sort by epsilon (information density) and take top fragments
        all_fragments.sort(key=lambda f: f['epsilon'], reverse=True)
        selected = all_fragments[:self.top_n]
        
        # Build compressed context
        parts = []
        total_len = 0
        max_total = 3000  # Max 3000 chars of context (saves tokens)
        for frag in selected:
            if total_len + len(frag['text']) > max_total:
                remaining = max_total - total_len
                if remaining > 100:
                    parts.append(f"[{frag['keyword']}] {frag['text'][:remaining]}")
                break
            parts.append(f"[{frag['keyword']}] {frag['text']}")
            total_len += len(frag['text'])
        
        return "\n\n".join(parts) if parts else text[:2000]
    
    def _extract_with_keywords(self, text: str, keywords: List[str]) -> str:
        """Fallback: keyword-based extraction without POLER."""
        if not keywords:
            return text[:2000]
        
        # Find paragraphs containing any keyword
        paragraphs = text.split('\n\n')
        relevant = []
        other = []
        
        for para in paragraphs:
            para_lower = para.lower()
            if any(kw in para_lower for kw in keywords):
                relevant.append(para.strip())
            else:
                other.append(para.strip())
        
        # Take relevant paragraphs first, then fill with other
        result_parts = []
        total_len = 0
        max_total = 3000
        
        for para in relevant:
            if total_len + len(para) > max_total:
                break
            result_parts.append(para)
            total_len += len(para)
        
        # If we have room, add beginning paragraphs (methodology is usually at top)
        if total_len < max_total:
            for para in other[:3]:
                if total_len + len(para) > max_total:
                    break
                result_parts.append(para)
                total_len += len(para)
        
        return "\n\n".join(result_parts) if result_parts else text[:2000]


# ═══════════════════════════════════════════════════════════════════════════
# Observer — binary decision maker (from v2.1 observer.py, simplified)
# ═══════════════════════════════════════════════════════════════════════════

class Observer:
    """Lightweight binary observer for sandbox decisions.
    
    Makes ONE LLM call per decision. Returns binary results.
    This is the token-efficient replacement for the 4-agent chain.
    
    Each call: ~100-200 tokens input, ~20-50 tokens output = ~150-250 tokens total
    """
    
    def __init__(self, llm: LLMProvider):
        self.llm = llm
        self.log: List[Dict] = []
    
    def should_generate(self, query: str, skill_context: str) -> bool:
        """Observer: should we invoke LLM generation, or is context sufficient?
        
        Returns True if generation is needed, False if context alone answers the query.
        This replaces the Planner+Reviewer+Critic chain with a single binary decision.
        """
        system = (
            "You are an observer. Decide if the user's question requires NEW content generation, "
            "or if the provided context already contains the answer.\n"
            "Reply with exactly one word: YES (need generation) or NO (context is sufficient)."
        )
        
        # Truncate context to save tokens
        ctx_preview = skill_context[:500] if skill_context else "(empty)"
        
        user = (
            f"USER QUESTION: {query}\n\n"
            f"AVAILABLE CONTEXT:\n{ctx_preview}\n\n"
            "Does this question need new content generation? YES or NO?"
        )
        
        response = self.llm.chat(system, user)
        if not response:
            return True  # Default to generating when observer unavailable
        
        answer = response.strip().upper()[:3]
        decision = answer == "YES"
        
        self.log.append({
            "mode": "should_generate",
            "query": query[:100],
            "decision": decision,
        })
        
        return decision
    
    def is_sufficient(self, query: str, generated_content: str) -> bool:
        """Observer: is the generated content sufficient?
        
        Replaces Reviewer+Critic with one binary check.
        """
        system = (
            "You are an observer evaluating your own work. "
            "Decide if the generated content adequately answers the user's question.\n"
            "Reply with exactly one word: YES (sufficient) or NO (needs improvement)."
        )
        
        content_preview = generated_content[:500] if generated_content else "(empty)"
        
        user = (
            f"USER QUESTION: {query}\n\n"
            f"GENERATED CONTENT:\n{content_preview}\n\n"
            "Is this content sufficient? YES or NO?"
        )
        
        response = self.llm.chat(system, user)
        if not response:
            return True  # Accept on failure (don't loop)
        
        answer = response.strip().upper()[:3]
        return answer == "YES"
    
    def what_to_focus(self, query: str, content: str, issues: str = "") -> str:
        """Observer: what should be improved? (only called if content is insufficient)
        
        Returns a brief focus instruction for the next generation attempt.
        This replaces the Critic's detailed analysis with a concise direction.
        """
        system = (
            "You are an observer. The generated content needs improvement. "
            "In ONE short sentence, state what should be focused on or fixed."
        )
        
        user = (
            f"USER QUESTION: {query}\n"
            f"CONTENT PREVIEW: {content[:300]}\n"
            f"ISSUES: {issues[:200]}\n\n"
            "What should be focused on? One sentence only."
        )
        
        response = self.llm.chat(system, user)
        return response.strip()[:200] if response else "Be more specific and detailed"


# ═══════════════════════════════════════════════════════════════════════════
# SandboxV2 Backend — Observer + POLER + 1 LLM call
# ═══════════════════════════════════════════════════════════════════════════

class SandboxV2:
    """Token-efficient sandbox backend using Observer + POLER.
    
    ARCHITECTURE:
        1. POLER extracts resonant context from SKILL.md (0 LLM calls)
        2. Observer decides if generation is needed (1 LLM call, ~200 tokens)
        3. If yes: 1 LLM call generates content with POLER-filtered context
        4. Observer checks if result is sufficient (1 LLM call, ~200 tokens)
        5. If no: 1 more LLM call with focus instruction (loop max 2 times)
    
    TOKEN COMPARISON:
        v1 (4-agent):    4 full LLM calls ≈ 5000 tokens, 25-80s
        v2 (Observer+POLER): 1-3 binary LLM calls ≈ 500-1500 tokens, 3-15s
    
    SPEED COMPARISON:
        v1: planner(5s) + executor(10s) + reviewer(5s) + critic(5s) = 25s minimum
        v2: poler(0s, local) + observer(2s) + generate(8s) + check(2s) = 12s maximum
    """
    
    MAX_ITERATIONS = 1  # 1 generation attempt by default (fastest)
    
    def __init__(self, llm_provider: Optional[LLMProvider] = None,
                 skill_context: Optional[Dict] = None,
                 verbose: bool = False,
                 use_observer: bool = False,
                 max_iterations: int = 1):
        self.llm_provider = llm_provider or HostLLMProvider.from_zai_cli()
        self.skill_context = skill_context or {}
        self.verbose = verbose
        self.use_observer = use_observer  # Observer OFF by default for speed
        self.MAX_ITERATIONS = max_iterations
        
        # Components
        self.observer = Observer(self.llm_provider) if use_observer else None
        self.poler = PolerContextExtractor()
        
        # Stats
        self.trace: List[Dict] = []
        self._total_llm_calls = 0
    
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Optional[str]:
        """Main API: takes messages, returns text response.
        
        Same interface as v1 SandboxBackend.chat() — drop-in replacement.
        """
        start_time = time.time()
        
        # Extract query and system prompt from messages
        query = self._extract_query(messages)
        system_prompt = self._extract_system_prompt(messages)
        
        # Update skill context if system prompt present
        if system_prompt and not self.skill_context.get("skill_md"):
            self.skill_context["skill_md"] = system_prompt
        
        if not query:
            return None
        
        # Run the Observer-POLER loop
        result = self._run_loop(query)
        
        # Record trace
        elapsed = time.time() - start_time
        self.trace.append({
            "query": query[:200],
            "result_length": len(result) if result else 0,
            "elapsed_sec": round(elapsed, 2),
            "llm_calls": self._count_llm_calls(),
        })
        
        if self.verbose:
            print(f"[sandbox-v2] Done in {elapsed:.2f}s, "
                  f"{self._count_llm_calls()} LLM calls, "
                  f"{len(result) if result else 0} chars",
                  file=sys.stderr)
        
        return result
    
    def _run_loop(self, query: str) -> str:
        """Observer-POLER generation loop.
        
        FAST MODE (use_observer=False, default):
            POLER compresses context → 1 LLM call generates content
            Total: 1 LLM call, ~500-1500 tokens, 5-15s
        
        OBSERVER MODE (use_observer=True):
            Observer decides if generation needed → generate → observer checks
            Total: 1-3 LLM calls, ~500-2000 tokens, 8-25s
        
        Unlike v1's 4-agent chain (always 4 calls):
            - POLER compresses context locally (0 LLM calls)
            - Only 1 generation call (not 4)
            - Observer optional, not mandatory
        """
        skill_md = self.skill_context.get("skill_md", "")
        skill_name = self.skill_context.get("skill_name", "unknown")
        
        # ── Step 1: POLER extracts resonant context (0 LLM calls) ──
        compressed_context = self.poler.extract(skill_md, query)
        
        if self.verbose:
            original_len = len(skill_md)
            compressed_len = len(compressed_context)
            ratio = compressed_len / original_len if original_len > 0 else 0
            print(f"[sandbox-v2] POLER: {original_len} → {compressed_len} chars "
                  f"({ratio:.0%} compression)", file=sys.stderr)
        
        # ── Step 2 (optional): Observer decides if generation is needed ──
        if self.observer:
            needs_generation = self.observer.should_generate(query, compressed_context)
            self._total_llm_calls += 1
            
            if not needs_generation:
                if self.verbose:
                    print("[sandbox-v2] Observer: context sufficient, no generation needed",
                          file=sys.stderr)
                return compressed_context
        
        # ── Step 3: Generate content (1 LLM call with POLER-compressed context) ──
        current_content = ""
        focus = ""
        
        for attempt in range(1, self.MAX_ITERATIONS + 1):
            if self.verbose:
                print(f"[sandbox-v2] Generation attempt {attempt}/{self.MAX_ITERATIONS}",
                      file=sys.stderr)
            
            # Build the generation prompt with POLER-compressed context
            system_prompt = self._build_system_prompt(skill_name, compressed_context, focus)
            user_prompt = self._build_user_prompt(query, skill_name)
            
            # Single LLM call for generation
            generated = self.llm_provider.chat(system_prompt, user_prompt)
            self._total_llm_calls += 1
            
            if not generated:
                if attempt == self.MAX_ITERATIONS:
                    break
                focus = "Generate substantive content that directly addresses the request"
                continue
            
            current_content = generated
            
            # ── Step 4 (optional): Observer checks sufficiency ──
            if self.observer and attempt < self.MAX_ITERATIONS:
                is_sufficient = self.observer.is_sufficient(query, current_content)
                self._total_llm_calls += 1
                
                if is_sufficient:
                    break
                
                focus = self.observer.what_to_focus(query, current_content)
                self._total_llm_calls += 1
            else:
                # No observer — accept first generation
                break
        
        return current_content
    
    def _build_system_prompt(self, skill_name: str, context: str,
                             focus: str = "") -> str:
        """Build generation system prompt with compressed context."""
        parts = [
            f"You are the '{skill_name}' skill.",
            "Follow the methodology below strictly and generate real, substantive content.",
            "Respond in the user's language. Be specific and actionable.",
        ]
        
        if context:
            parts.append(f"\n--- SKILL METHODOLOGY (POLER-filtered) ---\n{context}\n--- END ---")
        
        if focus:
            parts.append(f"\nFOCUS FOR THIS ATTEMPT: {focus}")
        
        parts.append(
            "\nIMPORTANT: Write the actual content directly. "
            "Do NOT write meta-descriptions or placeholders. "
            "Do NOT ask clarifying questions — just produce the best answer you can."
        )
        
        return "\n\n".join(parts)
    
    def _build_user_prompt(self, query: str, skill_name: str) -> str:
        """Build generation user prompt."""
        return f"Apply the {skill_name} methodology to this request:\n\n{query}"
    
    def _extract_query(self, messages: List[Dict]) -> str:
        """Extract user query from messages."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        if messages:
            return messages[-1].get("content", "")
        return ""
    
    def _extract_system_prompt(self, messages: List[Dict]) -> str:
        """Extract system prompt from messages."""
        for msg in messages:
            if msg.get("role") == "system":
                return msg.get("content", "")
        return ""
    
    def _count_llm_calls(self) -> int:
        """Count total LLM calls (observer + generation)."""
        provider_calls = 0
        if hasattr(self.llm_provider, 'call_count'):
            provider_calls = self.llm_provider.call_count
        # Also count observer calls from log
        observer_calls = len(self.observer.log) if self.observer else 0
        return max(provider_calls, self._total_llm_calls + observer_calls)
    
    def stats(self) -> Dict:
        """Return execution statistics."""
        total_queries = len([t for t in self.trace if t.get("query")])
        total_llm = self._count_llm_calls()
        
        return {
            "total_queries": total_queries,
            "total_llm_calls": total_llm,
            "observer_decisions": len(self.observer.log) if self.observer else 0,
            "llm_provider": self.llm_provider.name() if hasattr(self.llm_provider, 'name') else "unknown",
            "trace": self.trace[-10:],
            "observer_log": self.observer.log[-10:] if self.observer else [],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Bridge function — drop-in replacement for call_sandbox_chat()
# ═══════════════════════════════════════════════════════════════════════════

_backend_instance: Optional[SandboxV2] = None


def call_sandbox_v2_chat(
    system_prompt: str,
    user_prompt: str,
    skill_name: str = "",
    skill_md: str = "",
    timeout_sec: int = 120,
    verbose: bool = False,
    llm_provider: Optional[LLMProvider] = None,
) -> Optional[str]:
    """Drop-in replacement for call_sandbox_chat() from bridge.py.
    
    Uses Observer+POLER instead of 4-agent chain.
    Same interface, less tokens, faster.
    """
    global _backend_instance
    
    try:
        skill_context = {
            "skill_name": skill_name,
            "skill_md": skill_md or system_prompt,
        }
        
        # Create or reuse backend
        if _backend_instance is None:
            _backend_instance = SandboxV2(
                llm_provider=llm_provider,
                skill_context=skill_context,
                verbose=verbose,
            )
        else:
            _backend_instance.skill_context = skill_context
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        
        result = _backend_instance.chat(messages)
        
        if result and verbose:
            stats = _backend_instance.stats()
            print(f"[sandbox-v2] Stats: {stats['total_llm_calls']} LLM calls, "
                  f"{stats['observer_decisions']} observer decisions, "
                  f"provider: {stats['llm_provider']}",
                  file=sys.stderr)
        
        return result
    
    except Exception as e:
        if verbose:
            print(f"[sandbox-v2] Error: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CLI — for testing
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Sandbox V2 — Observer+POLER Backend")
    parser.add_argument("query", help="User query to test")
    parser.add_argument("--skill", default="blog-writer", help="Skill name")
    parser.add_argument("--skill-dir", default=None, help="Skill directory (for SKILL.md)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--backend", choices=["v2", "v1", "mock"], default="v2")
    args = parser.parse_args()
    
    # Read skill MD if available
    skill_md = ""
    if args.skill_dir:
        skill_path = Path(args.skill_dir) / "SKILL.md"
        if skill_path.exists():
            skill_md = skill_path.read_text(encoding="utf-8")
    
    if args.backend == "mock":
        # Mock: no LLM calls
        print(f"[MOCK] Query: {args.query}")
        print(f"[MOCK] Skill: {args.skill}")
        sys.exit(0)
    
    if args.backend == "v1":
        # Use original sandbox backend
        try:
            from backend import SandboxBackend
            provider = HostLLMProvider.from_zai_cli()
            backend_v1 = SandboxBackend(
                llm_provider=provider,
                skill_context={"skill_name": args.skill, "skill_md": skill_md},
                verbose=args.verbose,
            )
            messages = [{"role": "user", "content": args.query}]
            result = backend_v1.chat(messages)
            stats = backend_v1.stats()
            print(f"\n=== V1 RESULT ===")
            if result:
                print(result[:500])
            print(f"\n=== V1 STATS ===")
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        except ImportError as e:
            print(f"Cannot import v1 backend: {e}")
        sys.exit(0)
    
    # V2: Observer + POLER
    provider = HostLLMProvider.from_zai_cli()
    backend_v2 = SandboxV2(
        llm_provider=provider,
        skill_context={"skill_name": args.skill, "skill_md": skill_md},
        verbose=args.verbose,
    )
    
    messages = [{"role": "user", "content": args.query}]
    result = backend_v2.chat(messages)
    stats = backend_v2.stats()
    
    print(f"\n{'='*60}")
    print(f"  Sandbox V2 — Observer + POLER")
    print(f"{'='*60}")
    print(f"\nResult ({len(result) if result else 0} chars):")
    if result:
        print(result[:800])
    print(f"\nStats:")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
