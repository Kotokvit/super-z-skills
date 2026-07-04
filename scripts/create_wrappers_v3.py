#!/usr/bin/env python3
"""
create_wrappers_v3.py — Final pass: 100% executable coverage.

Creates scripts/run.py for the remaining 16 docs_only skills so every skill
in the registry becomes executable. This achieves the architect's vision:
"100% coverage — one tool that covers all my powers".

Strategy per skill:
  - gap-detector: has its own gap_detector.py main entry — delegate directly
  - poler-psi, poler-toolkit: have their own scripts — use llm_wrapper fallback
  - All others: use _shared/llm_wrapper.py (SKILL.md → LLM system prompt → answer)
"""
import json
import os
from pathlib import Path

SKILLS_ROOT = Path("/home/z/my-project/skills")

# Skills that delegate to their own existing main script
DELEGATE_TO_MAIN = {
    "gap-detector": "gap_detector.py",
}

# Skills that use the LLM wrapper (read SKILL.md → LLM as system prompt → answer)
LLM_WRAPPER_SKILLS = [
    "aminer-academic-search",
    "aminer-daily-paper",
    "aminer-free-academic",
    "gaokao-collect-student-info",
    "gaokao-fetch-volunteers",
    "gaokao-generate-report",
    "gaokao-recommend-majors",
    "gaokao-recommend-schools",
    "ai-news-collectors",
    "qingyan-research",
    "poler-psi",
    "poler-toolkit",
    "web-shader-extractor",
    "skill-finder-cn",
    "fullstack-dev",
]


LLM_WRAPPER_CODE = '''#!/usr/bin/env python3
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


DELEGATE_CODE = '''#!/usr/bin/env python3
"""{skill} runner — delegates to scripts/{main_script}."""
import subprocess
import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parent / "{main_script}"


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else None
    cmd = [sys.executable, str(MAIN)]
    if query:
        cmd.append(query)
    else:
        cmd.append("-")
        # Read stdin and pass through
    if not query or query == "-":
        # Pipe stdin to subprocess
        proc = subprocess.run(cmd, stdin=sys.stdin)
    else:
        proc = subprocess.run(cmd)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
'''


def update_manifest(skill: str):
    manifest_path = SKILLS_ROOT / skill / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    changed = False
    if m.get("entry_point") != "scripts/run.py":
        m["entry_point"] = "scripts/run.py"
        changed = True
    if m.get("docs_only", True) is not False:
        m["docs_only"] = False
        changed = True
    if "entry_points" not in m:
        m["entry_points"] = {"default": "scripts/run.py"}
        changed = True
    tags = set(m.get("tags", []))
    if skill not in tags:
        m.setdefault("tags", []).append(skill)
        changed = True
    if changed:
        manifest_path.write_text(json.dumps(m, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return changed


def make_wrapper(skill: str, code: str) -> bool:
    skill_dir = SKILLS_ROOT / skill
    if not skill_dir.exists():
        return False
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    run_py = scripts_dir / "run.py"
    if run_py.exists():
        return False
    run_py.write_text(code, encoding="utf-8")
    run_py.chmod(0o755)
    return True


def main():
    print("Final pass: 100% executable coverage\n")

    created = 0
    skipped = 0

    # Delegate skills
    print("Delegate-to-main skills:")
    for skill, main_script in DELEGATE_TO_MAIN.items():
        # Verify main script exists
        main_path = SKILLS_ROOT / skill / "scripts" / main_script
        if not main_path.exists():
            print(f"  SKIP {skill:30s} ({main_script} not found)")
            skipped += 1
            continue
        code = DELEGATE_CODE.replace("{skill}", skill).replace("{main_script}", main_script)
        if make_wrapper(skill, code):
            update_manifest(skill)
            print(f"  CREATE {skill:30s} (delegates to {main_script})")
            created += 1
        else:
            print(f"  OK    {skill:30s} (already has run.py)")
            skipped += 1

    # LLM wrapper skills
    print("\nLLM wrapper skills:")
    for skill in LLM_WRAPPER_SKILLS:
        code = LLM_WRAPPER_CODE.replace("{skill}", skill)
        if make_wrapper(skill, code):
            update_manifest(skill)
            print(f"  CREATE {skill:30s} (LLM wrapper)")
            created += 1
        else:
            print(f"  OK    {skill:30s} (already has run.py)")
            skipped += 1

    print(f"\nDone. Created: {created}, Skipped: {skipped}")

    # Final count
    total = 0
    exec_count = 0
    for d in SKILLS_ROOT.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        if name in ("_orchestrator", "_shared"):
            continue
        if name.startswith("."):
            continue
        total += 1
        if (d / "scripts" / "run.py").exists():
            exec_count += 1
    print(f"\n=== FINAL STATUS ===")
    print(f"Total skills:   {total}")
    print(f"Executable:     {exec_count}")
    print(f"Coverage:       {exec_count * 100 / total:.1f}%")


if __name__ == "__main__":
    main()
