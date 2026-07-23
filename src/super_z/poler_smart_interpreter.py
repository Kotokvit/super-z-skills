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
from .smart_rules import (
    load_rules, AstAdapter, CompiledRule,
    apply_transformation, apply_transformations,
)


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
                 force_recompile_rules: bool = False,
                 adapter: Any = None,
                 language: str | None = None) -> None:
        self.source = source
        self.filename = filename

        # Stage 2: adapter selection — explicit adapter, or by language name,
        # or default (Python) for backward compatibility
        if adapter is not None:
            self.adapter = adapter
        elif language is not None:
            from .smart_rules import get_adapter_for_language
            self.adapter = get_adapter_for_language(language)
        else:
            # Backward-compat default: Python
            self.adapter = AstAdapter()

        # Load compiled rules (cache invalidates on version/schema change)
        # Stage 2: pass adapter so rules are filtered by interpreter_targets
        self.rules, self.rule_warnings, self.rule_meta = load_rules(
            adapter=self.adapter,
            force_recompile=force_recompile_rules
        )

        # Index rules by id for fix lookup
        self._rules_by_id: dict[str, CompiledRule] = {r.id: r for r in self.rules}

        # Custom rules (deep_nesting etc.) — handled with special traversal
        self._custom_rules: list[CompiledRule] = [r for r in self.rules if r.is_custom]

        # Parse source — raises SyntaxError if invalid (caller should handle)
        # Tree-sitter is more lenient and may not raise, but that's fine
        try:
            self.tree = self.adapter.parse(source, filename=filename)
        except SyntaxError:
            raise
        except Exception as e:
            # Wrap non-SyntaxError parser failures
            raise SyntaxError(str(e)) from e
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

    def _check_deep_nesting(self, node: Any, depth: int, max_depth: int,
                            rule: CompiledRule) -> list[dict]:
        """Recursively check for nested if/for/while blocks deeper than max_depth.

        Stage 2: language-agnostic via adapter.get_node_type(). Each adapter
        exposes its own control-flow node types — we check against a per-
        interpreter set.
        """
        out = []
        new_depth = depth

        # Per-interpreter control-flow node types
        if self.adapter.NAME == "python":
            control_types = {"If", "For", "While", "With", "Try"}
        elif self.adapter.NAME == "javascript":
            control_types = {"if_statement", "for_statement", "for_in_statement",
                              "while_statement", "do_statement", "switch_statement",
                              "try_statement"}
        else:
            control_types = set()  # unknown adapter — skip deep nesting

        node_type = self.adapter.get_node_type(node)
        if node_type in control_types:
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
        """Create a violation record with line/col + source snippet + rendered fix.

        Uses adapter Protocol methods (get_position, get_node_type,
        get_source_text) — never inspects the node directly. This keeps
        SmartInterpreter language-agnostic: it doesn't know whether
        `node` is a Python ast.AST or a tree-sitter Node.
        """
        # Position via adapter Protocol
        pos = self.adapter.get_position(node)
        # Source snippet via adapter Protocol
        source_line = self.adapter.get_source_text(node, self.lines)
        # Node type via adapter Protocol
        node_type = self.adapter.get_node_type(node)

        # Render fix template with captures (unparse complex captures)
        fix = rule.fix_template
        for k, v in captures.items():
            if v is None:
                rendered = "?"
            elif isinstance(v, str):
                rendered = v
            else:
                # AST node — unparse back to source
                rendered = self.adapter.unparse(v) if hasattr(v, "lineno") or hasattr(v, "start_point") else str(v)
            fix = fix.replace("{{" + k + "}}", rendered)

        # Stage 4: build Transformation object if rule has transform_factory
        transformation = None
        if rule.transform_factory is not None:
            try:
                transformation = rule.transform_factory(node, captures)
            except Exception:
                transformation = None

        return {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "severity": rule.severity,
            "category": rule.category,
            "why": rule.why,
            "fix": fix,
            "manual_review": rule.manual_review,
            "line": pos.line,
            "col": pos.col,
            "end_line": pos.end_line,
            "end_col": pos.end_col,
            "source_snippet": source_line,
            "ast_node_type": node_type,
            "context": captures,
            # Stage 4: target node reference + compiled Transformation
            # _node is intentionally underscore-prefixed — callers should not
            # introspect; use apply_fix(violation) instead.
            "_node": node,
            "transformation": transformation,
        }

    # ------------------------------------------------------------------ #
    # Stage 4: Apply IR transformations
    # ------------------------------------------------------------------ #

    def apply_fix(self, violation: dict) -> dict:
        """Apply a single violation's transformation to the parsed tree.

        Returns a result dict with:
          - success: bool
          - message: str (human-readable status)
          - action: str (replace/wrap/remove/insert_*)
          - source_before: str
          - source_after: str (for Python, re-emit tree; for JS, get_current_source)

        If the violation has no transformation (rule has no transform_factory),
        returns success=False, message='no transformation'.
        """
        transform = violation.get("transformation")
        if transform is None:
            return {
                "success": False,
                "message": "no transformation",
                "action": None,
                "source_before": self.source,
                "source_after": self.source,
            }

        source_before = self.source
        captures = violation.get("context", {})
        try:
            ok, msg = apply_transformation(
                self.tree, transform, self.adapter, captures
            )
        except Exception as e:
            return {
                "success": False,
                "message": f"exception: {e}",
                "action": transform.action,
                "source_before": source_before,
                "source_after": source_before,
            }

        # Get resulting source
        if self.adapter.NAME == "javascript":
            # tree-sitter: source was spliced, retrieve from adapter
            source_after = self.adapter.get_current_source()
            # Update tree to the new one
            new_tree = self.adapter.get_current_tree()
            if new_tree is not None:
                self.tree = new_tree
        else:
            # Python ast: tree was mutated in place, re-emit
            source_after = self.adapter.emit(self.tree)

        # Update self.source so subsequent fixes apply to the modified source
        self.source = source_after
        self.lines = source_after.splitlines()

        return {
            "success": ok,
            "message": msg,
            "action": transform.action,
            "source_before": source_before,
            "source_after": source_after,
        }

    def apply_all_fixes(self, violations: list[dict]) -> dict:
        """Apply all transformations from a list of violations.

        Transformations are applied in reverse order (last violation first)
        to keep earlier line/byte offsets valid as the source mutates.

        Returns a summary dict:
          - total: int
          - applied: int (successful)
          - failed: int
          - results: list of per-violation result dicts
          - final_source: str
        """
        # Filter to only violations that have a transformation
        fixable = [v for v in violations if v.get("transformation") is not None]
        # Reverse: last violation first (preserves offsets for earlier ones)
        fixable.reverse()

        results = []
        applied = 0
        failed = 0

        for v in fixable:
            r = self.apply_fix(v)
            results.append({
                "rule_id": v["rule_id"],
                "line": v["line"],
                **r,
            })
            if r["success"]:
                applied += 1
            else:
                failed += 1

        return {
            "total": len(fixable),
            "applied": applied,
            "failed": failed,
            "results": results,
            "final_source": self.source,
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
                 force_recompile_rules: bool = False,
                 language: str | None = None) -> dict:
    """Analyze source code and return a complete report.

    Stage 2: language parameter selects adapter. If None, defaults to Python
    (backward-compat). Supported: "python", "javascript".

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
                                  force_recompile_rules=force_recompile_rules,
                                  language=language)
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
