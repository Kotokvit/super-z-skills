#!/usr/bin/env python3
"""
register_remaining_skills.py — Generate manifest.json for ALL remaining
unregistered skills (41 of them). Each manifest is auto-generated from the
skill's SKILL.md frontmatter (name + description) plus heuristic category /
trigger / entry_point detection.

For skills with executable scripts → entry_point is set, docs_only=False.
For skills with only SKILL.md → docs_only=True, agent reads SKILL.md directly.

Generated manifests are minimal but valid: registry will load them, planner
can match them by keywords, and the agent can invoke them via the standard
entry_point convention.

Usage:
    python3 register_remaining_skills.py --dry-run    # show what would be generated
    python3 register_remaining_skills.py              # write manifests
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


# ─── Category inference by skill name keywords ──────────────────────────────
def infer_category(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ["academic", "aminer", "paper", "research", "qingyan"]):
        return "academic-research"
    if any(k in n for k in ["gaokao", "interview", "study", "quiz", "cheat"]):
        return "education"
    if any(k in n for k in ["resume", "jd", "job", "interview-prep"]):
        return "career"
    if any(k in n for k in ["blog", "seo", "content", "marketing", "writing"]):
        return "content-creation"
    if any(k in n for k in ["image", "video", "podcast", "storyboard", "design", "ui-ux", "visual"]):
        return "creative-media"
    if any(k in n for k in ["finance", "stock", "market"]):
        return "data-analysis"
    if any(k in n for k in ["dream", "fortune", "gift", "meditation", "anti-pua", "mindfulness"]):
        return "lifestyle-personal"
    if any(k in n for k in ["poler", "coding", "version", "task-review", "skill-creator", "skill-finder"]):
        return "tooling-meta"
    if any(k in n for k in ["fullstack", "auto-target"]):
        return "development"
    return "general"


# ─── Priority heuristic ─────────────────────────────────────────────────────
def infer_priority(name: str, has_entry: bool) -> int:
    """Higher priority = more likely to be selected when matched."""
    n = name.lower()
    # Premium / signature skills
    if n in ("poler-psi", "poler-toolkit"):
        return 80
    if any(k in n for k in ["academic", "aminer", "research", "qingyan"]):
        return 72
    if any(k in n for k in ["resume", "blog", "seo", "writing"]):
        return 70
    if has_entry:
        return 65
    return 50  # docs-only


# ─── Triggers heuristic by category / name ──────────────────────────────────
def infer_triggers(name: str, category: str) -> dict:
    n = name.lower()
    triggers = {
        "file_extensions": [],
        "url_schemes": [],
        "mime_types": [],
        "content_contains": [],
    }

    # File-extension triggers
    if "pdf" in n or "docx" in n:
        triggers["file_extensions"].extend([".pdf", ".docx", ".doc"])
    if "image" in n or "gift" in n or "design" in n or "ui-ux" in n or "visual" in n:
        triggers["file_extensions"].extend([".png", ".jpg", ".jpeg", ".gif", ".webp"])
    if "video" in n or "podcast" in n:
        triggers["file_extensions"].extend([".mp4", ".webm", ".mp3", ".wav"])
    if "blog" in n or "writing" in n or "storyboard" in n or "cheat" in n:
        triggers["file_extensions"].extend([".md", ".txt", ".markdown"])

    # URL scheme
    if any(k in n for k in ["academic", "aminer", "news", "market", "research"]):
        triggers["url_schemes"] = ["http", "https"]

    # Content keywords (Russian + English + Chinese where applicable)
    keyword_map = {
        "academic-research": ["academic", "paper", "论文", "论文", "исследовани", "paper search",
                              "literature", "literatura"],
        "education": ["gaokao", "高考", "quiz", "квиз", "study", "учеб", "interview",
                      "собеседование", "question"],
        "career": ["resume", "резюме", "CV", "job", "работа", "vacancy", "ваканс",
                   "interview prep", "JD", "job description"],
        "content-creation": ["blog", "пост", "article", "статья", "SEO", "content",
                             "контент", "marketing", "маркетинг"],
        "creative-media": ["image", "изображ", "video", "видео", "podcast", "подкаст",
                           "storyboard", "сценарий", "design", "дизайн", "UI", "UX"],
        "data-analysis": ["stock", "акци", "finance", "финанс", "market", "рынок",
                          "investment", "инвестиц"],
        "lifestyle-personal": ["dream", "сон", "fortune", "удача", "gift", "подар",
                               "meditation", "медитац", "mindfulness"],
        "tooling-meta": ["skill", "скилл", "version", "версия", "task review", "poler"],
        "development": ["fullstack", "code", "код", "Next.js", "auto target", "tracker"],
        "general": [],
    }
    triggers["content_contains"] = keyword_map.get(category, [])
    return triggers


# ─── Find main entry point ──────────────────────────────────────────────────
def find_entry_point(skill_dir: Path) -> str | None:
    """Find the main script for this skill.
    Heuristics:
      1. scripts/main.py
      2. scripts/<skill_name>.py
      3. scripts/index.ts
      4. scripts/<skill_name>.ts
      5. The first .py file in scripts/
      6. The first .ts file in scripts/
      7. The first .py file in the skill dir
    """
    if not (skill_dir / "scripts").exists():
        # Look for any .py or .ts directly in skill dir
        for p in sorted(skill_dir.glob("*.py")):
            return str(p.relative_to(skill_dir))
        for p in sorted(skill_dir.glob("*.ts")):
            return str(p.relative_to(skill_dir))
        return None

    scripts_dir = skill_dir / "scripts"
    candidates = [
        scripts_dir / "main.py",
        scripts_dir / f"{skill_dir.name}.py",
        scripts_dir / "index.ts",
        scripts_dir / f"{skill_dir.name}.ts",
        scripts_dir / "run.py",
        scripts_dir / "cli.py",
    ]
    for c in candidates:
        if c.exists():
            return str(c.relative_to(skill_dir))

    # Fallback: first .py / .ts in scripts/
    for p in sorted(scripts_dir.glob("*.py")):
        return str(p.relative_to(skill_dir))
    for p in sorted(scripts_dir.glob("*.ts")):
        return str(p.relative_to(skill_dir))

    return None


# ─── Parse SKILL.md frontmatter ─────────────────────────────────────────────
def parse_skill_md(skill_dir: Path) -> dict:
    """Extract name + description from SKILL.md YAML frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {"name": skill_dir.name, "description": ""}

    text = skill_md.read_text(encoding="utf-8", errors="ignore")
    # YAML frontmatter between --- and ---
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        # No frontmatter — use first H1 line or first non-empty line
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                return {"name": skill_dir.name, "description": line[2:].strip()}
            if line and not line.startswith("---"):
                return {"name": skill_dir.name, "description": line[:200]}
        return {"name": skill_dir.name, "description": ""}

    frontmatter = m.group(1)
    name = skill_dir.name
    description = ""
    for line in frontmatter.split("\n"):
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip().strip('"').strip("'")
    return {"name": name, "description": description}


# ─── Build manifest for a skill ─────────────────────────────────────────────
def build_manifest(skill_dir: Path) -> dict:
    skill_name = skill_dir.name
    parsed = parse_skill_md(skill_dir)
    entry_point = find_entry_point(skill_dir)
    docs_only = entry_point is None
    category = infer_category(skill_name)
    priority = infer_priority(skill_name, has_entry=not docs_only)
    triggers = infer_triggers(skill_name, category)

    # Determine language
    if entry_point and entry_point.endswith(".ts"):
        language = "typescript"
    elif entry_point and entry_point.endswith(".py"):
        language = "python"
    else:
        language = "markdown"

    description = parsed["description"] or f"Skill '{skill_name}' — see SKILL.md for details."

    return {
        "name": skill_name,
        "version": "1.0.0",
        "description": description,
        "category": category,
        "priority": priority,
        "author": "Auto-registered by register_remaining_skills.py",
        "language": language,
        "docs_only": docs_only,
        "entry_point": entry_point or "",
        "triggers": triggers,
        "inputs": {
            "schema": {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "User query or input path"
                    },
                    "json": {"type": "boolean", "default": True}
                }
            }
        },
        "outputs": {
            "schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "json": {"type": "object"}
                }
            }
        },
        "tags": [skill_name, category] + triggers.get("content_contains", [])[:5]
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be generated, don't write files")
    args = ap.parse_args()

    all_dirs = sorted([d for d in SKILLS_DIR.iterdir()
                       if d.is_dir() and d.name != "_orchestrator"])
    unregistered = [d for d in all_dirs if not (d / "manifest.json").exists()]

    print(f"Found {len(unregistered)} unregistered skills:")
    for d in unregistered:
        print(f"  - {d.name}")
    print()

    generated = 0
    skipped = 0
    for d in unregistered:
        try:
            manifest = build_manifest(d)
        except Exception as e:
            print(f"  ✗ {d.name}: ERROR building manifest: {e}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [DRY-RUN] {d.name}: docs_only={manifest['docs_only']} "
                  f"entry={manifest['entry_point'] or 'N/A'} "
                  f"cat={manifest['category']} pri={manifest['priority']}")
        else:
            out_path = d / "manifest.json"
            out_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            print(f"  ✓ {d.name}: wrote manifest.json "
                  f"(docs_only={manifest['docs_only']}, "
                  f"entry={manifest['entry_point'] or 'N/A'}, "
                  f"cat={manifest['category']}, pri={manifest['priority']})")
        generated += 1

    print()
    print(f"Generated: {generated} | Skipped: {skipped}")
    if not args.dry_run:
        # Verify
        from pathlib import Path
        new_count = sum(1 for d in SKILLS_DIR.iterdir()
                        if d.is_dir() and (d / "manifest.json").exists())
        print(f"Total skills with manifest now: {new_count}")


if __name__ == "__main__":
    main()
