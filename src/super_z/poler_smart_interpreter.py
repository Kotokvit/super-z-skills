"""
poler_smart_interpreter.py — Smart Python interpreter.

Architecture (user's vision):
    [Python ast.walk] -> finds dangerous patterns deterministically
           | positions
    [POLER v3.0] -> provides semantic context (fragment, resonance)
           |
    [Rule-based fixer] -> deterministic patches (NO LLM!)
           |
    Report -> AI/human architect accepts/rejects

Rule system (NEW):
    Rules are NOT hardcoded in this file. They live in:
        smart_rules/schema.yaml
    Rules are COMPILED at runtime by:
        smart_rules/generator.py + smart_rules/ast_adapter.py
    Rules are CACHED by:
        smart_rules/cache.py
    When the interpreter or its version changes, the cache invalidates
    automatically and rules re-compile from the schema. The schema file
    stays stable across interpreter versions.

Public API:
    from super_z.poler_smart_interpreter import SmartInterpreter
    interp = SmartInterpreter(source, filename="example.py")
    violations = interp.find_violations()
    violations = interp.enrich_with_poler(violations)
"""
from __future__ import annotations

import ast
from typing import Any

from .poler_edit import PolerEdit
from .smart_rules import load_rules, AstAdapter, CompiledRule


# =============================================================================
# SmartInterpreter — the "smart" Python interpreter
# =============================================================================

class SmartInterpreter:
    """The 'smart' Python interpreter — uses AST + compiled rules + POLER.

    Rules are loaded via smart_rules.load_rules() on construction. If the
    interpreter or schema has changed since last run, rules re-compile.

    Usage:
        interp = SmartInterpreter(source, filename="example.py")
        violations = interp.find_violations()
        violations = interp.enrich_with_poler(violations)
        for v in violations:
            print(f"line {v['line']}: [{v['severity']}] {v['rule_id']}")
            print(f"  source: {v['source_snippet']}")
            print(f"  fix:    {v['fix']}")
    """

    def __init__(self, source: str, filename: str = "<code>",
                 force_recompile_rules: bool = False) -> None:
        self.source = source
        self.filename = filename

        # Adapter for current interpreter (auto-detects node types)
        self.adapter = AstAdapter()

        # Load compiled rules (cache invalidates on version/schema change)
        self.rules, self.rule_warnings, self.rule_meta = load_rules(
            force_recompile=force_recompile_rules
        )

        # Index rules by id for fix lookup
        self._rules_by_id: dict[str, CompiledRule] = {r.id: r for r in self.rules}

        # Custom rules (deep_nesting etc.) — handled with special traversal
        self._custom_rules: list[CompiledRule] = [r for r in self.rules if r.is_custom]

        # Parse source — raises SyntaxError if invalid (caller should handle)
        self.tree = self.adapter.parse(source, filename=filename)
        self.lines = source.splitlines()

    # ------------------------------------------------------------------ #
    # Finding violations
    # ------------------------------------------------------------------ #

    def find_violations(self) -> list[dict]:
        """Walk AST and find all rule violations."""
        violations = []

        # Standard rules — visit each node, check each rule
        for node in self.adapter.walk(self.tree):
            for rule in self.rules:
                if rule.is_custom:
                    continue  # handled below
                captures = rule.check(node, self.adapter)
                if captures is not None:
                    violations.append(self._make_violation(node, rule, captures))

        # Custom rules — special traversal
        for rule in self._custom_rules:
            violations.extend(self._run_custom(rule))

        return violations

    def _run_custom(self, rule: CompiledRule) -> list[dict]:
        """Dispatch a custom rule by name."""
        name = rule.node_type  # placeholder — actual name lives in schema
        # We stored custom name on the rule via the check closure
        # For deep_nesting specifically:
        if rule.id == "R009":  # deep_nesting
            return self._check_deep_nesting(self.tree, depth=0, max_depth=5, rule=rule)
        return []

    def _check_deep_nesting(self, node: ast.AST, depth: int, max_depth: int,
                            rule: CompiledRule) -> list[dict]:
        """Recursively check for nested if/for/while blocks deeper than max_depth."""
        out = []
        new_depth = depth
        if isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            new_depth = depth + 1
            if new_depth > max_depth:
                out.append(self._make_violation(node, rule, {"depth": new_depth}))
        for child in self.adapter.iter_children(node):
            out.extend(self._check_deep_nesting(child, new_depth, max_depth, rule))
        return out

    # ------------------------------------------------------------------ #
    # Violation record construction
    # ------------------------------------------------------------------ #

    def _make_violation(self, node: ast.AST, rule: CompiledRule, captures: dict) -> dict:
        """Create a violation record with line/col + source snippet + rendered fix."""
        lineno = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0)
        source_line = self.lines[lineno - 1] if 0 < lineno <= len(self.lines) else ""

        # Render fix template with captures (unparse complex captures)
        fix = rule.fix_template
        for k, v in captures.items():
            if v is None:
                rendered = "?"
            elif isinstance(v, str):
                rendered = v
            else:
                # AST node — unparse back to source
                rendered = self.adapter.unparse(v) if isinstance(v, ast.AST) else str(v)
            fix = fix.replace("{{" + k + "}}", rendered)

        return {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "severity": rule.severity,
            "category": rule.category,
            "why": rule.why,
            "fix": fix,
            "manual_review": rule.manual_review,
            "line": lineno,
            "col": col,
            "source_snippet": source_line.strip(),
            "ast_node_type": type(node).__name__,
            "context": captures,
        }

    # ------------------------------------------------------------------ #
    # POLER enrichment
    # ------------------------------------------------------------------ #

    def enrich_with_poler(self, violations: list[dict]) -> list[dict]:
        """For each violation, run POLER with the rule's keyword to get positions/resonance.

        POLER is cached per query — so if 48 eval violations exist, POLER('eval') is
        called only once.
        """
        poler_cache: dict[str, dict] = {}
        for v in violations:
            rule = self._rules_by_id.get(v["rule_id"])
            if not rule or not rule.poler_query:
                v["poler"] = None
                continue
            q = rule.poler_query
            if q not in poler_cache:
                try:
                    pe = PolerEdit(text=self.source, query=q, source=self.filename)
                    res = pe.analyze()
                    poler_cache[q] = res.get("poler_v3", {})
                except Exception as exc:  # noqa: BLE001
                    poler_cache[q] = {"error": str(exc), "veins": [], "navigation_map": {}}
            poler = poler_cache[q]
            nav = poler.get("navigation_map", {})
            info = nav.get(q) or nav.get(q.lower()) or {}
            best_vein = None
            for vein in poler.get("veins", []):
                if (vein.get("keyword", "")).lower() == q.lower():
                    if not best_vein or vein.get("resonance_integral", 0) > best_vein.get("resonance_integral", 0):
                        best_vein = vein
            v["poler"] = {
                "query": q,
                "total_count": info.get("count", 0),
                "peak_epsilon": info.get("peak_epsilon"),
                "total_resonance": info.get("total_resonance"),
                "domain": info.get("domain", "general"),
                "best_vein_fragment": (best_vein or {}).get("top_fragment", "")[:200] if best_vein else "",
            }
        return violations

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #

    def summary(self, violations: list[dict]) -> dict:
        """Return a summary dict of violations by severity and rule."""
        by_severity = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        by_rule: dict[str, int] = {}
        for v in violations:
            sev = v["severity"]
            if sev in by_severity:
                by_severity[sev] += 1
            by_rule[v["rule_id"]] = by_rule.get(v["rule_id"], 0) + 1
        return {
            "total": len(violations),
            "by_severity": by_severity,
            "by_rule": by_rule,
        }


# =============================================================================
# Convenience entry point
# =============================================================================

def analyze_code(source: str, filename: str = "<code>",
                 force_recompile_rules: bool = False) -> dict:
    """Analyze Python source and return a complete report.

    Returns:
        {
            "file": filename,
            "syntax_ok": True/False (if False, no violations, just error),
            "syntax_error": {...} or None,
            "summary": {total, by_severity, by_rule},
            "violations": [{rule_id, severity, why, fix, line, source_snippet, poler, ...}],
            "rules_meta": {cache_hit, interpreter, interpreter_version, ...},
        }
    """
    # Step 1: parse syntax
    try:
        interp = SmartInterpreter(source, filename=filename,
                                  force_recompile_rules=force_recompile_rules)
    except SyntaxError as e:
        return {
            "file": filename,
            "syntax_ok": False,
            "syntax_error": {
                "line": e.lineno,
                "col": e.offset,
                "message": e.msg,
                "text": (e.text or "").rstrip(),
            },
            "summary": {"total": 0, "by_severity": {}, "by_rule": {}},
            "violations": [],
            "rules_meta": None,
        }

    # Step 2: find violations
    violations = interp.find_violations()

    # Step 3: enrich with POLER
    violations = interp.enrich_with_poler(violations)

    # Step 4: summary
    summary = interp.summary(violations)

    return {
        "file": filename,
        "syntax_ok": True,
        "syntax_error": None,
        "summary": summary,
        "violations": violations,
        "rules_meta": interp.rule_meta,
        "rules_warnings": interp.rule_warnings,
    }
