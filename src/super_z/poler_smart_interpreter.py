"""
poler_smart_interpreter.py — Smart Python interpreter.

Architecture (user's vision):
    [Python ast.walk] → finds dangerous patterns deterministically
           ↓ positions
    [POLER v3.0] → provides semantic context (fragment, resonance)
           ↓
    [Rule-based fixer] → deterministic patches (no LLM!)
           ↓
    Report → AI/human architect accepts/rejects

This module proves: Python interpreter CAN suggest fixes by itself,
using POLER as a tool — no LLM needed for known patterns.

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


# ============================================================================
# DETERMINISTIC RULES — pattern → fix (no LLM, no hardcoded themes)
# ============================================================================

RULES: list[dict] = [
    {
        "id": "R001_eval",
        "severity": "CRITICAL",
        "why": "eval() executes arbitrary code. If input comes from user, it's RCE.",
        "fix_template": "ast.literal_eval({{arg}})  # safe — only literals",
        "manual_review": "If arg is dynamic user input, REWRITE the logic without eval.",
        "poler_query": "eval",
    },
    {
        "id": "R002_exec",
        "severity": "CRITICAL",
        "why": "exec() runs arbitrary code at runtime. Almost never needed.",
        "fix_template": "# REMOVE exec — inline the statement instead",
        "manual_review": "If you need dynamic code, refactor to functions/data.",
        "poler_query": "exec",
    },
    {
        "id": "R003_subprocess_shell_true",
        "severity": "CRITICAL",
        "why": "shell=True + string concatenation = command injection.",
        "fix_template": "subprocess.run({{args_list}}, shell=False)  # pass args as list",
        "manual_review": "Pass args as a list, never concatenate strings.",
        "poler_query": "subprocess",
    },
    {
        "id": "R004_pickle_loads",
        "severity": "HIGH",
        "why": "pickle.loads on untrusted data = arbitrary code execution.",
        "fix_template": "json.loads({{arg}}.decode('utf-8') if isinstance({{arg}}, bytes) else {{arg}})  # safer",
        "manual_review": "If you really need pickle, sign the data with HMAC first.",
        "poler_query": "pickle",
    },
    {
        "id": "R005_pickle_load",
        "severity": "HIGH",
        "why": "pickle.load on untrusted file = arbitrary code execution.",
        "fix_template": "json.load({{arg}})  # safer",
        "manual_review": "Same as R004.",
        "poler_query": "pickle",
    },
    {
        "id": "R006_bare_except",
        "severity": "MEDIUM",
        "why": "Bare except catches KeyboardInterrupt, SystemExit — debugging hell.",
        "fix_template": "except Exception:  # or more specific",
        "manual_review": "Catch the specific exception you expect.",
        "poler_query": "except",
    },
    {
        "id": "R007_pass_in_except",
        "severity": "MEDIUM",
        "why": "Silent failure — bugs hide forever.",
        "fix_template": "except Exception as e:\n    logger.exception('...')  # at least log",
        "manual_review": "Either handle or log, never silently pass.",
        "poler_query": "except",
    },
    {
        "id": "R008_global_mutation",
        "severity": "MEDIUM",
        "why": "global + mutation = invisible state, untestable code.",
        "fix_template": "# Pass state as argument, return new state",
        "manual_review": "Refactor to pure functions.",
        "poler_query": "global",
    },
    {
        "id": "R009_deep_nesting",
        "severity": "LOW",
        "why": "Deep nesting (>= 5 levels) = unreadable, untestable.",
        "fix_template": "# Extract inner block to a function, or use early return",
        "manual_review": "Use guard clauses: if not X: return",
        "poler_query": None,
    },
    {
        "id": "R010_sql_string_concat",
        "severity": "CRITICAL",
        "why": "String-concatenated SQL = injection by definition.",
        "fix_template": "cursor.execute('SQL WITH ? placeholders', (a, b, c))",
        "manual_review": "ALWAYS use parameterized queries.",
        "poler_query": "INSERT",
    },
    {
        "id": "R011_hardcoded_password",
        "severity": "HIGH",
        "why": "Hardcoded password in source — leaks via git/VCS.",
        "fix_template": "os.environ['PASSWORD']  # from env, never in code",
        "manual_review": "Move secrets to env vars or secret manager.",
        "poler_query": "password",
    },
]

_RULES_BY_ID = {r["id"]: r for r in RULES}


# ============================================================================
# Helpers
# ============================================================================

def _is_call_to(node: ast.AST, func_name: str) -> bool:
    """True if node is Call() to a bare name like `eval(...)`, `exec(...)`."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id == func_name
    if isinstance(f, ast.Attribute):
        return f.attr == func_name
    return False


def _has_kwarg(call: ast.Call, arg_name: str, value: Any = None) -> bool:
    """True if call has keyword arg `arg_name` (with optional value match)."""
    for kw in call.keywords:
        if kw.arg == arg_name:
            if value is None:
                return True
            if isinstance(kw.value, ast.Constant):
                return kw.value.value == value
    return False


def _extract_string(node: ast.AST) -> str | None:
    """Try to extract a string literal from a node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


# ============================================================================
# SmartInterpreter — the "smart" Python interpreter
# ============================================================================

class SmartInterpreter:
    """The 'smart' Python interpreter — uses AST + rules + POLER.

    Usage:
        interp = SmartInterpreter(source, filename="example.py")
        violations = interp.find_violations()
        violations = interp.enrich_with_poler(violations)
        for v in violations:
            print(f"line {v['line']}: [{v['severity']}] {v['rule_id']}")
            print(f"  source: {v['source_snippet']}")
            print(f"  fix:    {v['fix']}")
    """

    def __init__(self, source: str, filename: str = "<code>") -> None:
        self.source = source
        self.filename = filename
        # Parse once — raise if invalid syntax (caller should handle)
        self.tree = ast.parse(source, filename=filename)
        self.lines = source.splitlines()

    def find_violations(self) -> list[dict]:
        """Walk AST and find all rule violations."""
        violations = []
        for node in ast.walk(self.tree):
            violations.extend(self._check_node(node))
        # Also check deep nesting (needs custom walk with depth tracking)
        violations.extend(self._check_deep_nesting(self.tree, depth=0, max_depth=5))
        return violations

    def _check_deep_nesting(self, node: ast.AST, depth: int, max_depth: int) -> list[dict]:
        """Recursively check for nested if/for/while blocks deeper than max_depth."""
        out = []
        new_depth = depth
        if isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
            new_depth = depth + 1
            if new_depth > max_depth:
                out.append(self._make_violation(node, "R009_deep_nesting", {
                    "depth": new_depth,
                }))
        for child in ast.iter_child_nodes(node):
            out.extend(self._check_deep_nesting(child, new_depth, max_depth))
        return out

    def _check_node(self, node: ast.AST) -> list[dict]:
        """Check one AST node against all rules."""
        out = []

        # R001: eval()
        if _is_call_to(node, "eval"):
            out.append(self._make_violation(node, "R001_eval", {
                "arg": ast.unparse(node.args[0]) if node.args else "?",
            }))

        # R002: exec()
        if _is_call_to(node, "exec"):
            out.append(self._make_violation(node, "R002_exec", {
                "arg": ast.unparse(node.args[0]) if node.args else "?",
            }))

        # R003: subprocess.run(..., shell=True)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in ("run", "call", "Popen", "check_call", "check_output")
                and _has_kwarg(node, "shell", True)):
            out.append(self._make_violation(node, "R003_subprocess_shell_true", {
                "args_list": ast.unparse(node.args[0]) if node.args else "?",
            }))

        # R004/R005: pickle.loads()/pickle.load()
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pickle"
                and node.func.attr in ("loads", "load")):
            rule_id = "R004_pickle_loads" if node.func.attr == "loads" else "R005_pickle_load"
            out.append(self._make_violation(node, rule_id, {
                "arg": ast.unparse(node.args[0]) if node.args else "?",
            }))

        # R006/R007: bare except / pass-in-except
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                out.append(self._make_violation(node, "R006_bare_except", {}))
            if (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                out.append(self._make_violation(node, "R007_pass_in_except", {}))

        # R008: global mutation
        if isinstance(node, ast.Global) and isinstance(getattr(node, "names", None), list):
            out.append(self._make_violation(node, "R008_global_mutation", {
                "names": ", ".join(node.names),
            }))

        # R010: SQL string concat
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left_str = _extract_string(node.left)
            right_str = _extract_string(node.right)
            combined = (left_str or "") + (right_str or "")
            if any(kw in combined.upper() for kw in ("INSERT ", "SELECT ", "UPDATE ", "DELETE ", "DROP ", "VALUES ")):
                out.append(self._make_violation(node, "R010_sql_string_concat", {
                    "snippet": combined[:80],
                }))

        # R011: hardcoded password
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.lower() in ("password", "pwd", "passwd", "secret", "api_key", "token")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and node.value.value  # not empty
                and not node.value.value.startswith("$")  # not env-var ref
                and not node.value.value.startswith("os.environ")):
            out.append(self._make_violation(node, "R011_hardcoded_password", {
                "var": node.targets[0].id,
            }))

        return out

    def _make_violation(self, node: ast.AST, rule_id: str, ctx: dict) -> dict:
        """Create a violation record with line/col + source snippet."""
        rule = _RULES_BY_ID[rule_id]
        lineno = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0)
        source_line = self.lines[lineno - 1] if 0 < lineno <= len(self.lines) else ""
        # Format the fix template with context variables
        fix = rule["fix_template"]
        for k, v in ctx.items():
            fix = fix.replace("{{" + k + "}}", str(v))
        return {
            "rule_id": rule_id,
            "severity": rule["severity"],
            "why": rule["why"],
            "fix": fix,
            "manual_review": rule["manual_review"],
            "line": lineno,
            "col": col,
            "source_snippet": source_line.strip(),
            "ast_node_type": type(node).__name__,
            "context": ctx,
        }

    def enrich_with_poler(self, violations: list[dict]) -> list[dict]:
        """For each violation, run POLER with the rule's keyword to get positions/resonance.

        POLER is cached per query — so if 48 eval violations exist, POLER('eval') is
        called only once.
        """
        poler_cache: dict[str, dict] = {}
        for v in violations:
            rule = _RULES_BY_ID.get(v["rule_id"])
            if not rule or not rule.get("poler_query"):
                v["poler"] = None
                continue
            q = rule["poler_query"]
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


# ============================================================================
# Convenience entry point
# ============================================================================

def analyze_code(source: str, filename: str = "<code>") -> dict:
    """Analyze Python source and return a complete report.

    Returns:
        {
            "file": filename,
            "syntax_ok": True/False (if False, no violations, just error),
            "syntax_error": {...} or None,
            "summary": {total, by_severity, by_rule},
            "violations": [{rule_id, severity, why, fix, line, source_snippet, poler, ...}],
        }
    """
    # Step 1: parse syntax
    try:
        interp = SmartInterpreter(source, filename=filename)
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
    }
