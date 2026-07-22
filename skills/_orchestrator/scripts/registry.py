#!/usr/bin/env python3
"""
registry.py — Skill Registry for the _orchestrator skill.

Loads manifest.json from all sibling skills and provides lookup APIs.

Usage (as a module):
    from registry import SkillRegistry
    reg = SkillRegistry("str(Path(__file__).resolve().parents[2] / "skills")")
    manifest = reg.get_manifest("poler-toolkit")
    deps = reg.get_dependencies("poler-toolkit")
    matches = reg.find_by_extension(".pdf")
    matches = reg.find_by_keyword("analyze")

CLI:
    python3 registry.py list
    python3 registry.py show poler-toolkit
    python3 registry.py find .pdf
    python3 registry.py doctor

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class SkillRegistry:
    """Loads and indexes skill manifests from a skills directory."""

    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir)
        if not self.skills_dir.is_dir():
            raise NotADirectoryError(f"Skills dir not found: {self.skills_dir}")
        self.manifests: Dict[str, Dict[str, Any]] = {}
        # Indexes (built once at load time for O(1) lookup)
        self._by_extension: Dict[str, List[str]] = {}
        self._by_mime: Dict[str, List[str]] = {}
        self._by_keyword: Dict[str, List[str]] = {}
        self._by_category: Dict[str, List[str]] = {}
        self._load_all()

    # -----------------------------------------------------------------
    # Loading
    # -----------------------------------------------------------------

    def _load_all(self) -> None:
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith(".") or skill_dir.name.startswith("_"):
                # _orchestrator itself, hidden dirs
                continue
            manifest_path = skill_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                # Validate required fields
                for f in ("name", "version", "category", "priority"):
                    if f not in m:
                        sys.stderr.write(
                            f"[registry] WARNING: {skill_dir.name}/manifest.json "
                            f"missing required field '{f}' — skipping\n"
                        )
                        m = None
                        break
                if m is None:
                    continue
                self.manifests[m["name"]] = m
            except json.JSONDecodeError as e:
                sys.stderr.write(
                    f"[registry] WARNING: {skill_dir.name}/manifest.json "
                    f"invalid JSON: {e}\n"
                )
                continue

        # Build indexes
        for name, m in self.manifests.items():
            # Index by file extensions
            exts = m.get("triggers", {}).get("file_extensions", [])
            for ext in exts:
                ext_lower = ext.lower()
                self._by_extension.setdefault(ext_lower, []).append(name)

            # Index by MIME types
            mimes = m.get("triggers", {}).get("mime_types", [])
            for mt in mimes:
                self._by_mime.setdefault(mt.lower(), []).append(name)

            # Index by content keywords
            kws = m.get("triggers", {}).get("content_contains", [])
            for kw in kws:
                self._by_keyword.setdefault(kw.lower(), []).append(name)

            # Index by category
            cat = m.get("category", "uncategorized")
            self._by_category.setdefault(cat, []).append(name)

    # -----------------------------------------------------------------
    # Lookup APIs
    # -----------------------------------------------------------------

    def get_manifest(self, skill_name: str) -> Optional[Dict[str, Any]]:
        return self.manifests.get(skill_name)

    def list_skills(self) -> List[str]:
        return sorted(self.manifests.keys())

    def list_skills_by_category(self) -> Dict[str, List[str]]:
        return dict(self._by_category)

    def get_dependencies(self, skill_name: str) -> List[str]:
        m = self.get_manifest(skill_name)
        return m.get("dependencies", []) if m else []

    def get_cost(self, skill_name: str) -> Dict[str, Any]:
        m = self.get_manifest(skill_name)
        return m.get("cost", {}) if m else {}

    def get_priority(self, skill_name: str) -> int:
        m = self.get_manifest(skill_name)
        return m.get("priority", 50) if m else 50

    def find_by_extension(self, ext: str) -> List[str]:
        """Find skills that trigger on the given file extension."""
        if not ext.startswith("."):
            ext = "." + ext
        return sorted(self._by_extension.get(ext.lower(), []))

    def find_by_mime(self, mime: str) -> List[str]:
        return sorted(self._by_mime.get(mime.lower(), []))

    def find_by_keyword(self, kw: str) -> List[str]:
        """Find skills whose triggers contain the keyword (case-insensitive)."""
        return sorted(self._by_keyword.get(kw.lower(), []))

    def find_by_query(self, query: str) -> List[str]:
        """Find skills whose keywords appear in the query string."""
        q_lower = query.lower()
        matches: Dict[str, int] = {}
        for kw, skills in self._by_keyword.items():
            if kw in q_lower:
                for s in skills:
                    matches[s] = matches.get(s, 0) + 1
        # Sort by match count desc, then by priority desc
        result = sorted(matches.keys(),
                        key=lambda s: (-matches[s], -self.get_priority(s)))
        return result

    def get_entry_point(self, skill_name: str,
                        entry_name: str = "ingest") -> Optional[Path]:
        """Get the file path to a skill's entry point script."""
        m = self.get_manifest(skill_name)
        if not m:
            return None
        entry_points = m.get("entry_points", {})
        script_rel = entry_points.get(entry_name)
        if not script_rel:
            # Try common defaults
            script_rel = "scripts/main.py"
        return self.skills_dir / skill_name / script_rel

    def get_validator_path(self, skill_name: str) -> Optional[Path]:
        m = self.get_manifest(skill_name)
        if not m:
            return None
        v = m.get("validator", {})
        script = v.get("script", "validator.py")
        return self.skills_dir / skill_name / "scripts" / script

    def get_doctor_path(self, skill_name: str) -> Optional[Path]:
        m = self.get_manifest(skill_name)
        if not m:
            return None
        st = m.get("self_test", {})
        script = st.get("doctor_script", "doctor.py")
        return self.skills_dir / skill_name / "scripts" / script

    # -----------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        return {
            "total_skills": len(self.manifests),
            "categories": len(self._by_category),
            "extensions_indexed": len(self._by_extension),
            "mime_types_indexed": len(self._by_mime),
            "keywords_indexed": len(self._by_keyword),
        }

    def doctor(self) -> List[Dict[str, Any]]:
        """Run a quick consistency check across all manifests.

        Reports:
          - errors: blocking issues (entry_point declared but file missing)
          - warnings: non-blocking (validator/doctor not yet implemented)
        """
        results = []
        for name, m in self.manifests.items():
            errors = []
            warnings = []
            # Check entry_points point to existing files (only if any declared)
            for ep_name, ep_path in m.get("entry_points", {}).items():
                full = self.skills_dir / name / ep_path
                if not full.exists():
                    errors.append(f"entry_point {ep_name} → {ep_path} missing")
            # Check dependencies are satisfiable
            for dep in m.get("dependencies", []):
                if dep not in self.manifests:
                    errors.append(f"dependency '{dep}' not in registry")
            # Check validator exists (warning, not error — may not be implemented yet)
            v = m.get("validator", {}).get("script")
            if v:
                vpath = self.skills_dir / name / "scripts" / v
                if not vpath.exists():
                    warnings.append(f"validator script {v} not yet implemented")
            # Check doctor exists (warning)
            d = m.get("self_test", {}).get("doctor_script")
            if d:
                dpath = self.skills_dir / name / "scripts" / d
                if not dpath.exists():
                    warnings.append(f"doctor script {d} not yet implemented")

            if errors:
                status = "ERRORS"
            elif warnings:
                status = "WARN"
            else:
                status = "OK"

            results.append({
                "skill": name,
                "status": status,
                "errors": errors,
                "warnings": warnings,
            })
        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    project_root = Path(__file__).resolve().parents[3]
    default_skills = project_root / "skills"
    ap = argparse.ArgumentParser(description="Skill registry CLI")
    ap.add_argument("--skills-dir", default=str(default_skills))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all registered skills")
    p_show = sub.add_parser("show", help="Show full manifest for a skill")
    p_show.add_argument("name")

    p_find = sub.add_parser("find", help="Find skills by extension/keyword/mime")
    p_find.add_argument("query")
    p_find.add_argument("--mode", choices=["ext", "mime", "kw", "query"],
                        default="kw")

    sub.add_parser("categories", help="List skills grouped by category")
    sub.add_parser("stats", help="Print registry statistics")
    sub.add_parser("doctor", help="Check all manifests for consistency issues")

    args = ap.parse_args()
    reg = SkillRegistry(args.skills_dir)

    if args.cmd == "list":
        print(f"\n{len(reg.list_skills())} skills registered:\n")
        for s in reg.list_skills():
            m = reg.get_manifest(s)
            print(f"  {s:30s} v{m['version']:8s} pri={m['priority']:3d}  "
                  f"[{m['category']}]")
        return 0

    if args.cmd == "show":
        m = reg.get_manifest(args.name)
        if not m:
            print(f"Not found: {args.name}", file=sys.stderr)
            return 1
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "find":
        if args.mode == "ext":
            results = reg.find_by_extension(args.query)
        elif args.mode == "mime":
            results = reg.find_by_mime(args.query)
        elif args.mode == "kw":
            results = reg.find_by_keyword(args.query)
        else:
            results = reg.find_by_query(args.query)
        print(f"\nFound {len(results)} skill(s) for '{args.query}' ({args.mode}):\n")
        for s in results:
            m = reg.get_manifest(s)
            print(f"  {s:30s} pri={m['priority']:3d}  [{m['category']}]")
        return 0

    if args.cmd == "categories":
        for cat, skills in sorted(reg.list_skills_by_category().items()):
            print(f"\n  {cat} ({len(skills)}):")
            for s in skills:
                print(f"    - {s}")
        return 0

    if args.cmd == "stats":
        s = reg.stats()
        print(json.dumps(s, indent=2))
        return 0

    if args.cmd == "doctor":
        results = reg.doctor()
        ok = sum(1 for r in results if r["status"] == "OK")
        warn = sum(1 for r in results if r["status"] == "WARN")
        err = sum(1 for r in results if r["status"] == "ERRORS")
        print(f"\nRegistry doctor: {ok} OK, {warn} warnings, {err} errors\n")
        for r in results:
            icon = {"OK": "✓", "WARN": "⚠", "ERRORS": "✗"}[r["status"]]
            print(f"  {icon} {r['skill']:30s} {r['status']}")
            for issue in r.get("errors", []):
                print(f"      ✗ → {issue}")
            for issue in r.get("warnings", []):
                print(f"      ⚠ → {issue}")
        return 0 if err == 0 else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
