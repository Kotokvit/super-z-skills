#!/usr/bin/env python3
"""
watcher.py — Conversation Watcher (online context layer).

Watches each incoming user message, detects signals via manifest triggers,
and runs matching skills in background threads. Results accumulate in a
"context_brief.json" file the agent reads BEFORE answering.

This is the "online layer" complement to the CLI orchestrator:
  - orchestrator.py: explicit user request → build DAG → execute → report
  - watcher.py:      passive observation → trigger-matched skills → context brief

Architecture (mapping to GPT's 6 module types):

  ┌──────────────────────────────────────────────────────────────┐
  │  Sensors        — _detect_signals()                          │
  │                   regex/keyword/url-pattern detection        │
  │                   produces Signal events                     │
  ├──────────────────────────────────────────────────────────────┤
  │  Observers      — _match_skills_to_signals()                │
  │                   registry.find_by_query() + ext/mime check  │
  │                   decides WHICH skills care about this msg   │
  ├──────────────────────────────────────────────────────────────┤
  │  Reasoners      — (future: LLM gap detector, planner)        │
  │                   currently: simple priority + dedup         │
  ├──────────────────────────────────────────────────────────────┤
  │  Executors      — self.executor (existing Executor class)    │
  │                   runs skill subprocess, validates output    │
  ├──────────────────────────────────────────────────────────────┤
  │  Memory         — context_brief.json (rolling, last N msgs)  │
  │                   entity index (future)                      │
  │                   skill cache (transcripts, OCR results)     │
  └──────────────────────────────────────────────────────────────┘

Usage:
    # As a module (called by orchestrator --watch mode):
    from watcher import ConversationWatcher
    w = ConversationWatcher(skills_dir)
    w.process_message("посмотри это https://youtube.com/watch?v=xxx")

    # CLI standalone:
    python3 watcher.py --stdin                    # interactive stdin loop
    python3 watcher.py --process "your message"   # one-shot
    python3 watcher.py --brief                    # print current context_brief

Author: Online Layer build, 2026-07-04
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor

# Make sibling modules importable
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from registry import SkillRegistry  # noqa: E402
from executor import Executor  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Default paths
# ─────────────────────────────────────────────────────────────────────

def _resolve_repo_root() -> Path:
    env_value = os.getenv("SUPER_Z_REPO_ROOT")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


DEFAULT_SKILLS_DIR = Path(os.getenv("SUPER_Z_SKILLS_DIR") or (_resolve_repo_root() / "skills")).expanduser().resolve()
DEFAULT_BRIEF_FILE = Path(os.getenv("SUPER_Z_BRIEF_FILE") or (_resolve_repo_root() / ".context" / "context_brief.json")).expanduser().resolve()
DEFAULT_BRIEF_FILE.parent.mkdir(parents=True, exist_ok=True)

# How many recent entries to keep in context_brief
MAX_BRIEF_ENTRIES = 50

# Skills that are explicitly CLI-only (not safe for background triggering)
# These would be skills that produce large outputs, require user input, etc.
BLOCKED_SKILLS = {"docx", "pdf", "pptx", "xlsx", "charts",
                  "image-generation", "video-generation", "TTS",
                  "podcast-generate", "image-edit", "image-search",
                  "skill-creator", "writing-plans",
                  "stock-analysis-skill",
                  "web-shader-extractor"}


# ─────────────────────────────────────────────────────────────────────
# Signal detection
# ─────────────────────────────────────────────────────────────────────

# Pre-compiled signal patterns (the "Sensors" layer)
SIGNAL_PATTERNS = {
    # ─── Media URL signals ─────────────────────────────────────────────
    "youtube_url": re.compile(
        r'https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)[A-Za-z0-9_-]{6,}',
        re.IGNORECASE),
    "soundcloud_url": re.compile(
        r'https?://(?:www\.)?soundcloud\.com/[^\s]+', re.IGNORECASE),
    "vimeo_url": re.compile(
        r'https?://(?:www\.)?vimeo\.com/[^\s]+', re.IGNORECASE),
    "direct_media_url": re.compile(
        r'https?://[^\s]+\.(?:mp3|wav|m4a|mp4|webm|aac|flac|ogg)', re.IGNORECASE),

    # ─── Geographic signals ────────────────────────────────────────────
    "toponym_ru": re.compile(
        r'\b(?i:(?:возле|около|под|вблизи|рядом\s+с|under|near))\s+([А-ЯЁ][а-яё]+(?:[\s\-][А-ЯЁ][а-яё]+)?)',
        re.UNICODE),
    "geocoords": re.compile(
        r'\b\d{1,3}\.\d+,\s*\d{1,3}\.\d+\b'),

    # ─── Attachment signals ────────────────────────────────────────────
    "pdf_attachment": re.compile(
        r'\b[\w/\\-]+\.pdf\b', re.IGNORECASE),
    "docx_attachment": re.compile(
        r'\b[\w/\\-]+\.docx?\b', re.IGNORECASE),
    "image_attachment": re.compile(
        r'\b[\w/\\-]+\.(?:png|jpg|jpeg|gif|webp|bmp)\b', re.IGNORECASE),

    # ─── Content-creation signals (NEW — for the 10 wrapped skills) ────
    # Each pattern is intentionally narrow to avoid false-positives.
    # We match on verb+noun combos that strongly indicate the skill's intent.
    # NOTE: Russian declensions handled with \w* suffix (e.g., "стат" matches
    # "статья/статью/статьей", "рынк" matches "рынок/рынка/рынку").
    "blog_writing_request": re.compile(
        r'\b(?:напиши|write|сочини|создай|сделай)\b[^.!?]{0,30}\b(?:пост|стат\w*|блог|article|blog|post)\b',
        re.IGNORECASE),
    "seo_writing_request": re.compile(
        r'\bSEO\s+(?:стат\w*|article|post|текст\w*|content)|оптимизируй\s+под\s+SEO|SEO-оптимиз\w*',
        re.IGNORECASE),
    "resume_request": re.compile(
        r'\b(?:состав[ьи]|напиши|сделай|create|write|build)\s+(?:резюме|CV|resume)|оцени\s+(?:мо[её]\s+)?резюме|проверь\s+резюме',
        re.IGNORECASE),
    "jd_tailor_request": re.compile(
        r'\b(?:под\s+ваканси\w*|под\s+JD|подгоня\w*|tailor\s+(?:resume|CV)|адаптируй\s+резюме)',
        re.IGNORECASE),
    "interview_prep_request": re.compile(
        r'\b(?:подготов[ьи]\s+(?:меня\s+)?к\s+собеседован|подготов[ьи]\s+к\s+interview|interview\s+prep|пройд\w+\s+собеседован\w*|повтор\w+\s+(?:перед\s+)?собеседован\w*|хочу\s+подготов\w*\s+к\s+собеседован)',
        re.IGNORECASE),
    "quiz_request": re.compile(
        r'\b(?:создай|сделай|generate|create)\s+(?:тест|квиз|quiz|вопросник)|тест\s+по\s+\w+|квиз\s+по\s+\w+',
        re.IGNORECASE),
    "dream_interpretation_request": re.compile(
        r'\bприсни\w*|толкован\w*\s+сн\w*|разбер\w+\s+со\s+сн\w*|interpret\s+(?:my\s+)?dream|сон\s+(?:присни|во\ss+котором|где)',
        re.IGNORECASE),
    "market_research_request": re.compile(
        r'\bанализ\s+рынк\w*|market\s+(?:research|analysis)|исследован\w*\s+рынк\w*|конкурент\w*\s+анализ|анализ\s+конкурент\w*',
        re.IGNORECASE),
    "storyboard_request": re.compile(
        r'\b(?:сценарий|storyboard|раскадровк\w*|напиши\s+(?:истори\w*|рассказ|story|сценарий))',
        re.IGNORECASE),
    "study_buddy_request": re.compile(
        r'\b(?:помоги\s+(?:выучить|подготов\w*\s+к\s+экзамен|изучить|разобраться\s+с)|объясни\s+тему\s+по\s+\w+|study\s+buddy|разбер[иу]\s+(?:тему|концепци\w*|материал))',
        re.IGNORECASE),

    # ─── Tier-2 content/analytical signals (added in watcher-expansion v2) ─
    "marketing_campaign_request": re.compile(
        r'\b(?:маркетинг\w*|campaign|кампани\w*|реклам\w*\s+(?:кампан|стратег)|ad\s*copy|копирайт\s+для\s+реклам|продвижени\w*)',
        re.IGNORECASE),
    "gift_request": re.compile(
        r'\b(?:что\s+подарить|иде[ия]\s+подарк\w*|подбери\s+подар|gift\s+ideas|present\s+for|подарок\s+для)',
        re.IGNORECASE),
    "uiux_request": re.compile(
        r'\b(?:UI[\s/-]?UX|UX[\s/-]?дизайн|интерфейс\s+(?:программ|приложен|сайта)|wireframe|прототип\s+интерфейса|юзабилити|usability)',
        re.IGNORECASE),
    "anti_pua_request": re.compile(
        r'\b(?:манипуляц\w*|газлайт\w*|токсичн\w*\s+(?:отношен|человек|началь)|нарцисс|abuse|PUA|ков\w*\s+услов\w*|обесцен\w*)',
        re.IGNORECASE),
    "code_help_request": re.compile(
        r'\b(?:напиши\s+(?:функци\w*|класс|скрипт|код|программ|function|class)|исправь\s+(?:код|баг|ошибку)|debug\s+(?:код|программ)|refactor\s+(?:код|этот)|оптимизируй\s+код)',
        re.IGNORECASE),
    "content_strategy_request": re.compile(
        r'\b(?:контент[\s-]*план|content\s+strategy|контент[\s-]*стратег|редакцион\w*|editorial\s+calendar|контент[\s-]*календар)',
        re.IGNORECASE),
    "content_analysis_request": re.compile(
        r'\b(?:анализ\s+контент\w*|проанализируй\s+(?:текст|стат\w*|контент)|content\s+audit|разбер[иу]\s+текст|оцени\s+(?:текст|стат\w*))',
        re.IGNORECASE),
    "cheat_sheet_request": re.compile(
        r'\b(?:шпаргал\w*|cheat\s*sheet|reference\s+card|кратк\w*\s+свод\w*|выжим\w*|summary\s+card)',
        re.IGNORECASE),
    "finance_request": re.compile(
        r'\b(?:бюджет\w*|распредели\s+бюджет|инвест\w*|финансов\w*\s+(?:план|анализ|отчёт)|budget\s+plan|portfolio\s+allocation)',
        re.IGNORECASE),
    "web_browse_request": re.compile(
        r'\b(?:открой\s+сайт|зайди\s+на\s+сайт|посет\w*\s+сайт|scrape\s+(?:website|url)|спарс\w*|собер[иу]\s+данные\s+с\s+сайта)',
        re.IGNORECASE),
    "design_request": re.compile(
        r'\b(?:дизайн\s+(?:логотип\w*|макет\w*|визуал\w*|обложк\w*)|сделай\s+макет|design\s+(?:logo|mockup|layout)|color\s+palette|цветов\w*\s+палитр)',
        re.IGNORECASE),

    # ─── Special: length-based ─────────────────────────────────────────
    "long_message": None,  # special: length-based
}

# Map signal types to skill names (used by _match_skills_to_signals)
SIGNAL_TO_SKILL = {
    "youtube_url": "media-triage",
    "soundcloud_url": "media-triage",
    "vimeo_url": "media-triage",
    "direct_media_url": "media-triage",
    "pdf_attachment": "doc-triage",
    "docx_attachment": "doc-triage",
    "image_attachment": "image-understand",
    "geocoords": "site-context-loader",
    "toponym_ru": "site-context-loader",
    "blog_writing_request": "blog-writer",
    "seo_writing_request": "seo-content-writer",
    "resume_request": "resume-builder",
    "jd_tailor_request": "jd-resume-tailor",
    "interview_prep_request": "interview-prep",
    "quiz_request": "quiz-mastery",
    "dream_interpretation_request": "dream-interpreter",
    "market_research_request": "market-research-reports",
    "storyboard_request": "storyboard-manager",
    "study_buddy_request": "study-buddy",
    # Tier-2 mappings (added in watcher-expansion v2)
    "marketing_campaign_request": "marketing-mode",
    "gift_request": "gift-evaluator",
    "uiux_request": "ui-ux-pro-max",
    "anti_pua_request": "anti-pua",
    "code_help_request": "coding-agent",
    "content_strategy_request": "content-strategy",
    "content_analysis_request": "contentanalysis",
    "cheat_sheet_request": "cheat-sheet",
    "finance_request": "finance",
    "web_browse_request": "agent-browser",
    "design_request": "design",
}


def detect_signals(message: str) -> List[Dict[str, Any]]:
    """Sensor layer: detect all signal types in a message.

    Returns list of {type, value, position} dicts.
    """
    signals: List[Dict[str, Any]] = []

    for sig_type, pattern in SIGNAL_PATTERNS.items():
        if sig_type == "long_message":
            if len(message) > 2000:
                signals.append({
                    "type": "long_message",
                    "value": len(message),
                    "position": 0,
                })
            continue
        if pattern is None:
            continue
        for m in pattern.finditer(message):
            signals.append({
                "type": sig_type,
                "value": m.group(0),
                "position": m.start(),
            })

    return signals


# ─────────────────────────────────────────────────────────────────────
# ConversationWatcher — the main class
# ─────────────────────────────────────────────────────────────────────

class ConversationWatcher:
    """Watches user messages, triggers matching skills in background."""

    def __init__(self, skills_dir: Path = DEFAULT_SKILLS_DIR,
                 context_brief_file: Path = DEFAULT_BRIEF_FILE,
                 verbose: bool = False,
                 max_workers: int = 2,
                 session_id: Optional[str] = None,
                 transient: bool = False,
                 transient_ttl_sec: int = 3600):
        """
        Args:
            session_id: optional session identifier. If set, entries tagged
                        with this session_id can be purged on shutdown.
            transient: Pattern 5 — if True, mark all new entries with this
                       session_id and an expires_at timestamp. They will be
                       purged by purge_expired() or on shutdown().
            transient_ttl_sec: how long transient entries live (default 1h).
        """
        self.skills_dir = Path(skills_dir)
        self.context_brief_file = Path(context_brief_file)
        self.verbose = verbose
        self.registry = SkillRegistry(self.skills_dir)
        self.executor = Executor(self.registry, verbose=verbose)
        # Background thread pool — at most 2 skills running in parallel
        # to avoid swamping CPU/network
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="watcher-worker")
        # Track running skills to avoid duplicate runs of same skill on
        # the same input
        self._running: Set[str] = set()
        self._running_lock = threading.Lock()
        # Pattern 5: transience
        import uuid
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.transient = bool(transient)
        self.transient_ttl_sec = int(transient_ttl_sec)

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def process_message(self, message: str,
                        file_attachments: Optional[List[Dict]] = None,
                        ) -> Dict[str, Any]:
        """Process a user message — detect signals, dispatch skills.

        Returns immediately with a "dispatch report". Actual skill results
        land in context_brief.json when background tasks finish.
        """
        if self.verbose:
            sys.stderr.write(
                f"\n[watcher] message ({len(message)} chars): "
                f"{message[:80]!r}...\n"
            )

        # 1. Sensor layer: detect signals
        signals = detect_signals(message)
        if file_attachments:
            for att in file_attachments:
                signals.append({
                    "type": f"file:{att.get('ext', 'unknown')}",
                    "value": att.get("path", ""),
                    "position": -1,
                })

        if not signals:
            if self.verbose:
                sys.stderr.write("[watcher] no signals detected, skipping\n")
            return {"dispatched": [], "signals": [], "reason": "no signals"}

        # 2. Observer layer: match skills to signals
        skills_to_run = self._match_skills_to_signals(signals, message)

        if not skills_to_run:
            if self.verbose:
                sys.stderr.write(
                    f"[watcher] {len(signals)} signals but no skills matched\n"
                )
            return {"dispatched": [], "signals": signals,
                    "reason": "no matching skills"}

        if self.verbose:
            sys.stderr.write(
                f"[watcher] dispatching {len(skills_to_run)} skill(s): "
                f"{skills_to_run}\n"
            )

        # 3. Executor layer: dispatch in background
        dispatched = []
        for skill_name, input_data in skills_to_run:
            dispatch_key = f"{skill_name}:{input_data.get('input', '')[:50]}"
            with self._running_lock:
                if dispatch_key in self._running:
                    if self.verbose:
                        sys.stderr.write(
                            f"[watcher] ⏭ {skill_name} already running on same input, skipping\n"
                        )
                    continue
                self._running.add(dispatch_key)

            # Submit to background pool
            future = self._pool.submit(self._run_skill_safely, skill_name,
                                        input_data, dispatch_key, message)
            dispatched.append({
                "skill": skill_name,
                "input": input_data.get("input", "")[:100],
                "future_submitted": True,
            })

        return {
            "dispatched": dispatched,
            "signals": signals,
            "reason": "ok",
        }

    def get_context_brief(self) -> Dict[str, Any]:
        """Read current context_brief.json. Returns empty dict if not exists."""
        if not self.context_brief_file.exists():
            return {"entries": [], "entities": {}}
        try:
            return json.loads(self.context_brief_file.read_text(encoding="utf-8"))
        except Exception:
            return {"entries": [], "entities": {}}

    def format_brief_for_agent(self, max_entries: int = 5) -> str:
        """Format the context_brief as a compact string for the agent.

        This is what the agent should see BEFORE composing its reply.
        Supports both Pattern 1 (source-grounded brief with claims+coverage)
        and legacy format (just keywords/brief text).
        """
        brief = self.get_context_brief()
        entries = brief.get("entries", [])
        if not entries:
            return ""

        # Take the most recent entries (last N)
        recent = entries[-max_entries:]
        lines = []
        lines.append("─" * 60)
        lines.append(f"📋 CONTEXT BRIEF ({len(recent)} recent entr{'y' if len(recent)==1 else 'ies'} from background skills)")
        lines.append("─" * 60)
        for entry in recent:
            ts = entry.get("timestamp", 0)
            ts_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"
            for skill_name, result in entry.get("results", {}).items():
                status = result.get("status", "unknown")
                icon = {"success": "✓", "error": "✗",
                        "skipped": "⏭"}.get(status, "?")
                data = result.get("data") or {}
                # Pattern 1: source-grounded brief with claims + coverage
                if isinstance(data, dict) and "claims" in data and "coverage" in data:
                    lines.append(f"\n[{ts_str}] {icon} {skill_name}")
                    brief_text = data.get("brief", "")
                    if brief_text:
                        lines.append(brief_text)
                    cov = data.get("coverage", {})
                    unanswered = cov.get("unanswered_aspects", [])
                    if unanswered:
                        lines.append(f"  ⚠ unanswered aspects: {', '.join(unanswered)} (gap-detector may ask user)")
                    # Compact claim list — just text + source
                    claims = data.get("claims", [])
                    if claims:
                        lines.append(f"  claims ({len(claims)}, each cites a source):")
                        for c in claims[:5]:
                            conf = c.get("confidence", "?")
                            src = c.get("source", "?")
                            txt = c.get("text", "")[:100]
                            lines.append(f"    • [{conf}] {src}: {txt}")
                        if len(claims) > 5:
                            lines.append(f"    ... +{len(claims)-5} more")
                else:
                    # Legacy format — just brief text or status line
                    brief_text = data.get("brief") if isinstance(data, dict) else None
                    if brief_text:
                        lines.append(f"\n[{ts_str}] {icon} {skill_name}")
                        lines.append(brief_text)
                    else:
                        conf = result.get("confidence", "?")
                        err = result.get("error", "")
                        line = f"[{ts_str}] {icon} {skill_name} (conf={conf})"
                        if err:
                            line += f" — {err[:80]}"
                        lines.append(line)
        lines.append("─" * 60)
        lines.append("💡 Use these citations when answering. If unanswered_aspects")
        lines.append("   exist, ask the user before guessing — Pattern 2 (citation-or-decline).")
        lines.append("─" * 60)
        return "\n".join(lines)

    def shutdown(self):
        """Clean shutdown of background pool + Pattern 5 purge if transient."""
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self.transient:
            try:
                purged = self.purge_session()
                if self.verbose and purged:
                    sys.stderr.write(
                        f"[watcher] Pattern 5: purged {purged} transient entries for session {self.session_id}\n"
                    )
            except Exception as e:
                sys.stderr.write(f"[watcher] purge_session failed: {e}\n")

    # -----------------------------------------------------------------
    # Pattern 2: Citation-or-decline Reasoner
    # -----------------------------------------------------------------

    def reason(self, user_message: str, timeout_sec: int = 30) -> Dict[str, Any]:
        """Pattern 2: run gap-detector on the user's message against current brief.

        This is the citation-or-decline Reasoner. Returns a dict with:
          - verdict: "answer_with_citations" | "answer_with_caveat" |
                     "ask_user_first" | "decline"
          - covered: list of covered aspects (with claim citations)
          - gaps: list of missing knowledge aspects
          - suggested_skills: skills that could fill the gaps
          - ask_user: prompt to show user if verdict is ask_user_first/decline

        The agent SHOULD call this BEFORE composing its reply, and:
          - if verdict == "answer_with_citations": answer using claims[]
          - if verdict == "answer_with_caveat": answer but flag gaps
          - if verdict == "ask_user_first": show ask_user prompt, don't answer
          - if verdict == "decline": tell user no sources cover this
        """
        gap_script = (self.skills_dir / "gap-detector" / "scripts" /
                      "gap_detector.py")
        if not gap_script.exists():
            return {
                "verdict": "answer_with_caveat",
                "covered": [], "gaps": [],
                "suggested_skills": [],
                "ask_user": None,
                "error": "gap-detector skill not found",
            }

        try:
            cmd = [sys.executable, str(gap_script), user_message, "--json"]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout_sec)
            if r.returncode != 0:
                return {
                    "verdict": "answer_with_caveat",
                    "covered": [], "gaps": [],
                    "suggested_skills": [],
                    "ask_user": None,
                    "error": f"gap-detector failed: {r.stderr[:200]}",
                }
            env = json.loads(r.stdout)
            data = env.get("data") or {}
            return {
                "verdict": data.get("verdict", "answer_with_caveat"),
                "covered": [c for c in data.get("claims", [])
                            if "covered" in (c.get("tags") or [])],
                "gaps": data.get("gaps", []),
                "suggested_skills": data.get("suggested_skills", []),
                "ask_user": data.get("ask_user"),
                "confidence": env.get("confidence", 0.5),
            }
        except subprocess.TimeoutExpired:
            return {
                "verdict": "answer_with_caveat",
                "covered": [], "gaps": [],
                "suggested_skills": [],
                "ask_user": None,
                "error": f"gap-detector timed out after {timeout_sec}s",
            }
        except Exception as e:
            return {
                "verdict": "answer_with_caveat",
                "covered": [], "gaps": [],
                "suggested_skills": [],
                "ask_user": None,
                "error": f"reason() exception: {e}",
            }

    def format_reason_for_agent(self, user_message: str,
                                 timeout_sec: int = 30) -> str:
        """Pattern 2: produce a text block the agent reads BEFORE answering.

        Combines:
          1. context_brief (Pattern 1 claims from background skills)
          2. reason() output (gap-detector verdict on this specific question)
        """
        brief_text = self.format_brief_for_agent(max_entries=5)
        reason_result = self.reason(user_message, timeout_sec=timeout_sec)

        lines = [brief_text, ""]
        lines.append("─" * 60)
        lines.append("🧠 GAP-DETECTOR VERDICT (Pattern 2: citation-or-decline)")
        lines.append("─" * 60)
        verdict = reason_result.get("verdict", "?")
        covered = reason_result.get("covered", [])
        gaps = reason_result.get("gaps", [])
        ask_user = reason_result.get("ask_user")
        suggested = reason_result.get("suggested_skills", [])
        err = reason_result.get("error")

        lines.append(f"verdict: {verdict}")
        lines.append(f"covered aspects: {len(covered)} | gaps: {len(gaps)}")

        if covered:
            lines.append("\n✓ COVERED (cite these in your answer):")
            for c in covered[:5]:
                txt = c.get("text", "")[:120]
                src = c.get("source", "?")
                lines.append(f"  • [{src}] {txt}")

        if gaps:
            lines.append("\n⚠ GAPS (knowledge missing):")
            for g in gaps[:5]:
                aspect = g.get("aspect", "?")
                why = g.get("why_missing", "")[:120]
                lines.append(f"  • {aspect}: {why}")

        if suggested:
            lines.append("\n💡 SUGGESTED SKILLS to fill gaps:")
            for s in suggested[:3]:
                lines.append(f"  • {s.get('skill')}: {s.get('aspect')}")

        if err:
            lines.append(f"\n⚠ gap-detector error (non-fatal): {err}")

        lines.append("")
        if verdict == "answer_with_citations":
            lines.append("→ Answer using the cited claims. No need to ask user.")
        elif verdict == "answer_with_caveat":
            lines.append("→ Answer but EXPLICITLY flag what's missing. Don't guess.")
        elif verdict == "ask_user_first":
            prompt = ask_user or "I don't have enough context to answer confidently. Could you clarify?"
            lines.append(f"→ ASK USER FIRST: {prompt}")
        elif verdict == "decline":
            lines.append("→ DECLINE: tell user no sources cover this question.")
        lines.append("─" * 60)
        return "\n".join(lines)

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _match_skills_to_signals(self, signals: List[Dict],
                                  message: str) -> List[tuple]:
        """Observer layer: map signals to (skill_name, input_data) tuples.

        Returns list of (skill_name, input_data) pairs to dispatch.
        """
        skills_to_run: Dict[str, Dict[str, Any]] = {}

        # Map signal types to skills using the SIGNAL_TO_SKILL table.
        # For URL/file/coords signals, input = the matched value.
        # For content-creation signals (blog_writing_request, etc.), input =
        # the full user message — these skills need the whole query to operate.
        CONTENT_CREATION_SIGNALS = set(SIGNAL_TO_SKILL.keys()) - {
            "youtube_url", "soundcloud_url", "vimeo_url", "direct_media_url",
            "pdf_attachment", "docx_attachment", "image_attachment",
            "geocoords", "toponym_ru",
        }

        for sig in signals:
            sig_type = sig["type"]
            sig_value = sig["value"]

            skill_name = SIGNAL_TO_SKILL.get(sig_type)
            if not skill_name:
                # Unknown signal type — try file: prefix
                if sig_type.startswith("file:"):
                    ext = sig_type.split(":", 1)[1]
                    matches = self.registry.find_by_extension(f".{ext}")
                    for m in matches:
                        if m not in BLOCKED_SKILLS and m not in skills_to_run:
                            skills_to_run[m] = {"input": sig_value}
                continue

            if skill_name in BLOCKED_SKILLS:
                continue
            if skill_name in skills_to_run:
                continue

            # Decide input: URL/path/coords → matched value; content-creation → full message
            if sig_type in CONTENT_CREATION_SIGNALS:
                # Pass the ENTIRE user message so the skill can understand context
                skills_to_run[skill_name] = {"input": message}
            elif sig_type in ("pdf_attachment", "docx_attachment", "image_attachment"):
                # File-based skills need the path to actually exist
                if Path(sig_value).exists():
                    skills_to_run[skill_name] = {"input": sig_value}
            else:
                # URL/coords/toponym — pass matched value directly
                skills_to_run[skill_name] = {"input": sig_value}

        # Also use registry.find_by_query() to catch keyword-triggered skills
        # (e.g., "проанализируй PDF" → poler-toolkit)
        keyword_matches = self.registry.find_by_query(message)
        for skill_name in keyword_matches:
            if skill_name in BLOCKED_SKILLS:
                continue
            if skill_name in skills_to_run:
                continue
            # Only auto-trigger keyword-matched skills if there's an actual
            # file/url signal — otherwise the watcher would trigger on every
            # "analyze" mention
            if any(s["type"] in ("pdf_attachment", "docx_attachment",
                                  "youtube_url", "soundcloud_url",
                                  "vimeo_url", "direct_media_url")
                   or s["type"].startswith("file:")
                   for s in signals):
                # If we have an attachment, run poler-toolkit on the message itself
                if skill_name == "poler-toolkit":
                    # poler needs a file path; if we have an attachment, use it
                    for s in signals:
                        if s["type"] in ("pdf_attachment", "docx_attachment") and Path(s["value"]).exists():
                            skills_to_run.setdefault(
                                "poler-toolkit", {"input": s["value"]})
                            break

        # Convert to list of tuples
        return [(name, data) for name, data in skills_to_run.items()]

    def _run_skill_safely(self, skill_name: str, input_data: Dict,
                          dispatch_key: str, original_message: str):
        """Run a skill in background, catch all exceptions, save to brief."""
        t0 = time.time()
        try:
            if self.verbose:
                sys.stderr.write(
                    f"[watcher] ▶ {skill_name} starting\n"
                )
            result = self.executor.run(skill_name, input_data, timeout=600)
            elapsed = time.time() - t0
            if self.verbose:
                status = result.get("status", "unknown")
                conf = result.get("confidence", 0)
                sys.stderr.write(
                    f"[watcher] ✓ {skill_name} done in {elapsed:.1f}s "
                    f"(status={status}, conf={conf})\n"
                )
            self._append_to_brief(skill_name, result, original_message)
        except Exception as e:
            elapsed = time.time() - t0
            if self.verbose:
                sys.stderr.write(
                    f"[watcher] ✗ {skill_name} crashed after {elapsed:.1f}s: {e}\n"
                )
            self._append_to_brief(skill_name, {
                "status": "error",
                "confidence": 0.0,
                "data": None,
                "error": f"watcher: {e}",
            }, original_message)
        finally:
            with self._running_lock:
                self._running.discard(dispatch_key)

    def _append_to_brief(self, skill_name: str, result: Dict,
                          original_message: str):
        """Append skill result to context_brief.json (thread-safe)."""
        # Use a lock file to prevent concurrent writes
        lock_path = self.context_brief_file.with_suffix(".lock")
        max_tries = 10
        for i in range(max_tries):
            try:
                # Atomic-ish: create lock file
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                time.sleep(0.1 * (i + 1))
        else:
            # Could not acquire lock — give up
            sys.stderr.write(f"[watcher] WARNING: could not acquire brief lock, dropping result for {skill_name}\n")
            return

        try:
            # Read current
            if self.context_brief_file.exists():
                try:
                    brief = json.loads(self.context_brief_file.read_text(encoding="utf-8"))
                except Exception:
                    brief = {"entries": [], "entities": {}}
            else:
                brief = {"entries": [], "entities": {}}

            # Ensure structure
            if "entries" not in brief:
                brief["entries"] = []
            if "entities" not in brief:
                brief["entities"] = {}

            # Append entry
            entry = {
                "timestamp": time.time(),
                "message_preview": original_message[:120],
                "results": {skill_name: result},
                "session_id": self.session_id,
            }
            # Pattern 5: tag transient entries with expires_at
            if self.transient:
                entry["expires_at"] = time.time() + self.transient_ttl_sec
            # Also respect per-skill transient flag (Pattern 1 coverage.transient)
            try:
                data = result.get("data") or {}
                cov = data.get("coverage", {}) if isinstance(data, dict) else {}
                if cov.get("transient") and "expires_at" not in entry:
                    entry["expires_at"] = time.time() + self.transient_ttl_sec
            except Exception:
                pass
            brief["entries"].append(entry)

            # Trim to last MAX_BRIEF_ENTRIES
            if len(brief["entries"]) > MAX_BRIEF_ENTRIES:
                brief["entries"] = brief["entries"][-MAX_BRIEF_ENTRIES:]

            # Write atomically
            tmp_path = self.context_brief_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            tmp_path.replace(self.context_brief_file)

        finally:
            try:
                lock_path.unlink()
            except Exception:
                pass

    def purge_expired(self) -> int:
        """Pattern 5: remove entries whose expires_at has passed.

        Returns count of purged entries.
        """
        if not self.context_brief_file.exists():
            return 0
        lock_path = self.context_brief_file.with_suffix(".lock")
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            return 0  # another writer has the lock; skip this round
        try:
            try:
                brief = json.loads(self.context_brief_file.read_text(encoding="utf-8"))
            except Exception:
                return 0
            entries = brief.get("entries", [])
            now = time.time()
            kept = []
            purged = 0
            for e in entries:
                exp = e.get("expires_at")
                if exp is not None and exp < now:
                    purged += 1
                    continue
                kept.append(e)
            if purged:
                brief["entries"] = kept
                tmp = self.context_brief_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(brief, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                tmp.replace(self.context_brief_file)
            return purged
        finally:
            try:
                lock_path.unlink()
            except Exception:
                pass

    def purge_session(self, session_id: Optional[str] = None) -> int:
        """Pattern 5: remove all entries from a given session (default: this session)."""
        sid = session_id or self.session_id
        if not self.context_brief_file.exists():
            return 0
        lock_path = self.context_brief_file.with_suffix(".lock")
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            return 0
        try:
            try:
                brief = json.loads(self.context_brief_file.read_text(encoding="utf-8"))
            except Exception:
                return 0
            entries = brief.get("entries", [])
            kept = [e for e in entries if e.get("session_id") != sid]
            purged = len(entries) - len(kept)
            if purged:
                brief["entries"] = kept
                tmp = self.context_brief_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(brief, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                tmp.replace(self.context_brief_file)
            return purged
        finally:
            try:
                lock_path.unlink()
            except Exception:
                pass

    # -----------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------

    def doctor(self) -> Dict[str, Any]:
        """Quick health check."""
        return {
            "skills_loaded": len(self.registry.list_skills()),
            "brief_file": str(self.context_brief_file),
            "brief_exists": self.context_brief_file.exists(),
            "brief_entries": len(self.get_context_brief().get("entries", [])),
            "running_skills": list(self._running),
            "thread_pool_workers": self._pool._max_workers,
            "session_id": self.session_id,
            "transient": self.transient,
            "transient_ttl_sec": self.transient_ttl_sec,
        }


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Conversation watcher — online context layer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-shot: process a single message
  python3 watcher.py --process "посмотри https://youtube.com/watch?v=xxx"

  # Interactive stdin loop (one message per line)
  python3 watcher.py --stdin

  # Print current context_brief
  python3 watcher.py --brief

  # Print formatted brief for agent consumption
  python3 watcher.py --brief-for-agent

  # Doctor
  python3 watcher.py --doctor
""",
    )
    ap.add_argument("--skills-dir", default=str(DEFAULT_SKILLS_DIR))
    ap.add_argument("--brief-file", default=str(DEFAULT_BRIEF_FILE))
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--process", help="Process a single message and exit")
    ap.add_argument("--stdin", action="store_true",
                    help="Read messages from stdin (one per line)")
    ap.add_argument("--brief", action="store_true",
                    help="Print current context_brief.json")
    ap.add_argument("--brief-for-agent", action="store_true",
                    help="Print formatted brief for agent consumption")
    ap.add_argument("--doctor", action="store_true",
                    help="Print watcher health check")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="After --process, wait N seconds for background skills to finish")
    ap.add_argument("--transient", action="store_true",
                    help="Pattern 5: mark all entries as transient (purged on shutdown)")
    ap.add_argument("--session-id", default=None,
                    help="Pattern 5: explicit session id (default: random 8-char)")
    ap.add_argument("--purge-expired", action="store_true",
                    help="Pattern 5: purge all expired entries and exit")
    ap.add_argument("--reason", default=None,
                    help="Pattern 2: run gap-detector on this message, "
                         "print combined brief + verdict for agent")
    ap.add_argument("--verify", action="store_true",
                    help="Run sanity checks on watcher setup (patterns, registry, brief file) and exit")
    args = ap.parse_args()

    watcher = ConversationWatcher(
        skills_dir=Path(args.skills_dir),
        context_brief_file=Path(args.brief_file),
        verbose=args.verbose,
        session_id=args.session_id,
        transient=args.transient,
    )

    if args.doctor:
        print(json.dumps(watcher.doctor(), ensure_ascii=False, indent=2))
        return 0

    if args.verify:
        # Sanity check: patterns load, registry works, brief dir writable
        print(f"✓ Watcher module loaded")
        print(f"  Signal patterns: {len(SIGNAL_PATTERNS)}")
        print(f"  Skill mappings:  {len(SIGNAL_TO_SKILL)}")
        # Check brief file dir
        brief_dir = Path(args.brief_file).parent
        brief_dir.mkdir(parents=True, exist_ok=True)
        test_file = brief_dir / ".verify_test"
        try:
            test_file.write_text("ok")
            test_file.unlink()
            print(f"  Brief file dir:  writable ({brief_dir})")
        except Exception as e:
            print(f"  ✗ Brief file dir not writable: {e}")
            return 1
        # Check skills dir
        skills_dir = Path(args.skills_dir)
        if not skills_dir.exists():
            print(f"  ✗ Skills dir not found: {skills_dir}")
            return 1
        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())
        exec_count = sum(1 for d in skills_dir.iterdir()
                         if d.is_dir() and (d / "scripts" / "run.py").exists())
        print(f"  Skills dir:      {skills_dir}")
        print(f"  Skills found:    {skill_count}")
        print(f"  Executable:      {exec_count}")
        # Test a sample signal detection
        test_msg = "напиши пост про ИИ и посмотри https://youtube.com/watch?v=abc123"
        sigs = detect_signals(test_msg)
        print(f"  Sample detect:   '{test_msg[:40]}...' → {len(sigs)} signal(s)")
        if not sigs:
            print(f"  ✗ Sample detection failed (expected at least 1 signal)")
            return 1
        print(f"✓ Watcher verification passed")
        return 0

    if args.purge_expired:
        n = watcher.purge_expired()
        print(json.dumps({"purged": n, "session_id": watcher.session_id},
                         ensure_ascii=False, indent=2))
        return 0

    if args.reason:
        # Pattern 2: run gap-detector on the message, print brief + verdict
        text = watcher.format_reason_for_agent(args.reason, timeout_sec=60)
        print(text)
        return 0

    if args.brief:
        print(json.dumps(watcher.get_context_brief(), ensure_ascii=False, indent=2))
        return 0

    if args.brief_for_agent:
        text = watcher.format_brief_for_agent()
        if text:
            print(text)
        else:
            print("(context_brief is empty)")
        return 0

    if args.process:
        report = watcher.process_message(args.process)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.wait > 0:
            sys.stderr.write(f"[watcher] waiting {args.wait}s for background skills...\n")
            time.sleep(args.wait)
            sys.stderr.write("\n" + watcher.format_brief_for_agent() + "\n")
        return 0

    if args.stdin:
        sys.stderr.write(
            "Conversation watcher — stdin mode. Type messages (one per line), Ctrl-D to exit.\n"
        )
        try:
            for line in sys.stdin:
                line = line.rstrip("\n")
                if not line:
                    continue
                if line.lower() in ("exit", "quit"):
                    break
                report = watcher.process_message(line)
                sys.stderr.write(
                    f"[watcher] dispatched: {len(report.get('dispatched', []))} skill(s)\n"
                )
        except KeyboardInterrupt:
            pass
        return 0

    # No args — print help
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
