#!/usr/bin/env python3
"""
update_run_py.py — Update all skills' run.py to support --backend flag.

Adds argparse with --backend choice to each run.py that uses the
standard llm_wrapper template.
"""
from pathlib import Path
import re

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

OLD_TEMPLATE = '''if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_skill(SKILL_NAME, user_query=query)
    sys.exit(0 if result.get("status") == "success" else 1)'''

NEW_TEMPLATE = '''if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None, help="User query")
    ap.add_argument("--backend", default=None,
                    choices=["zai_cli", "sandbox", "mock"],
                    help="LLM backend to use")
    args = ap.parse_args()
    result = run_skill(SKILL_NAME, user_query=args.query, backend=args.backend)
    sys.exit(0 if result.get("status") == "success" else 1)'''

updated = 0
skipped = 0
errors = []

for run_py in sorted(SKILLS_DIR.glob("*/scripts/run.py")):
    skill_name = run_py.parent.parent.name
    if skill_name.startswith("_"):
        continue

    content = run_py.read_text(encoding="utf-8", errors="ignore")

    # Check if already updated
    if "--backend" in content:
        skipped += 1
        continue

    # Check if this is a standard llm_wrapper template
    if "from llm_wrapper import run_skill" not in content:
        skipped += 1
        continue

    # Replace the old pattern
    new_content = content.replace(
        'query = sys.argv[1] if len(sys.argv) > 1 else None',
        ''
    )

    # Replace the run_skill call and main block
    new_content = re.sub(
        r'result = run_skill\("(.*?)",\s*user_query=query\)',
        r'result = run_skill("\1", user_query=args.query, backend=args.backend)',
        new_content
    )

    # Replace the if __name__ block
    new_content = re.sub(
        r'if __name__ == "__main__":\s*\n\s*query = .*?\n\s*result = run_skill.*?\n\s*sys\.exit.*',
        f'''if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None, help="User query")
    ap.add_argument("--backend", default=None,
                    choices=["zai_cli", "sandbox", "mock"],
                    help="LLM backend to use")
    args = ap.parse_args()
    result = run_skill("{skill_name}", user_query=args.query, backend=args.backend)
    sys.exit(0 if result.get("status") == "success" else 1)''',
        new_content,
        flags=re.DOTALL
    )

    # Remove duplicate import argparse if it got added
    lines = new_content.split("\n")
    seen_argparse = False
    clean_lines = []
    for line in lines:
        if "import argparse" in line:
            if seen_argparse:
                continue
            seen_argparse = True
        clean_lines.append(line)
    new_content = "\n".join(clean_lines)

    # Verify the change worked
    if "--backend" in new_content and "backend=args.backend" in new_content:
        run_py.write_text(new_content, encoding="utf-8")
        updated += 1
    else:
        errors.append(f"{skill_name}: auto-update failed, manual update needed")

print(f"Updated: {updated}")
print(f"Skipped: {skipped}")
print(f"Errors: {len(errors)}")
for e in errors:
    print(f"  - {e}")
