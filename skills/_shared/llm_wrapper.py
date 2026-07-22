#!/usr/bin/env python3
"""
llm_wrapper.py — Universal LLM wrapper for docs-only skills.

Many skills in the registry consist of only a SKILL.md (methodology doc) without
any executable code. This wrapper turns such a skill into an executable by:

  1. Reading the skill's SKILL.md → using it as the SYSTEM prompt
  2. Routing the query through Local-First engine (free → cheap → paid)
  3. Wrapping the LLM response in a Pattern 1 source-grounded brief
     (single claim citing the SKILL.md as the source of methodology)

LOCAL-FIRST ROUTING (new default):
    The wrapper now uses super_z_core for routing:
    - LOCAL skills → Python/Bash (FREE, instant)
    - AI_REASONING skills → host callback (FREE) or CLI (PAID)
    - EXTERNAL_API skills → CLI only (PAID, no alternative)

    Set SUPER_Z_HOST_LLM_CALLBACK for free in-process execution.

Usage (called by each skill's run.py):
    from llm_wrapper import run_skill
    run_skill(
        skill_name="blog-writer",
        skill_dir="/home/z/my-project/skills/blog-writer",
        user_query=sys.argv[1] if len(sys.argv) > 1 else None,
    )

Or as a CLI directly:
    python3 llm_wrapper.py --skill blog-writer --query "write a post about X"

Backend selection:
    # Default: Local-First routing (recommended, saves money)
    python3 llm_wrapper.py --skill blog-writer --query "..."

    # Force z-ai CLI (paid)
    SUPER_Z_BACKEND=zai_cli python3 llm_wrapper.py --skill blog-writer --query "..."
    python3 llm_wrapper.py --skill blog-writer --query "..." --backend zai_cli

    # Sandbox mode: internal agent chain
    SUPER_Z_BACKEND=sandbox python3 llm_wrapper.py --skill blog-writer --query "..."
    python3 llm_wrapper.py --skill blog-writer --query "..." --backend sandbox

    # Mock mode: return placeholder
    python3 llm_wrapper.py --skill blog-writer --query "..." --backend mock

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
                   timeout_sec: int = 90) -> Optional[str]:
    """Call z-ai chat CLI. Returns the assistant's text content, or None on failure."""
    if not os.path.exists(Z_AI):
        sys.stderr.write(f"[llm_wrapper] z-ai binary not found at {Z_AI}\n")
        return None

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
        # z-ai CLI prints log lines (🚀 Initializing...) before the JSON envelope.
        # Find the first '{' and try to parse the OpenAI-style envelope.
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
        # Fallback: treat entire stdout as content
        return out.strip() if out.strip() else None

    except subprocess.TimeoutExpired:
        sys.stderr.write(f"[llm_wrapper] z-ai chat timed out ({timeout_sec}s)\n")
        return None
    except Exception as e:
        sys.stderr.write(f"[llm_wrapper] z-ai chat error: {e}\n")
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

def _detect_backend(override: Optional[str] = None) -> str:
    """Detect which LLM backend to use.

    LOCAL-FIRST priority (CHANGED: default is now "local-first", not "zai_cli"):
        1. Explicit override (CLI --backend flag)
        2. SUPER_Z_BACKEND env var
        3. Default: "local-first" (uses super_z_core routing)

    The old default "zai_cli" is now the LAST resort, not the first.
    """
    # 1. Explicit override
    if override:
        return override.lower()

    # 2. Environment variable
    env_backend = os.environ.get("SUPER_Z_BACKEND", "").lower()
    if env_backend in ("local-first", "super-z", "local", "core", "auto"):
        return "local-first"
    if env_backend in ("sandbox", "internal", "agents"):
        return "sandbox"
    if env_backend in ("mock", "test", "dry", "dummy"):
        return "mock"
    if env_backend in ("zai", "zai_cli", "z-ai"):
        return "zai_cli"

    # 3. Default: local-first (saves money!)
    return "local-first"


def _call_local_first(system_prompt: str, user_prompt: str,
                      skill_name: str, skill_md: str,
                      skill_dir: Optional[str] = None) -> Optional[str]:
    """Route through Local-First engine (super_z_core).

    This is the NEW default routing. Checks:
    1. Can we do this locally? (FREE)
    2. Is there a host AI callback? (FREE)
    3. Fall back to CLI (PAID)
    """
    try:
        # Import super_z_core
        core_path = Path(__file__).resolve().parent / "sandbox"
        if str(core_path) not in sys.path:
            sys.path.insert(0, str(core_path))
        from super_z_core import run_skill_local_first
        return run_skill_local_first(
            skill_name=skill_name,
            user_query=user_prompt,
            skill_md=skill_md,
            skill_dir=skill_dir,
            system_prompt=system_prompt,
        )
    except ImportError:
        sys.stderr.write("[llm_wrapper] super_z_core not available, falling back to sandbox\n")
        return _call_sandbox(system_prompt, user_prompt, skill_name, skill_md)
    except Exception as e:
        sys.stderr.write(f"[llm_wrapper] local-first error: {e}, falling back to sandbox\n")
        return _call_sandbox(system_prompt, user_prompt, skill_name, skill_md)


def _call_sandbox(system_prompt: str, user_prompt: str,
                  skill_name: str, skill_md: str) -> Optional[str]:
    """Call the internal sandbox backend instead of external LLM."""
    try:
        # Import sandbox bridge
        bridge_path = Path(__file__).resolve().parent / "sandbox"
        if str(bridge_path) not in sys.path:
            sys.path.insert(0, str(bridge_path))
        from bridge import call_sandbox_chat
        return call_sandbox_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            skill_name=skill_name,
            skill_md=skill_md,
        )
    except Exception as e:
        sys.stderr.write(f"[llm_wrapper] sandbox error: {e}\n")
        return None


def _call_mock(system_prompt: str, user_prompt: str,
               skill_name: str) -> str:
    """Return a mock placeholder response (for testing)."""
    return (
        f"[MOCK RESPONSE for {skill_name}]\n\n"
        f"Query: {user_prompt[:200]}\n\n"
        f"This is a placeholder response generated by the mock backend. "
        f"No external LLM was called. Enable 'sandbox' or 'zai_cli' backend "
        f"for real responses."
    )


def run_skill(skill_name: str, user_query: Optional[str] = None,
              json_output: bool = True,
              backend: Optional[str] = None) -> Dict[str, Any]:
    """Main entry point. Reads query from arg or stdin.

    Args:
        skill_name: Name of the skill to run.
        user_query: User's query text. If None or '-', reads from stdin.
        json_output: If True, print Pattern 1 brief JSON to stdout.
        backend: LLM backend override. One of: "zai_cli", "sandbox", "mock".
                 If None, auto-detected from env/config.
    """
    # Detect backend
    active_backend = _detect_backend(backend)

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

    # Route to the appropriate backend
    t0 = time.time()
    if active_backend == "local-first":
        llm_response = _call_local_first(
            system_prompt, user_prompt, skill_name, skill_md_text,
            str(skill_dir) if skill_dir else None,
        )
    elif active_backend == "sandbox":
        llm_response = _call_sandbox(
            system_prompt, user_prompt, skill_name, skill_md_text
        )
    elif active_backend == "mock":
        llm_response = _call_mock(system_prompt, user_prompt, skill_name)
    else:  # zai_cli (paid, last resort)
        llm_response = call_z_ai_chat(system_prompt, user_prompt, timeout_sec=120)
    elapsed = time.time() - t0

    if not llm_response:
        return {
            "skill": skill_name,
            "status": "error",
            "error": f"LLM call failed (backend={active_backend})",
            "metadata": {"elapsed_sec": round(elapsed, 2), "backend": active_backend},
        }

    brief = build_pattern1_brief(
        skill_name=skill_name,
        skill_dir=skill_dir,
        user_query=user_query,
        llm_response=llm_response,
        elapsed_sec=elapsed,
    )
    brief["status"] = "success"
    brief["metadata"]["backend"] = active_backend

    if json_output:
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    return brief


def main():
    ap = argparse.ArgumentParser(description="Run a docs-only skill via LLM wrapper")
    ap.add_argument("--skill", required=True, help="Skill name (e.g., blog-writer)")
    ap.add_argument("--query", default=None,
                    help="User query (if omitted, reads from stdin)")
    ap.add_argument("--json", action="store_true", default=True,
                    help="Output as Pattern 1 brief JSON (default)")
    ap.add_argument("--backend", default=None,
                    choices=["local-first", "zai_cli", "sandbox", "mock"],
                    help="LLM backend: local-first (default, saves money), zai_cli (paid), sandbox (internal agents), mock (placeholder)")
    args = ap.parse_args()

    result = run_skill(args.skill, user_query=args.query, json_output=args.json,
                       backend=args.backend)
    if "error" in result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
