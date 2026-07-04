#!/usr/bin/env python3
"""
fix_manifests_for_wrappers.py — Update manifest.json for skills that just got
a scripts/run.py wrapper. Set entry_point, docs_only=false, ensure triggers.
"""
import json
from pathlib import Path

SKILLS_ROOT = Path("/home/z/my-project/skills")

WRAPPED_SKILLS = [
    "quiz-html", "marketing-mode", "gift-evaluator", "ui-ux-pro-max",
    "anti-pua", "coding-agent", "content-strategy", "contentanalysis",
    "design", "doc-triage", "cheat-sheet", "finance", "agent-browser",
]

# Per-skill trigger phrases (Russian + English)
SKILL_TRIGGERS = {
    "quiz-html":            ["quiz", "квиз", "тест", "викторина", "test", "questions"],
    "marketing-mode":       ["marketing", "маркетинг", "campaign", "кампания", "реклама", "ad copy"],
    "gift-evaluator":       ["gift", "подарок", "подарки", "present", "идеи подарков"],
    "ui-ux-pro-max":        ["UI", "UX", "design", "дизайн", "интерфейс", "wireframe", "прототип"],
    "anti-pua":             ["PUA", "манипуляция", "газлайтинг", "toxic", "токсичн"],
    "coding-agent":         ["code", "код", "программ", "function", "функция", "debug", "дебаг"],
    "content-strategy":     ["content strategy", "контент-стратег", "контент план", "editorial"],
    "contentanalysis":      ["analyze content", "анализ контент", "content audit"],
    "design":               ["design", "дизайн", "visual", "визуальн", "layout", "макет"],
    "doc-triage":           ["triage", "сортировк", "prioritize docs", "приоритизируй"],
    "cheat-sheet":          ["cheat sheet", "шпаргалк", "reference card", "сводк"],
    "finance":              ["finance", "финанс", "budget", "бюджет", "инвестиц", "invest"],
    "agent-browser":        ["browse", "браузер", "scrape", "website", "сайт"],
}


def main():
    fixed = 0
    for skill in WRAPPED_SKILLS:
        manifest_path = SKILLS_ROOT / skill / "manifest.json"
        if not manifest_path.exists():
            print(f"SKIP {skill} — no manifest.json")
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"SKIP {skill} — invalid JSON: {e}")
            continue

        changed = False

        # Fix entry_point
        if manifest.get("entry_point") != "scripts/run.py":
            manifest["entry_point"] = "scripts/run.py"
            changed = True

        # Fix docs_only
        if manifest.get("docs_only", True) is not False:
            manifest["docs_only"] = False
            changed = True

        # Add entry_points map
        if "entry_points" not in manifest or manifest["entry_points"].get("default") != "scripts/run.py":
            manifest["entry_points"] = {"default": "scripts/run.py"}
            changed = True

        # Enrich triggers
        triggers = manifest.setdefault("triggers", {})
        extra = SKILL_TRIGGERS.get(skill, [])
        existing = set(triggers.get("content_contains", []))
        for kw in extra:
            if kw not in existing:
                triggers.setdefault("content_contains", []).append(kw)
                existing.add(kw)
                changed = True

        # Tags
        tags = set(manifest.get("tags", []))
        if skill not in tags:
            manifest.setdefault("tags", []).append(skill)
            changed = True

        if changed:
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"FIX   {skill}")
            fixed += 1
        else:
            print(f"OK    {skill}")

    print(f"\nDone. Fixed: {fixed}")


if __name__ == "__main__":
    main()
