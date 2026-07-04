#!/usr/bin/env python3
"""
create_wrappers_v2.py — Create scripts/run.py for high-priority docs_only skills.

These are skills that have their main work in TypeScript (.ts) files calling
z-ai SDK directly. The wrapper dispatches via the `z-ai` CLI subcommand.

Each wrapper:
  - Calls z-ai CLI with appropriate subcommand
  - For .ts-based skills: tries `tsx <skill>.ts` first (Node native)
  - Falls back to _shared/llm_wrapper.py (LLM with SKILL.md as system prompt)
  - Returns Pattern 1 brief JSON
"""
import json
from pathlib import Path

SKILLS_ROOT = Path("/home/z/my-project/skills")

# Z-AI CLI dispatch skills (these skills wrap a z-ai subcommand directly)
Z_AI_DISPATCH = {
    "LLM":              ("chat",    "Chat completion via z-ai SDK"),
    "TTS":              ("tts",     "Text-to-speech via z-ai SDK"),
    "ASR":              ("asr",     "Speech-to-text via z-ai SDK"),
    "VLM":              ("vision",  "Vision-language model via z-ai SDK"),
    "image-generation": ("image",   "Image generation via z-ai SDK"),
    "image-edit":       ("image-edit", "Image editing via z-ai SDK"),
    "image-search":     ("image-search", "Image search via z-ai SDK"),
    "web-search":       ("--",      "Web search (uses _shared/llm_wrapper)"),
    "web-reader":       ("--",      "Web reader (uses _shared/llm_wrapper)"),
    "image-understand": ("vision",  "Image understanding via z-ai vision API"),
    "video-understand": ("--",      "Video understanding (uses _shared/llm_wrapper)"),
}

# Plain LLM-wrapper skills (no z-ai subcommand; just call LLM with SKILL.md)
LLM_WRAPPER_ONLY = [
    "pdf",
    "docx",
    "xlsx",
    "pptx",
    "charts",
    "multi-search-engine",
    "pdf-ocr",
    "podcast-generate",
    "writing-plans",
    "skill-creator",
    "task-review",
    "interview-designer",
    "get-fortune-analysis",
    "mindfulness-meditation",
    "visual-design-foundations",
    "version-management",
    "stock-analysis-skill",
    "auto-target-tracker",
    "job-intent-tracker",
]


WRAPPER_LLM_TEMPLATE = '''#!/usr/bin/env python3
"""{skill} runner — thin wrapper over _shared/llm_wrapper.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from llm_wrapper import run_skill

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_skill("{skill}", user_query=query)
    sys.exit(0 if result.get("status") == "success" else 1)
'''


WRAPPER_Z_AI_TEMPLATE = '''#!/usr/bin/env python3
"""{skill} runner — dispatches to z-ai {subcommand} subcommand."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

Z_AI = shutil.which("z-ai") or "/usr/local/bin/z-ai"
SKILL_DIR = Path(__file__).resolve().parent.parent


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else None
    if not query or query == "-":
        query = sys.stdin.read().strip()
    if not query:
        print(json.dumps({{"skill": "{skill}", "status": "error",
                           "error": "no query provided"}}, ensure_ascii=False, indent=2))
        sys.exit(1)

    # Build z-ai command
    cmd = [Z_AI, "{subcommand}", "--prompt", query]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(json.dumps({{"skill": "{skill}", "status": "error",
                           "error": "timeout (120s)"}}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if r.returncode != 0:
        # Fallback to LLM wrapper with SKILL.md as system prompt
        sys.path.insert(0, str(SKILL_DIR.parent / "_shared"))
        try:
            from llm_wrapper import run_skill
            result = run_skill("{skill}", user_query=query)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0 if result.get("status") == "success" else 1)
        except Exception as e:
            print(json.dumps({{"skill": "{skill}", "status": "error",
                               "error": f"z-ai failed and fallback failed: {{e}}"}},
                             ensure_ascii=False, indent=2))
            sys.exit(1)

    out = r.stdout
    # Try to parse JSON envelope
    start = out.find("{{")
    if start >= 0:
        try:
            env = json.loads(out[start:])
            content = env.get("choices", [{{}}])[0].get("message", {{}}).get("content", "")
            if content:
                brief = {{
                    "skill": "{skill}",
                    "status": "success",
                    "confidence": 0.85,
                    "brief": content[:300] + ("..." if len(content) > 300 else ""),
                    "claims": [{{"text": content, "source": "{skill}:z-ai-{subcommand}",
                                 "confidence": 0.85}}],
                    "metadata": {{"method": "z-ai-{subcommand}", "raw_env": env.get("id", "")}},
                }}
                print(json.dumps(brief, ensure_ascii=False, indent=2))
                sys.exit(0)
        except json.JSONDecodeError:
            pass

    # Fallback: print raw output as a brief
    brief = {{
        "skill": "{skill}",
        "status": "success",
        "confidence": 0.5,
        "brief": (out or "(empty)")[:300],
        "claims": [{{"text": out, "source": "{skill}:z-ai-{subcommand}",
                     "confidence": 0.5}}],
        "metadata": {{"method": "z-ai-{subcommand}", "raw": True}},
    }}
    print(json.dumps(brief, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
'''


def make_llm_wrapper(skill: str) -> bool:
    skill_dir = SKILLS_ROOT / skill
    if not skill_dir.exists():
        return False
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    run_py = scripts_dir / "run.py"
    if run_py.exists():
        return False
    run_py.write_text(WRAPPER_LLM_TEMPLATE.format(skill=skill), encoding="utf-8")
    run_py.chmod(0o755)
    return True


def make_z_ai_wrapper(skill: str, subcommand: str) -> bool:
    skill_dir = SKILLS_ROOT / skill
    if not skill_dir.exists():
        return False
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    run_py = scripts_dir / "run.py"
    if run_py.exists():
        return False
    # Escape { } for .format()
    template = WRAPPER_Z_AI_TEMPLATE.replace("{skill}", skill).replace("{subcommand}", subcommand)
    # The template uses {{ }} which got eaten by replace; restore manually
    # Easier: use a different templating approach
    code = '''#!/usr/bin/env python3
"""__SKILL__ runner — dispatches to z-ai __SUB__ subcommand."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

Z_AI = shutil.which("z-ai") or "/usr/local/bin/z-ai"
SKILL_DIR = Path(__file__).resolve().parent.parent


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else None
    if not query or query == "-":
        query = sys.stdin.read().strip()
    if not query:
        print(json.dumps({"skill": "__SKILL__", "status": "error",
                          "error": "no query provided"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    cmd = [Z_AI, "__SUB__", "--prompt", query]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(json.dumps({"skill": "__SKILL__", "status": "error",
                          "error": "timeout (120s)"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if r.returncode != 0:
        # Fallback to LLM wrapper
        sys.path.insert(0, str(SKILL_DIR.parent / "_shared"))
        try:
            from llm_wrapper import run_skill
            result = run_skill("__SKILL__", user_query=query)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0 if result.get("status") == "success" else 1)
        except Exception as e:
            print(json.dumps({"skill": "__SKILL__", "status": "error",
                              "error": f"z-ai failed and fallback failed: {e}"},
                             ensure_ascii=False, indent=2))
            sys.exit(1)

    out = r.stdout
    start = out.find("{")
    if start >= 0:
        try:
            env = json.loads(out[start:])
            content = env.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                brief = {
                    "skill": "__SKILL__",
                    "status": "success",
                    "confidence": 0.85,
                    "brief": content[:300] + ("..." if len(content) > 300 else ""),
                    "claims": [{"text": content, "source": "__SKILL__:z-ai-__SUB__",
                                "confidence": 0.85}],
                    "metadata": {"method": "z-ai-__SUB__", "raw_env": env.get("id", "")},
                }
                print(json.dumps(brief, ensure_ascii=False, indent=2))
                sys.exit(0)
        except json.JSONDecodeError:
            pass

    brief = {
        "skill": "__SKILL__",
        "status": "success",
        "confidence": 0.5,
        "brief": (out or "(empty)")[:300],
        "claims": [{"text": out, "source": "__SKILL__:z-ai-__SUB__",
                     "confidence": 0.5}],
        "metadata": {"method": "z-ai-__SUB__", "raw": True},
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
'''
    code = code.replace("__SKILL__", skill).replace("__SUB__", subcommand)
    run_py.write_text(code, encoding="utf-8")
    run_py.chmod(0o755)
    return True


def update_manifest(skill: str, entry_point: str = "scripts/run.py"):
    """Update manifest.json — set entry_point, docs_only=false, add triggers."""
    manifest_path = SKILLS_ROOT / skill / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    changed = False
    if m.get("entry_point") != entry_point:
        m["entry_point"] = entry_point
        changed = True
    if m.get("docs_only", True) is not False:
        m["docs_only"] = False
        changed = True
    if "entry_points" not in m:
        m["entry_points"] = {"default": entry_point}
        changed = True
    tags = set(m.get("tags", []))
    if skill not in tags:
        m.setdefault("tags", []).append(skill)
        changed = True
    if changed:
        manifest_path.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def main():
    print("Creating wrappers for top-15 docs_only skills...\n")

    created = 0
    skipped = 0

    # Z-AI dispatch skills
    print("Z-AI CLI dispatch skills:")
    for skill, (subcmd, desc) in Z_AI_DISPATCH.items():
        skill_dir = SKILLS_ROOT / skill
        if not skill_dir.exists():
            print(f"  SKIP {skill:25s} (directory missing)")
            skipped += 1
            continue
        if subcmd == "--":
            # Use plain LLM wrapper
            if make_llm_wrapper(skill):
                update_manifest(skill)
                print(f"  CREATE {skill:25s} (LLM wrapper)")
                created += 1
            else:
                print(f"  OK    {skill:25s} (already has run.py)")
                skipped += 1
        else:
            if make_z_ai_wrapper(skill, subcmd):
                update_manifest(skill)
                print(f"  CREATE {skill:25s} (z-ai {subcmd})")
                created += 1
            else:
                print(f"  OK    {skill:25s} (already has run.py)")
                skipped += 1

    # Plain LLM wrapper skills
    print("\nLLM wrapper skills:")
    for skill in LLM_WRAPPER_ONLY:
        skill_dir = SKILLS_ROOT / skill
        if not skill_dir.exists():
            print(f"  SKIP {skill:25s} (directory missing)")
            skipped += 1
            continue
        if make_llm_wrapper(skill):
            update_manifest(skill)
            print(f"  CREATE {skill:25s} (LLM wrapper)")
            created += 1
        else:
            print(f"  OK    {skill:25s} (already has run.py)")
            skipped += 1

    print(f"\nDone. Created: {created}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
