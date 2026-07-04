#!/usr/bin/env python3
"""
ingest.py — Unified document ingestion pipeline for poler-toolkit.

Replaces the multi-step "find → download → convert → theme-detect" workflow
with a single command. Handles:

  - Local files: .pdf, .md, .txt, .epub, .zip, .tar.gz, .py, .rs, .c, …
  - URLs:        http(s)://... → curl → HTML strip → text
  - stdin:       pipe raw bytes/text via '-'

Auto-detects:
  - PDF type: digital (pdftotext) vs scanned (tesseract OCR via pdf-ocr skill)
  - File format: dispatched to poler_v6.read_file (which handles epub/zip/tar/code)
  - Code vs prose: code files get entity extraction, prose gets chunking
  - Theme: scored against poler THEMES (biology/astronomy/geography/cultivation/navigation)

Output (JSON):
  {
    "text": "...",
    "meta": {
      "source": "/path or URL",
      "source_type": "file|url|stdin",
      "format": "pdf|md|txt|epub|html|py|...",
      "ocr_used": false,
      "chars": 12345,
      "lang": "ru|en|...",
      "is_code": false,
      "n_chunks": 8
    },
    "theme": {
      "name": "биология",
      "scores": {"биология": 12, "астрономия": 3, ...},
      "distinct_words": {"биология": 4, ...},
      "method": "poler_themes"
    },
    "keywords": ["...", "...", "..."],          # top TF-IDF bigrams (local, no LLM)
    "clusters": [                                # only if text > MAX_CHARS_FOR_THEME
      {"id": 0, "chars": 1500, "keywords": [...]},
      ...
    ]
  }

By default NO LLM is called — everything runs locally in <1s for typical docs.
This avoids the 429 rate-limit storm (49 LLM calls on 7 docs × 7 clusters).

Optional --llm flag: makes ONE LLM call (via z-ai chat) to summarize the
document's overall topic into a single semantic phrase. This replaces the
7-docs × 7-clusters = 49 LLM calls pattern with 1 LLM call per document.

Usage:
  python3 ingest.py INPUT [-o OUT.json] [--llm] [--max-chars N] [--verbose]
                          [--no-keywords] [--no-clusters]

  INPUT  = file path | URL | '-' (stdin)

Examples:
  python3 ingest.py paper.pdf --json
  python3 ingest.py https://example.com/article.html --json
  cat notes.md | python3 ingest.py - --json
  python3 ingest.py book.epub --llm --json     # add 1 LLM call for semantic topic

Author: poler-toolkit (Task 7, 2026-07-03)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Make sibling scripts importable
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))  # for poler_v6, topic_common

import poler_v6  # type: ignore
import topic_common  # type: ignore

# Path to ocr_pdf.py from the pdf-ocr skill (sibling of poler-toolkit)
OCR_PDF_SCRIPT = SCRIPT_DIR.parent.parent / "pdf-ocr" / "scripts" / "ocr_pdf.py"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Below this many chars, just theme-detect the whole text — no chunking.
MAX_CHARS_FOR_THEME = 6000

# For chunking large docs, reuse topic_common's chunker (default 1500 chars/chunk).
CHUNK_MAX_CHARS = 1500

# Top-K keywords per cluster / overall (by TF-IDF score).
TOP_K_KEYWORDS = 5

# Stopwords (Russian + English) for keyword extraction — minimal set.
STOPWORDS = {
    # Russian
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь",
    "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей",
    "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя",
    "их", "чем", "была", "сам", "чтоб", "без", "будто", "чего", "раз", "тоже",
    "себе", "под", "будет", "ж", "тогда", "кто", "этот", "того", "потому",
    "этого", "какой", "совсем", "ним", "здесь", "этом", "один", "почти", "мой",
    "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем", "всех", "никогда",
    "можно", "при", "наконец", "два", "об", "другой", "хоть", "после", "над",
    "больше", "тот", "через", "эти", "нас", "про", "всего", "них", "какая",
    "много", "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой",
    "перед", "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им", "более",
    "всегда", "конечно", "всю", "между",
    # English
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "of",
    "to", "in", "on", "at", "by", "with", "from", "as", "is", "was", "are",
    "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must", "can", "this",
    "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "them", "their", "there", "here", "what", "which", "who", "when", "where",
    "why", "how", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "no", "not", "only", "own", "same", "so", "than",
    "too", "very", "just", "also", "now",
    # PDF artifacts
    "page", "стр",
}


# ---------------------------------------------------------------------------
# Source-type detection
# ---------------------------------------------------------------------------

def detect_source_type(input_arg: str) -> str:
    """Return 'url', 'stdin', or 'file'."""
    if input_arg == "-":
        return "stdin"
    parsed = urlparse(input_arg)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return "url"
    return "file"


# ---------------------------------------------------------------------------
# URL → text (curl + HTML strip)
# ---------------------------------------------------------------------------

# Tags whose content is typically noise for topic detection.
HTML_NOISE_TAGS = re.compile(
    r"<(script|style|noscript|iframe|svg|head|header|footer|nav|form)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
HTML_TAG = re.compile(r"<[^>]+>")
HTML_ENTITY = re.compile(r"&[a-zA-Z]+;|&#\d+;")
HTML_WS = re.compile(r"[ \t]+")
HTML_BLANK_LINES = re.compile(r"\n{3,}")


def fetch_url(url: str, verbose: bool = False) -> Tuple[str, Dict[str, Any]]:
    """Fetch URL via curl, strip HTML → plain text. Returns (text, meta)."""
    t0 = time.time()
    cmd = [
        "curl", "-sL", "--max-time", "30",
        "-A", "Mozilla/5.0 (compatible; poler-ingest/1.0)",
        url,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=45)
        if out.returncode != 0:
            raise RuntimeError(
                f"curl failed (exit={out.returncode}): "
                f"{out.stderr.decode('utf-8', errors='replace')[:200]}"
            )
        html = out.stdout.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise RuntimeError("curl timed out (>45s)")

    if verbose:
        sys.stderr.write(f"[ingest] fetched {len(html)} HTML bytes in {time.time()-t0:.1f}s\n")

    # Strip noise tags first (so their content doesn't pollute text)
    html = HTML_NOISE_TAGS.sub("", html)
    # Try to find <title> before stripping all tags
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""
    title = HTML_ENTITY.sub(" ", title)
    title = HTML_WS.sub(" ", title).strip()

    # Strip remaining tags
    text = HTML_TAG.sub(" ", html)
    # Decode common entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    text = HTML_ENTITY.sub(" ", text)
    # Normalize whitespace
    text = HTML_WS.sub(" ", text)
    text = HTML_BLANK_LINES.sub("\n\n", text)
    # Collapse spaces around newlines
    text = re.sub(r" *\n *", "\n", text)
    text = text.strip()

    meta = {
        "url": url,
        "html_bytes": len(html),
        "title": title,
        "fetch_sec": round(time.time() - t0, 2),
    }
    return text, meta


# ---------------------------------------------------------------------------
# PDF → text (via pdf-ocr skill)
# ---------------------------------------------------------------------------

def extract_pdf(pdf_path: str, max_pages: int, verbose: bool = False) -> Tuple[str, Dict[str, Any]]:
    """Call ocr_pdf.py to extract text from PDF (auto-detect digital vs scanned)."""
    if not OCR_PDF_SCRIPT.exists():
        # Fallback: direct pdftotext call (no OCR fallback)
        if verbose:
            sys.stderr.write(f"[ingest] ocr_pdf.py not found at {OCR_PDF_SCRIPT}, "
                             f"using pdftotext directly\n")
        out = subprocess.run(
            ["pdftotext", "-layout", "-l", str(max_pages), pdf_path, "-"],
            capture_output=True, timeout=120,
        )
        text = out.stdout.decode("utf-8", errors="replace")
        return text, {
            "ocr_used": False, "pages_processed": max_pages,
            "extraction_method": "pdftotext (fallback, ocr_pdf.py missing)",
            "chars_per_page_avg": round(len(text) / max(1, max_pages), 1),
        }

    cmd = [
        sys.executable, str(OCR_PDF_SCRIPT), pdf_path,
        "--json", "--max-pages", str(max_pages),
    ]
    if verbose:
        cmd.append("--verbose")
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=600)
        if out.returncode != 0:
            err = out.stderr.decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"ocr_pdf.py failed: {err}")
        payload = json.loads(out.stdout.decode("utf-8", errors="replace"))
        return payload["text"], payload["meta"]
    except subprocess.TimeoutExpired:
        raise RuntimeError("ocr_pdf.py timed out (>600s)")


# ---------------------------------------------------------------------------
# Local file → text (dispatch by extension)
# ---------------------------------------------------------------------------

def extract_file(file_path: str, max_pages: int, verbose: bool = False) -> Tuple[str, Dict[str, Any]]:
    """Extract text from local file. Dispatches by extension."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(file_path)

    ext = p.suffix.lower()

    # --- PDF: route through pdf-ocr skill ---
    if ext == ".pdf":
        text, ocr_meta = extract_pdf(file_path, max_pages=max_pages, verbose=verbose)
        return text, {
            "format": "pdf",
            "ocr_used": ocr_meta.get("ocr_used", False),
            "extraction_method": ocr_meta.get("extraction_method", "?"),
            "pages_processed": ocr_meta.get("pages_processed", 0),
            "chars_per_page_avg": ocr_meta.get("chars_per_page_avg", 0),
        }

    # --- HTML: same strip logic as URL ---
    if ext in (".html", ".htm"):
        html = p.read_text(encoding="utf-8", errors="replace")
        html = HTML_NOISE_TAGS.sub("", html)
        text = HTML_TAG.sub(" ", html)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
        text = HTML_ENTITY.sub(" ", text)
        text = HTML_WS.sub(" ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = HTML_BLANK_LINES.sub("\n\n", text).strip()
        return text, {"format": "html", "ocr_used": False, "extraction_method": "html_strip"}

    # --- Everything else: poler_v6.read_file handles .md/.txt/.epub/.zip/.tar.gz/.py/.rs/.c/... ---
    text = poler_v6.read_file(file_path)
    return text, {
        "format": ext.lstrip(".") or "txt",
        "ocr_used": False,
        "extraction_method": "poler_v6.read_file",
    }


# ---------------------------------------------------------------------------
# Theme detection (LOCAL, no LLM)
# ---------------------------------------------------------------------------

def detect_theme_local(text: str) -> Dict[str, Any]:
    """Use poler_v6 THEMES to score the text. Returns structured theme info."""
    # detect_theme_with_scores returns (theme_name, scores_dict, distinct_counts_dict)
    theme_name, scores, distinct_counts = poler_v6.detect_theme_with_scores(text)
    return {
        "name": theme_name,
        "scores": scores,
        "distinct_words": distinct_counts,
        "method": "poler_themes (local, no LLM)",
    }


# ---------------------------------------------------------------------------
# Keyword extraction (LOCAL TF-IDF, no LLM)
# ---------------------------------------------------------------------------

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9\-]*")


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens, stopwords filtered."""
    tokens = []
    for m in WORD_RE.finditer(text.lower()):
        t = m.group(0)
        if len(t) < 3:
            continue
        if t in STOPWORDS:
            continue
        tokens.append(t)
    return tokens


def extract_keywords_tfidf(text: str, top_k: int = TOP_K_KEYWORDS) -> List[str]:
    """Extract top-K keyword bigrams by simple TF (single-doc, no IDF corpus)."""
    tokens = tokenize(text)
    if len(tokens) < 4:
        return tokens[:top_k]
    # Count unigrams + bigrams
    from collections import Counter
    counts = Counter()
    for t in tokens:
        counts[t] += 1
    for i in range(len(tokens) - 1):
        bg = f"{tokens[i]} {tokens[i+1]}"
        counts[bg] += 1
    # Prefer bigrams (longer = more informative), then unigrams
    bigrams = [(w, c) for w, c in counts.items() if " " in w and c >= 2]
    bigrams.sort(key=lambda x: -x[1])
    keywords = [w for w, _ in bigrams[:top_k]]
    if len(keywords) < top_k:
        unigrams = [(w, c) for w, c in counts.items() if " " not in w and c >= 2]
        unigrams.sort(key=lambda x: -x[1])
        for w, _ in unigrams:
            if w not in keywords:
                keywords.append(w)
            if len(keywords) >= top_k:
                break
    return keywords


# ---------------------------------------------------------------------------
# Chunking + per-chunk keywords (LOCAL, no LLM)
# ---------------------------------------------------------------------------

def build_clusters(text: str) -> List[Dict[str, Any]]:
    """Split text into chunks and extract keywords per chunk."""
    chunks = topic_common.split_into_chunks(text, max_chars=CHUNK_MAX_CHARS)
    clusters = []
    for i, ch in enumerate(chunks):
        clusters.append({
            "id": i,
            "chars": len(ch),
            "keywords": extract_keywords_tfidf(ch, top_k=3),
        })
    return clusters


# ---------------------------------------------------------------------------
# Optional: 1 LLM call for semantic topic (--llm flag)
# ---------------------------------------------------------------------------
#
# IMPORTANT — SELF-AWARENESS NOTE:
# `z-ai chat` invokes GLM-4 (the same model that is writing this code).
# This is a deliberate design choice: the pipeline calls ITSELF for semantic
# topic detection. Implications:
#
#  1. The system prompt is written in GLM-4's own voice — direct Russian,
#     no "please"/"could you" politeness markers, no English preamble.
#  2. We ask for strict JSON output (GLM-4 is reliable at JSON when asked
#     explicitly with a schema). This lets us return structured fields
#     {topic, confidence, alt_topics[]} instead of just a string.
#  3. We pass poler's local THEMES scores as a hint — the LLM can confirm
#     or override them, but always sees them. This way local + LLM info
#     is fused, not siloed.
#  4. Failure is graceful: if LLM returns garbage, we keep poler's local
#     theme + TF-IDF keywords. The pipeline never hard-fails on LLM errors.
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = (
    "Ты — модуль автоопределения темы документа в pipeline poler-toolkit. "
    "Ты получаешь фрагмент документа и подсказку от локального словаря тем. "
    "Верни СТРОГО JSON без markdown, без комментариев, без code-fence блоков. "
    "Формат: {\"topic\": \"фраза 2-7 слов\", \"confidence\": 0.0-1.0, "
    "\"alt_topics\": [\"альтернатива 1\", \"альтернатива 2\"]}. "
    "topic — основная тема документа одной фразой в именительном падеже. "
    "confidence — твоя уверенность (0.0=не уверен, 1.0=точно). "
    "alt_topics — до 2 альтернативных тем если документ многотемный. "
    "Не добавляй никаких других полей. Не оборачивай в markdown."
)


def llm_semantic_topic(text: str, poler_theme: Dict[str, Any]) -> Dict[str, Any]:
    """One z-ai chat call (GLM-4 calling itself) for semantic topic.

    Returns dict with keys: topic, confidence, alt_topics, raw_response.
    All keys None/empty on failure (caller keeps poler's local result).
    """
    # Sample: start + middle + end (representative coverage)
    sample_max = 4000
    if len(text) <= sample_max:
        sample = text
    else:
        third = sample_max // 3
        sample = (
            text[:third]
            + "\n\n[...middle...]\n\n"
            + text[len(text) // 2 - third // 2:len(text) // 2 + third // 2]
            + "\n\n[...end...]\n\n"
            + text[-third:]
        )

    # Pass poler's local scores as a hint — LLM can confirm OR override
    scores_str = ", ".join(
        f"{k}={v}" for k, v in sorted(poler_theme["scores"].items(),
                                       key=lambda x: -x[1])
    ) if poler_theme.get("scores") else "no local scores"
    local_theme = poler_theme.get("name", "general")

    prompt = (
        f"Документ (фрагмент {len(sample)} символов):\n\n{sample}\n\n"
        f"---\n"
        f"Подсказка от локального словаря poler THEMES:\n"
        f"  Локальная тема: {local_theme}\n"
        f"  Счет по 5 темам: {scores_str}\n"
        f"  (Эти числа могут быть шумными — доверяй тексту больше, чем числам.)\n"
        f"---\n\n"
        f"Верни JSON с темой документа."
    )

    result: Dict[str, Any] = {
        "topic": None,
        "confidence": None,
        "alt_topics": [],
        "raw_response": None,
    }

    try:
        out = subprocess.run(
            ["z-ai", "chat", "-p", prompt, "-s", LLM_SYSTEM_PROMPT,
             "-o", "/tmp/zai_ingest_topic.json"],
            capture_output=True, timeout=60,
        )
        if out.returncode != 0:
            return result

        # Parse z-ai chat output — OpenAI-compatible:
        # {"choices": [{"message": {"content": "..."}}], ...}
        try:
            payload = json.loads(
                Path("/tmp/zai_ingest_topic.json").read_text(encoding="utf-8")
            )
        except Exception:
            return result

        content = None
        if isinstance(payload, dict):
            choices = payload.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message", {})
                content = msg.get("content")
            if not content:
                for key in ("content", "text", "response"):
                    if isinstance(payload.get(key), str):
                        content = payload[key]
                        break

        if not content or not isinstance(content, str):
            return result

        result["raw_response"] = content.strip()[:500]  # cap for safety

        # Try strict JSON parse first
        content_stripped = content.strip()
        # Strip markdown code fences if GLM added them anyway
        if content_stripped.startswith("```"):
            content_stripped = re.sub(r"^```(?:json)?\s*", "", content_stripped)
            content_stripped = re.sub(r"\s*```$", "", content_stripped)

        try:
            parsed = json.loads(content_stripped)
            if isinstance(parsed, dict):
                result["topic"] = (parsed.get("topic") or "").strip() or None
                conf = parsed.get("confidence")
                if isinstance(conf, (int, float)):
                    result["confidence"] = float(conf)
                alts = parsed.get("alt_topics") or []
                if isinstance(alts, list):
                    result["alt_topics"] = [
                        str(a).strip() for a in alts if a and str(a).strip()
                    ][:2]
                return result
        except json.JSONDecodeError:
            pass

        # Fallback: extract first quoted phrase or first line as topic
        # (GLM sometimes returns just the phrase without JSON wrapper)
        first_line = content_stripped.split("\n", 1)[0].strip().strip('"').strip("'")
        if first_line and len(first_line) < 200:
            result["topic"] = first_line
            result["confidence"] = 0.5  # mark as low-confidence fallback
        return result

    except Exception:
        return result


# ---------------------------------------------------------------------------
# Confidence calculation (Task 9 — manifest-based architecture)
# ---------------------------------------------------------------------------

def calculate_confidence(theme: Dict[str, Any],
                         keywords: List[str],
                         clusters: List[Dict[str, Any]],
                         meta: Dict[str, Any]) -> float:
    """Compute a normalized 0..1 confidence score for the ingest result.

    Heuristic (mirrors the design from DeepSeek audit):
      base = 0.85 if LLM semantic topic present, else 0.60
      + 0.05 if >5 keywords extracted
      + 0.05 if >2 clusters detected
      - 0.15 if OCR was used (OCR is noisier than digital text)
      - 0.10 if text < 200 chars (too short for reliable theme detection)
      + LLM semantic_confidence * 0.10 if available (LLM self-score bonus)
    Clamped to [0.0, 1.0].
    """
    base = 0.85 if theme.get("semantic") else 0.60

    if keywords and len(keywords) > 5:
        base += 0.05
    if clusters and len(clusters) > 2:
        base += 0.05

    if meta.get("ocr_used"):
        base -= 0.15
    if meta.get("chars", 0) < 200:
        base -= 0.10

    sem_conf = theme.get("semantic_confidence")
    if sem_conf is not None and isinstance(sem_conf, (int, float)):
        base += float(sem_conf) * 0.10

    # Theme strength: how dominant is the top theme?
    scores = theme.get("scores", {})
    if scores:
        top_score = max(scores.values()) if scores else 0
        if top_score >= 10:
            base += 0.03  # strong theme signal

    return max(0.0, min(1.0, round(base, 3)))


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------

def ingest(input_arg: str, max_pages: int = 50, do_llm: bool = False,
           do_keywords: bool = True, do_clusters: bool = True,
           verbose: bool = False) -> Dict[str, Any]:
    """Run the full ingestion pipeline. Returns structured result dict."""
    t0 = time.time()
    src_type = detect_source_type(input_arg)

    # --- Step 1: extract text ---
    extract_meta: Dict[str, Any] = {"source": input_arg, "source_type": src_type}

    if src_type == "url":
        text, url_meta = fetch_url(input_arg, verbose=verbose)
        extract_meta.update({
            "format": "html",
            "ocr_used": False,
            "extraction_method": "curl+html_strip",
            "url_title": url_meta.get("title", ""),
            "html_bytes": url_meta.get("html_bytes", 0),
            "fetch_sec": url_meta.get("fetch_sec", 0),
        })
    elif src_type == "stdin":
        raw = sys.stdin.buffer.read()
        # Try to decode as text
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        extract_meta.update({
            "format": "stdin",
            "ocr_used": False,
            "extraction_method": "stdin",
        })
    else:  # file
        text, file_meta = extract_file(input_arg, max_pages=max_pages, verbose=verbose)
        extract_meta.update(file_meta)

    if verbose:
        sys.stderr.write(
            f"[ingest] extracted {len(text)} chars via "
            f"{extract_meta.get('extraction_method', '?')} "
            f"(ocr_used={extract_meta.get('ocr_used', False)})\n"
        )

    # --- Step 2: detect code vs prose ---
    is_code_flag = topic_common.is_code(text, path=input_arg if src_type == "file" else None)
    lang = topic_common.detect_language(
        input_arg if src_type == "file" else None, text
    ) if is_code_flag else None

    # --- Step 3: theme detection (LOCAL) ---
    theme_info = detect_theme_local(text)

    # --- Step 4: keywords (LOCAL TF) ---
    keywords = extract_keywords_tfidf(text) if do_keywords else []

    # --- Step 5: clusters (LOCAL) ---
    clusters: List[Dict[str, Any]] = []
    if do_clusters and len(text) > MAX_CHARS_FOR_THEME:
        clusters = build_clusters(text)

    # --- Step 6 (optional): 1 LLM call for semantic topic ---
    # NOTE: z-ai chat invokes GLM-4 — the same model architecturally as the
    # agent running this pipeline. We're calling OURSELF for semantic topic.
    # See llm_semantic_topic() docstring for self-aware design notes.
    if do_llm:
        if verbose:
            sys.stderr.write("[ingest] calling LLM (GLM-4 self-call) for semantic topic...\n")
        llm_result = llm_semantic_topic(text, poler_theme=theme_info)
        # Always attach the structured LLM result (even if topic is None —
        # useful for debugging prompt quality)
        theme_info["semantic"] = llm_result["topic"]
        theme_info["semantic_confidence"] = llm_result["confidence"]
        theme_info["semantic_alt_topics"] = llm_result["alt_topics"]
        if llm_result["raw_response"] and not llm_result["topic"]:
            # LLM returned something but we couldn't parse it — keep raw for debug
            theme_info["llm_raw_response"] = llm_result["raw_response"]
            theme_info["method"] = "poler_themes + LLM (parse failed, fell back to local)"
        elif llm_result["topic"]:
            theme_info["method"] = "poler_themes + 1 LLM self-call (--llm)"
        else:
            theme_info["method"] = "poler_themes + LLM (call failed)"

    # --- Final result ---
    extract_meta["chars"] = len(text)
    extract_meta["lang"] = lang
    extract_meta["is_code"] = is_code_flag
    extract_meta["n_chunks"] = len(clusters)
    extract_meta["total_elapsed_sec"] = round(time.time() - t0, 2)

    # Compute confidence and wrap in standard envelope (Task 9)
    confidence = calculate_confidence(theme_info, keywords, clusters, extract_meta)

    # Return BOTH:
    #   - Standard envelope (top-level status/confidence/data) for the
    #     orchestrator/manifest-based architecture.
    #   - Legacy flat fields (text/meta/theme/keywords/clusters) at the top
    #     level too, so existing callers and tests that do result["text"]
    #     keep working. The Orchestrator reads .data, legacy callers read
    #     the flat fields — both work.
    return {
        "status": "success",
        "confidence": confidence,
        "data": {
            "text": text,
            "meta": extract_meta,
            "theme": theme_info,
            "keywords": keywords,
            "clusters": clusters,
        },
        # Legacy flat fields (backwards compatibility — same pointers)
        "text": text,
        "meta": extract_meta,
        "theme": theme_info,
        "keywords": keywords,
        "clusters": clusters,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Unified document ingestion pipeline — text + theme + keywords + clusters in one call.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 ingest.py paper.pdf --json
  python3 ingest.py https://example.com/article.html --json
  cat notes.md | python3 ingest.py - --json
  python3 ingest.py book.epub --llm --json      # 1 LLM call for semantic topic
""",
    )
    ap.add_argument("input", help="File path, URL (http/https), or '-' for stdin")
    ap.add_argument("-o", "--output", help="Write JSON to file (default: stdout)")
    ap.add_argument("--json", action="store_true",
                    help="Emit full JSON {text, meta, theme, keywords, clusters}")
    ap.add_argument("--llm", action="store_true",
                    help="Add 1 LLM call (via z-ai chat) for semantic topic. "
                         "Default: OFF — fully local, no rate-limit risk.")
    ap.add_argument("--max-pages", type=int, default=50,
                    help="For PDFs: cap pages (default: 50)")
    ap.add_argument("--no-keywords", action="store_true",
                    help="Skip keyword extraction (faster)")
    ap.add_argument("--no-clusters", action="store_true",
                    help="Skip per-chunk cluster keywords (faster)")
    ap.add_argument("--no-text", action="store_true",
                    help="Omit 'text' field from JSON output (smaller payload)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Progress to stderr")
    args = ap.parse_args()

    try:
        result = ingest(
            args.input,
            max_pages=args.max_pages,
            do_llm=args.llm,
            do_keywords=not args.no_keywords,
            do_clusters=not args.no_clusters,
            verbose=args.verbose,
        )
    except Exception as e:
        # Standard error envelope so orchestrator can parse failures uniformly
        err_result = {
            "status": "error",
            "confidence": 0.0,
            "data": None,
            "error": str(e),
        }
        sys.stderr.write(f"[ingest] ERROR: {e}\n")
        print(json.dumps(err_result, ensure_ascii=False, indent=2))
        return 1

    if args.no_text:
        # Strip text from BOTH the legacy top-level field and the .data wrapper
        result.pop("text", None)
        if isinstance(result.get("data"), dict):
            result["data"].pop("text", None)

    out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        if args.verbose:
            sys.stderr.write(f"[ingest] wrote {args.output}\n")
    else:
        print(out)

    # Always print summary to stderr (read from .data when present, fallback to top-level)
    data = result.get("data") or result
    m = data["meta"]
    t = data["theme"]
    sys.stderr.write(
        f"[ingest] {m.get('source_type', '?')} → {m.get('format', '?')} "
        f"({m.get('chars', 0)} chars, ocr={m.get('ocr_used', False)}) | "
        f"theme='{t['name']}' "
        f"({', '.join(f'{k}={v}' for k,v in sorted(t['scores'].items(), key=lambda x:-x[1])[:3])}) | "
        f"{len(data['keywords'])} kw, {len(data['clusters'])} clusters | "
        f"confidence={result.get('confidence', '?')} | "
        f"{m.get('total_elapsed_sec', 0)}s"
        + (f" | semantic='{t.get('semantic')}'" if t.get("semantic") else "")
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
