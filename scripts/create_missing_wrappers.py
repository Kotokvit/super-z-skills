#!/usr/bin/env python3
"""
create_missing_wrappers.py — Create scripts/run.py for skills that lack one.

Each run.py is a thin ~10-line wrapper that delegates to _shared/llm_wrapper.py.
This turns docs_only skills into executables that the watcher can dispatch.
"""
from pathlib import Path

SKILLS_ROOT = Path("/home/z/my-project/skills")

WRAPPER_TEMPLATE = '''#!/usr/bin/env python3
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

# Top-priority docs_only skills that need wrappers
TARGET_SKILLS = [
    # Tier 1 — high demand, frequently triggered
    "quiz-html",
    "marketing-mode",
    "gift-evaluator",
    "ui-ux-pro-max",
    # Tier 2 — useful analytical / utility skills
    "anti-pua",
    "coding-agent",
    "content-strategy",
    "contentanalysis",
    "design",
    "doc-triage",
    "cheat-sheet",
    "finance",
    # Tier 3 — bonus coverage
    "agent-browser",
]


def main():
    created = 0
    skipped = 0
    for skill in TARGET_SKILLS:
        skill_dir = SKILLS_ROOT / skill
        if not skill_dir.exists():
            print(f"SKIP  {skill}  (directory does not exist)")
            skipped += 1
            continue

        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        run_py = scripts_dir / "run.py"
        if run_py.exists():
            print(f"OK    {skill}  (run.py already exists)")
            skipped += 1
            continue

        run_py.write_text(WRAPPER_TEMPLATE.format(skill=skill), encoding="utf-8")
        run_py.chmod(0o755)
        print(f"CREATE {skill}  -> scripts/run.py")
        created += 1

    print(f"\nDone. Created: {created}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
