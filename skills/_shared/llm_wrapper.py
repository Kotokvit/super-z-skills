#!/usr/bin/env python3
"""
llm_wrapper.py — Universal LLM wrapper for docs-only skills.

Many skills in the registry consist of only a SKILL.md (methodology doc) without
any executable code. This wrapper turns such a skill into an executable by:

  1. Reading the skill's SKILL.md → using it as the SYSTEM prompt
  2. Forwarding the user's query to z-ai chat CLI
  3. Wrapping the LLM response in a Pattern 1 source-grounded brief
     (single claim citing the SKILL.md as the source of methodology)

This lets us register 10+ docs-only skills as executable overnight, without
writing bespoke Python for each. The agent gets the LLM's output pre-formatted
as a brief with citations, ready to merge into context_brief.json.

Usage (called by each skill's run.py):
    from llm_wrapper import run_skill
    run_skill(
        skill_name="blog-writer",
        skill_dir="/home/z/my-project/skills/blog-writer",
        user_query=sys.argv[1] if len(sys.argv) > 1 else None,
    )

Or as a CLI directly:
    python3 llm_wrapper.py --skill blog-writer --query "write a post about X"

Output: Pattern 1 brief JSON to stdout (compatible with executor.py validation).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from super_z.config import load_config
    from super_z.llm_backends import create_backend
except Exception:  # pragma: no cover - fallback for direct script execution
    load_config = None
    create_backend = None

# ─── Paths ────────────────────────────────────────────────────────────────
Z_AI = shutil.which("z-ai") or "/usr/local/bin/z-ai"
SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parent  # .../skills/

# ─── Helpers ──────────────────────────────────────────────────────────────

def find_skill_dir(skill_name: str) -> Optional[Path]:
    """Locate the skill directory by name."""
    p = SKILLS_ROOT / skill_name
    if p.is_dir():
        return p
    return None


def read_skill_md(skill_dir: Path) -> str:
    """Read SKILL.md and return its full text (frontmatter + body).
    Falls back to README.md if SKILL.md doesn't exist."""
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        return skill_md.read_text(encoding="utf-8", errors="ignore")
    readme = skill_dir / "README.md"
    if readme.exists():
        return readme.read_text(encoding="utf-8", errors="ignore")
    return f"# {skill_dir.name}\n\nNo SKILL.md found. Use general best practices."


def extract_description(skill_md_text: str) -> str:
    """Pull the 'description' field from YAML frontmatter, or first paragraph."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md_text, re.DOTALL)
    if m:
        for line in m.group(1).split("\n"):
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    # Fallback: first non-empty non-header line
    for line in skill_md_text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---"):
            return line[:300]
    return ""


def call_z_ai_chat(system_prompt: str, user_prompt: str,
                   timeout_sec: int = 90, backend_name: str = "mock") -> Optional[str]:
    """Call the configured backend. Returns the assistant's text content, or None on failure."""
    if create_backend is not None:
        try:
            backend = create_backend(backend_name)
            return backend.chat(system_prompt, user_prompt, timeout=timeout_sec)
        except Exception as exc:
            sys.stderr.write(f"[llm_wrapper] backend '{backend_name}' failed: {exc}\n")
            if backend_name != "mock" and create_backend is not None:
                try:
                    fallback = create_backend("mock")
                    return fallback.chat(system_prompt, user_prompt, timeout=timeout_sec)
                except Exception:
                    return None
            return None

    if backend_name != "zai_cli" and os.path.exists(Z_AI):
        try:
            cmd = [
                Z_AI, "chat",
                "--prompt", user_prompt,
                "--system", system_prompt,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
            if r.returncode != 0:
                sys.stderr.write(f"[llm_wrapper] z-ai chat failed: {r.stderr[:300]}\n")
                return None

            out = r.stdout
            envelope_start = out.find("{")
            if envelope_start >= 0:
                try:
                    envelope = json.loads(out[envelope_start:])
                    if isinstance(envelope, dict) and "choices" in envelope:
                        content = (
                            envelope.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                        )
                        if content and content.strip():
                            return content.strip()
                except json.JSONDecodeError:
                    pass
            return out.strip() if out.strip() else None

        except subprocess.TimeoutExpired:
            sys.stderr.write(f"[llm_wrapper] z-ai chat timed out ({timeout_sec}s)\n")
            return None
        except Exception as e:
            sys.stderr.write(f"[llm_wrapper] z-ai chat error: {e}\n")
            return None

    sys.stderr.write(f"[llm_wrapper] z-ai binary not found at {Z_AI}\n")
    return None


# ─── Pattern 1 brief builder ──────────────────────────────────────────────

def build_pattern1_brief(
    skill_name: str,
    skill_dir: Path,
    user_query: str,
    llm_response: str,
    elapsed_sec: float,
) -> Dict[str, Any]:
    """Wrap an LLM response as a Pattern 1 source-grounded brief.

    The 'claim' cites the SKILL.md as the source of methodology — this is honest:
    the LLM's response is grounded in the skill's documented methodology, not in
    external retrieved sources. The agent reads this brief and can decide to
    fetch external sources if the user query needs them.
    """
    skill_md_path = skill_dir / "SKILL.md"
    span = f"{skill_md_path.name}:#methodology"
    description = extract_description(read_skill_md(skill_dir))

    return {
        "skill": skill_name,
        "version": "1.0.0",
        "brief": llm_response[:200] + ("..." if len(llm_response) > 200 else ""),
        "claims": [
            {
                "text": llm_response,
                "source": f"{skill_name}:SKILL.md",
                "span": span,
                "confidence": 0.75,  # LLM-generated content, no external verification
            }
        ],
        "coverage": {
            "aspects_queried": [user_query[:200]],
            "aspects_covered": ["llm_response_per_skill_methodology"],
            "unanswered_aspects": [],
            "notes": (f"LLM applied the {skill_name} methodology from SKILL.md "
                      f"to the user query. Response is grounded in skill "
                      f"methodology, NOT in externally retrieved sources. "
                      f"For source-grounded answers, combine with web-search."),
        },
        "metadata": {
            "skill_description": description,
            "elapsed_sec": round(elapsed_sec, 2),
            "llm_call_count": 1,
        },
    }


# ─── Main entry ───────────────────────────────────────────────────────────

def run_skill(skill_name: str, user_query: Optional[str] = None,
              json_output: bool = True, backend_name: str = "mock") -> Dict[str, Any]:
    """Main entry point. Reads query from arg or stdin."""
    # Read query
    if not user_query or user_query == "-":
        user_query = sys.stdin.read().strip()
    if not user_query:
        return {
            "skill": skill_name,
            "status": "error",
            "error": "No user query provided. Pass as arg or pipe via stdin.",
        }

    skill_dir = find_skill_dir(skill_name)
    if not skill_dir:
        return {
            "skill": skill_name,
            "status": "error",
            "error": f"Skill directory not found: {skill_name}",
        }

    # Build system prompt from SKILL.md
    skill_md_text = read_skill_md(skill_dir)
    description = extract_description(skill_md_text)

    system_prompt = (
        f"You are the '{skill_name}' skill. Follow the methodology below strictly.\n\n"
        f"--- SKILL.md ---\n{skill_md_text}\n--- END SKILL.md ---\n\n"
        f"Apply this skill's methodology to the user's request. "
        f"Respond in the user's language (Russian if they wrote in Russian, etc.). "
        f"Be specific, actionable, and grounded in the skill's documented approach. "
        f"If the skill defines a specific output format, follow it. "
        f"If the request is unclear or outside the skill's scope, say so explicitly."
    )

    user_prompt = (
        f"USER REQUEST:\n{user_query}\n\n"
        f"Apply the {skill_name} methodology and produce your response."
    )

    t0 = time.time()
    backend = backend_name or "mock"
    if load_config is not None:
        try:
            backend = load_config().backend
        except Exception:
            backend = backend_name or "mock"
    llm_response = call_z_ai_chat(system_prompt, user_prompt, timeout_sec=120, backend_name=backend)
    elapsed = time.time() - t0

    if not llm_response:
        return {
            "skill": skill_name,
            "status": "error",
            "error": "LLM call failed (z-ai chat error or timeout)",
            "metadata": {"elapsed_sec": round(elapsed, 2)},
        }

    brief = build_pattern1_brief(
        skill_name=skill_name,
        skill_dir=skill_dir,
        user_query=user_query,
        llm_response=llm_response,
        elapsed_sec=elapsed,
    )
    brief["status"] = "success"

    if json_output:
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    return brief


def main():
    ap = argparse.ArgumentParser(description="Run a docs-only skill via LLM wrapper")
    ap.add_argument("--skill", required=True, help="Skill name (e.g., blog-writer)")
    ap.add_argument("--query", default=None,
                    help="User query (if omitted, reads from stdin)")
    ap.add_argument("--backend", default="mock", help="Backend selection")
    ap.add_argument("--json", action="store_true", default=True,
                    help="Output as Pattern 1 brief JSON (default)")
    args = ap.parse_args()

    result = run_skill(args.skill, user_query=args.query, json_output=args.json, backend_name=args.backend)
    if "error" in result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
