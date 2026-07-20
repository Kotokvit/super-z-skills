#!/usr/bin/env python3
"""
update_run_py_v2.py — Brute-force update all run.py to support --backend.

Strategy: rewrite the if __name__ block for every run.py that uses
llm_wrapper, preserving the skill name.
"""
from pathlib import Path
import re

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

updated = 0
skipped = 0
errors = []

for run_py in sorted(SKILLS_DIR.glob("*/scripts/run.py")):
    skill_name = run_py.parent.parent.name
    if skill_name.startswith("_"):
        continue

    content = run_py.read_text(encoding="utf-8", errors="ignore")

    # Already updated
    if "--backend" in content and "backend=args.backend" in content:
        skipped += 1
        continue

    # Not using llm_wrapper
    if "from llm_wrapper import run_skill" not in content:
        skipped += 1
        continue

    # Extract skill name from the existing run_skill() call
    match = re.search(r'run_skill\(\s*["\']([\w-]+)["\']', content)
    if not match:
        errors.append(f"{skill_name}: can't find run_skill() call")
        continue
    found_name = match.group(1)

    # Build the new if __name__ block
    new_main = f'''if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None, help="User query")
    ap.add_argument("--backend", default=None,
                    choices=["zai_cli", "sandbox", "mock"],
                    help="LLM backend: zai_cli (default), sandbox (internal agents), mock (placeholder)")
    args = ap.parse_args()
    result = run_skill("{found_name}", user_query=args.query, backend=args.backend)
    sys.exit(0 if result.get("status") == "success" else 1)
'''

    # Remove everything from "if __name__" to end of file, replace with new block
    idx = content.find('if __name__ == "__main__"')
    if idx < 0:
        errors.append(f"{skill_name}: no if __name__ block found")
        continue

    new_content = content[:idx] + new_main

    # Also remove any leftover "query = sys.argv[1]..." lines before __name__
    new_content = re.sub(r'\s*query = sys\.argv\[1\].*?\n', '\n', new_content)

    # Remove duplicate "import argparse" if it already exists in the header
    lines = new_content.split("\n")
    seen_argparse = False
    clean = []
    for line in lines:
        if line.strip() == "import argparse":
            if seen_argparse:
                continue
            seen_argparse = True
        clean.append(line)
    new_content = "\n".join(clean)

    run_py.write_text(new_content, encoding="utf-8")
    updated += 1

print(f"Updated: {updated}")
print(f"Skipped: {skipped}")
print(f"Errors: {len(errors)}")
for e in errors[:10]:
    print(f"  - {e}")
