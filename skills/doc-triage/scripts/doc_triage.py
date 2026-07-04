#!/usr/bin/env python3
"""
doc_triage.py — Online-layer triage of document attachments (PDF/docx/txt/md).

Triggered by conversation-watcher when a file path appears in the user's
message. Extracts text (pdf-ocr for PDF, python-docx for .docx, direct read
for txt/md), summarizes via poler-toolkit, returns a Pattern 1 source-grounded
brief (claims+citations+coverage) for the agent.

This is a fast triage — NOT a full report. The agent gets:
  - 3-5 line brief
  - claims[] each with source+span+confidence
  - coverage{} with unanswered_aspects for the gap-detector
  - text_path so the agent can answer follow-up questions directly

CLI:
    python3 doc_triage.py /path/to/file.pdf --json
    python3 doc_triage.py - --json            # read message from stdin, extract first path
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────

SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_DIR.parent.parent

# pdf-ocr entry point
PDF_OCR = SKILL_DIR.parent / "pdf-ocr" / "scripts" / "ocr_pdf.py"

# poler-toolkit ingest.py
POLER_INGEST = SKILL_DIR.parent / "poler-toolkit" / "scripts" / "ingest.py"

# Pattern 1 (source-grounded brief)
_ORCH_SCRIPTS = SKILL_DIR.parent / "_orchestrator" / "scripts"
if str(_ORCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ORCH_SCRIPTS))
try:
    from patterns.source_grounded_brief import build_brief, Claim, validate_brief
    _HAS_PATTERN1 = True
except Exception as _e:
    sys.stderr.write(f"[doc-triage] WARNING: source_grounded_brief unavailable: {_e}\n")
    _HAS_PATTERN1 = False

CACHE_DIR = Path("/tmp/doc_triage_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf"}


# ─────────────────────────────────────────────────────────────────────
# Text extraction
# ─────────────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: str, max_pages: int = 100) -> Tuple[str, Dict[str, Any]]:
    """Use pdf-ocr (which auto-detects digital vs scanned)."""
    if not PDF_OCR.exists():
        # Fallback: try pdftotext directly
        pdftotext = shutil.which("pdftotext")
        if pdftotext:
            try:
                r = subprocess.run(
                    [pdftotext, "-l", str(max_pages), pdf_path, "-"],
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode == 0:
                    return r.stdout, {"method": "pdftotext-fallback", "ocr_used": False}
            except Exception as e:
                sys.stderr.write(f"[doc-triage] pdftotext fallback failed: {e}\n")
        return "", {"method": "none", "ocr_used": False, "error": "no pdf-ocr or pdftotext available"}

    # Use cache for OCR (it's expensive)
    file_hash = hashlib.sha256(Path(pdf_path).read_bytes()[:65536]).hexdigest()[:16]
    cache_file = CACHE_DIR / f"{file_hash}.txt"
    if cache_file.exists():
        try:
            return cache_file.read_text(encoding="utf-8"), {"method": "pdf-ocr-cached", "ocr_used": "unknown"}
        except Exception:
            pass

    try:
        cmd = [sys.executable, str(PDF_OCR), pdf_path, "--max-pages", str(max_pages), "--json"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return "", {"method": "pdf-ocr", "ocr_used": False, "error": r.stderr[:200]}
        try:
            env = json.loads(r.stdout)
        except json.JSONDecodeError:
            # Maybe pdf-ocr writes plain text
            return r.stdout, {"method": "pdf-ocr-text", "ocr_used": False}
        text = (env.get("data") or {}).get("text", "") if isinstance(env.get("data"), dict) else ""
        ocr_used = (env.get("data") or {}).get("ocr_used", False) if isinstance(env.get("data"), dict) else False
        if text:
            try:
                cache_file.write_text(text, encoding="utf-8")
            except Exception:
                pass
        return text, {"method": "pdf-ocr", "ocr_used": ocr_used}
    except subprocess.TimeoutExpired:
        return "", {"method": "pdf-ocr", "ocr_used": False, "error": "timeout (600s)"}
    except Exception as e:
        return "", {"method": "pdf-ocr", "ocr_used": False, "error": str(e)}


def extract_text_from_docx(docx_path: str) -> Tuple[str, Dict[str, Any]]:
    """Use python-docx (try import) or unzip+xml fallback."""
    try:
        from docx import Document  # type: ignore
        doc = Document(docx_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text]
        return "\n".join(paragraphs), {"method": "python-docx"}
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write(f"[doc-triage] python-docx failed: {e}, falling back to xml\n")

    # Fallback: unzip + word/document.xml
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(docx_path) as z:
            with z.open("word/document.xml") as f:
                tree = ET.parse(f)
        root = tree.getroot()
        # Word namespace
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for p in root.iter(f"{ns}p"):
            texts = [t.text for t in p.iter(f"{ns}t") if t.text]
            if texts:
                paragraphs.append("".join(texts))
        return "\n".join(paragraphs), {"method": "docx-xml-fallback"}
    except Exception as e:
        return "", {"method": "none", "error": str(e)}


def extract_text_from_txt(txt_path: str) -> Tuple[str, Dict[str, Any]]:
    """Direct read with encoding detection."""
    p = Path(txt_path)
    raw = p.read_bytes()
    # Try utf-8, then cp1251, then latin-1
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc), {"method": f"direct-{enc}"}
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), {"method": "utf-8-replace"}


def extract_text(file_path: str, max_pages: int = 100) -> Tuple[str, Dict[str, Any]]:
    """Dispatch by extension."""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path, max_pages=max_pages)
    if ext in (".docx",):
        return extract_text_from_docx(file_path)
    if ext == ".doc":
        # Old .doc — try antiword if available, else fail gracefully
        antiword = shutil.which("antiword")
        if antiword:
            try:
                r = subprocess.run([antiword, file_path], capture_output=True, text=True, timeout=60)
                if r.returncode == 0:
                    return r.stdout, {"method": "antiword"}
            except Exception:
                pass
        return "", {"method": "none", "error": ".doc format requires antiword (not installed)"}
    if ext in (".txt", ".md", ".rtf"):
        return extract_text_from_txt(file_path)
    return "", {"method": "none", "error": f"unsupported extension: {ext}"}


# ─────────────────────────────────────────────────────────────────────
# Summarize via poler-toolkit
# ─────────────────────────────────────────────────────────────────────

def summarize_text(text: str) -> Tuple[Dict[str, Any], float]:
    """Run poler-toolkit ingest.py on extracted text. Returns (summary, elapsed)."""
    if not text or len(text) < 20:
        return {"theme": None, "keywords": [], "fallback": True}, 0.0
    t0 = time.time()
    try:
        # Pipe text via stdin
        cmd = [sys.executable, str(POLER_INGEST), "-", "--json"]
        r = subprocess.run(cmd, input=text, capture_output=True, text=True,
                           timeout=120, encoding="utf-8")
        if r.returncode != 0:
            sys.stderr.write(f"[doc-triage] poler-toolkit failed: {r.stderr[:200]}\n")
            return {"theme": None, "keywords": [], "fallback": True}, time.time() - t0
        env = json.loads(r.stdout)
        data = env.get("data") or {}
        theme_obj = data.get("theme") or {}
        keywords = data.get("keywords") or []
        return {
            "theme": theme_obj.get("name") or theme_obj.get("semantic"),
            "keywords": keywords[:10] if isinstance(keywords, list) else [],
            "clusters_count": len(data.get("clusters") or []),
            "fallback": False,
        }, time.time() - t0
    except Exception as e:
        sys.stderr.write(f"[doc-triage] summarize_text error: {e}\n")
        return {"theme": None, "keywords": [], "fallback": True}, time.time() - t0


# ─────────────────────────────────────────────────────────────────────
# Pattern 1 brief builder
# ─────────────────────────────────────────────────────────────────────

def format_brief_text(file_path: str, text: str, summary: Dict[str, Any],
                      extraction_meta: Dict[str, Any]) -> str:
    """3-5 line compact brief text."""
    lines = []
    p = Path(file_path)
    name = p.name
    ext = p.suffix.lower().lstrip(".")
    chars = len(text)
    method = extraction_meta.get("method", "unknown")
    ocr = extraction_meta.get("ocr_used", False)

    lines.append(f"📄 doc-triage: \"{name}\" ({ext})")
    lines.append(f"- extracted: {chars:,} chars via {method}"
                 + (" (OCR)" if ocr else ""))
    theme = summary.get("theme")
    kws = summary.get("keywords") or []
    if theme or kws:
        kw_str = ", ".join(kws[:5]) if kws else "—"
        lines.append(f"- theme: {theme or 'n/a'}, top keywords: {kw_str}")
    else:
        lines.append(f"- theme: n/a, first 200 chars: {text[:200]!r}")
    if kws:
        q1 = f"что документ говорит про {kws[0]}?"
        q2 = f"какие выводы про {kws[1]}?" if len(kws) > 1 else "каковы основные выводы?"
        lines.append(f"- suggested questions: \"{q1}\", \"{q2}\"")
    lines.append("→ agent has full text context, can answer directly")
    return "\n".join(lines)


def suggest_questions(kws: List[str]) -> List[str]:
    if not kws:
        return ["о чём этот документ?", "каковы основные выводы?"]
    qs = [f"что документ говорит про {kws[0]}?"]
    if len(kws) > 1:
        qs.append(f"какие аргументы про {kws[1]}?")
    qs.append("каковы основные выводы?")
    return qs


def calc_confidence(text: str, summary: Dict[str, Any]) -> float:
    if not text:
        return 0.0
    if len(text) < 100:
        return 0.2
    if summary.get("fallback"):
        return 0.5
    base = 0.75
    if summary.get("theme"):
        base += 0.1
    if summary.get("keywords"):
        base += 0.1
    return min(0.95, base)


# ─────────────────────────────────────────────────────────────────────
# Main triage
# ─────────────────────────────────────────────────────────────────────

def _error_envelope(msg: str, confidence: float = 0.0) -> Dict[str, Any]:
    return {"status": "error", "confidence": confidence, "data": None, "error": msg}


def triage(input_value: str, max_pages: int = 100,
           transient: bool = False) -> Dict[str, Any]:
    """Run full doc triage pipeline."""
    t_start = time.time()

    file_path: Optional[str] = None

    if input_value == "-":
        # Read message from stdin, extract first file path
        try:
            stdin_text = sys.stdin.read()
        except Exception:
            stdin_text = ""
        # Find first path that exists and has supported extension
        for m in re.finditer(r'[\w/\\\-\.]+\.(?:pdf|docx?|txt|md|rtf)', stdin_text, re.IGNORECASE):
            candidate = m.group(0)
            if Path(candidate).exists():
                file_path = candidate
                break
        if not file_path:
            return _error_envelope("No supported file path found in stdin message")
    elif Path(input_value).exists():
        file_path = input_value
    else:
        # Maybe input is a message containing a path
        for m in re.finditer(r'[\w/\\\-\.]+\.(?:pdf|docx?|txt|md|rtf)', input_value, re.IGNORECASE):
            candidate = m.group(0)
            if Path(candidate).exists():
                file_path = candidate
                break
        if not file_path:
            return _error_envelope(f"File not found: {input_value[:100]}")

    # 1. Extract text
    try:
        text, extraction_meta = extract_text(file_path, max_pages=max_pages)
    except Exception as e:
        return _error_envelope(f"Text extraction failed: {e}")

    if not text or len(text.strip()) < 10:
        return _error_envelope(
            f"Extraction returned empty text (file might be empty, scanned without OCR, or unsupported)",
            confidence=0.2,
        )

    # 2. Save extracted text for agent reference
    file_hash = hashlib.sha256(Path(file_path).read_bytes()[:65536]).hexdigest()[:16]
    text_path = CACHE_DIR / f"{file_hash}.txt"
    try:
        text_path.write_text(text, encoding="utf-8")
    except Exception:
        pass

    # 3. Summarize via poler-toolkit
    try:
        summary, summarize_elapsed = summarize_text(text)
    except Exception as e:
        summary = {"theme": None, "keywords": [], "fallback": True}
        summarize_elapsed = 0.0

    # 4. Build brief
    brief_text = format_brief_text(file_path, text, summary, extraction_meta)
    confidence = calc_confidence(text, summary)

    total_elapsed = time.time() - t_start

    # Pattern 1: source-grounded brief
    grounded = None
    if _HAS_PATTERN1:
        claims = []
        # Claim: extraction success
        claims.append(Claim(
            text=f"Extracted {len(text):,} chars from {Path(file_path).name} ({extraction_meta.get('method', '?')})",
            source="doc-triage",
            span=f"{file_path}:full",
            confidence=0.95,
            tags=["extraction"],
        ))
        # Claim: OCR usage
        if extraction_meta.get("ocr_used"):
            claims.append(Claim(
                text="PDF was scanned; OCR was used to extract text (may contain errors)",
                source="pdf-ocr",
                span=f"{file_path}:ocr",
                confidence=0.85,
                tags=["ocr"],
            ))
        # Claim: theme
        if summary.get("theme"):
            claims.append(Claim(
                text=f"Detected theme: {summary['theme']}",
                source="poler-toolkit",
                span=f"{text_path}:theme.name",
                confidence=0.85,
                tags=["theme"],
            ))
        # Claim: keywords
        kws = summary.get("keywords") or []
        if kws:
            claims.append(Claim(
                text=f"Top keywords: {', '.join(kws[:5])}",
                source="poler-toolkit",
                span=f"{text_path}:keywords[0:{min(5, len(kws))}]",
                confidence=0.8,
                tags=["keywords"],
            ))
        # Claim: full text available
        claims.append(Claim(
            text=f"Full extracted text available at {text_path} — agent can answer questions directly",
            source="doc-triage",
            span=str(text_path),
            confidence=confidence,
            tags=["full_text"],
        ))

        aspects_queried = ["extraction", "ocr", "theme", "keywords", "full_text", "entities"]
        aspects_covered = {t for c in claims for t in c.tags}

        try:
            grounded = build_brief(
                summary=brief_text,
                claims=claims,
                aspects_queried=aspects_queried,
                aspects_covered=sorted(aspects_covered),
                sources_used=2 if summary.get("theme") else 1,
                sources_total=2,
                transient=transient,
                extra={
                    "source_file": file_path,
                    "text_path": str(text_path),
                    "text_chars": len(text),
                    "theme": summary.get("theme"),
                    "keywords": kws,
                    "suggested_questions": suggest_questions(kws),
                    "extraction_meta": {
                        **extraction_meta,
                        "summarize_elapsed_sec": round(summarize_elapsed, 2),
                        "total_elapsed_sec": round(total_elapsed, 2),
                    },
                },
            )
        except Exception as e:
            sys.stderr.write(f"[doc-triage] grounded brief build failed: {e}\n")
            grounded = None

    if grounded is None:
        # Legacy fallback
        data = {
            "brief": brief_text,
            "source_file": file_path,
            "text_path": str(text_path),
            "text_chars": len(text),
            "theme": summary.get("theme"),
            "keywords": summary.get("keywords") or [],
            "suggested_questions": suggest_questions(summary.get("keywords") or []),
            "extraction_meta": {
                **extraction_meta,
                "summarize_elapsed_sec": round(summarize_elapsed, 2),
                "total_elapsed_sec": round(total_elapsed, 2),
            },
        }
    else:
        data = grounded

    return {
        "status": "success",
        "confidence": round(confidence, 2),
        "data": data,
        "error": None,
    }


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="doc-triage — extract brief from PDF/docx/txt/md file",
    )
    ap.add_argument("input", help="File path or '-' to read message from stdin")
    ap.add_argument("--json", action="store_true", help="Output JSON envelope")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--transient", action="store_true",
                    help="Pattern 5: mark this brief as transient")
    args = ap.parse_args()

    result = triage(args.input, max_pages=args.max_pages, transient=args.transient)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
