"""
generator.py — Compiles schema rule intents into executable rule checks.

Pipeline:
    schema.yaml (intents)
        |
        v
    AstAdapter (introspects current interpreter's ast module)
        |
        v
    generator.compile(schema, adapter) -> list[CompiledRule]
        |
        v
    SmartInterpreter uses CompiledRule.check(node) -> Optional[dict]

Each CompiledRule:
  - id, severity, why, fix_template, manual_review, poler_query
  - check(node) -> Optional[dict]  # dict = captures if matched, None if not
  - custom: Optional[Callable[[node, AstAdapter], Optional[dict]]]  # for non-AST rules

The generator is the ONLY place that translates intent -> executable. When
the interpreter changes, the adapter is rebuilt, and generator.compile() is
re-invoked — producing fresh CompiledRules for the new environment.
"""
from __future__ import annotations

import ast
import yaml
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .ast_adapter import AstAdapter, _MISSING


# =============================================================================
# CompiledRule — executable form of a schema rule
# =============================================================================

@dataclass
class CompiledRule:
    id: str
    name: str
    severity: str
    category: str
    why: str
    fix_template: str
    manual_review: str
    poler_query: Optional[str]
    node_type: str             # AST class name or "Custom"
    check: Callable[[Any, AstAdapter], Optional[dict]]  # returns captures or None
    is_custom: bool = False    # custom rules don't go through ast.walk
    interpreter: str = "python"  # which adapter this rule was compiled for


# =============================================================================
# Custom checks — non-trivial predicates that don't fit the path DSL
# These are registered by name and referenced from schema.yaml via `custom:`
# =============================================================================

CUSTOM_CHECKS: dict[str, Callable[[Any, AstAdapter, dict], Optional[dict]]] = {}


def register_custom(name: str):
    """Decorator: @register_custom("name") makes a check available to schema."""
    def deco(fn):
        CUSTOM_CHECKS[name] = fn
        return fn
    return deco


@register_custom("sql_string_concat_strings")
def _sql_string_concat_strings(node: ast.BinOp, adapter: AstAdapter, _params: dict) -> Optional[dict]:
    """Match BinOp(Add) where left or right is a string containing SQL keywords."""
    SQL_KW = ("INSERT ", "SELECT ", "UPDATE ", "DELETE ", "DROP ", "VALUES ")
    left_str = _extract_string(node.left)
    right_str = _extract_string(node.right)
    if left_str is None and right_str is None:
        return None
    combined = (left_str or "") + (right_str or "")
    if any(kw in combined.upper() for kw in SQL_KW):
        return {"snippet": combined[:80]}
    return None


@register_custom("not_env_var_reference")
def _not_env_var_reference(node: ast.Assign, adapter: AstAdapter, _params: dict) -> Optional[dict]:
    """Match Assign where value is a non-empty string that doesn't reference env vars."""
    value = node.value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return None
    s = value.value
    if not s:
        return None
    if s.startswith("$") or s.startswith("os.environ"):
        return None
    return {}  # match — captures filled from main path


@register_custom("deep_nesting")
def _deep_nesting(_node: Any, adapter: AstAdapter, params: dict) -> Optional[dict]:
    """Custom — handled specially by SmartInterpreter, not via node-by-node check.

    Returns None here because deep nesting requires recursive walk with depth
    tracking, which can't be expressed per-node. SmartInterpreter detects
    node_type=='Custom' + custom.name=='deep_nesting' and dispatches to its own
    _check_deep_nesting method.
    """
    return None


@register_custom("js_function_named_eval")
def _js_function_named_eval(node: Any, adapter: AstAdapter, _params: dict) -> Optional[dict]:
    """JS-only: match call_expression where function is `eval` (bare or member).

    Tree-sitter JS doesn't have a single field for 'function name' — we need
    to handle both `eval(x)` (identifier) and `window.eval(x)` (member_expression
    with property=eval). This custom check does that.
    """
    # Get the function field of the call_expression
    func = adapter.navigate(node, "function")
    if func is None:
        return None

    # Case 1: bare identifier — eval(x)
    if adapter.get_node_type(func) == "identifier":
        if adapter.navigate(func, "text") == "eval":
            return {}
        return None

    # Case 2: member expression — obj.eval(x)
    if adapter.get_node_type(func) == "member_expression":
        prop = adapter.navigate(func, "property")
        if prop is not None and adapter.navigate(prop, "text") == "eval":
            return {}
        return None

    return None


def _extract_string(node: Any) -> Optional[str]:
    """Extract string literal from a node (Python ast.Constant or JS string)."""
    # Python ast.Constant
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    # JS string node (tree-sitter) — check via attribute
    if hasattr(node, "type") and node.type == "string":
        try:
            text = node.text.decode("utf-8", errors="replace")
            # Strip surrounding quotes
            if len(text) >= 2 and text[0] in ('"', "'", "`") and text[-1] == text[0]:
                return text[1:-1]
            return text
        except Exception:
            return None
    return None


# =============================================================================
# Pattern compiler — translates schema pattern dict to a check function
# =============================================================================

def _compile_condition(cond: dict) -> Callable[[Any, AstAdapter], bool]:
    """Compile a single condition dict to a predicate."""
    if "path" not in cond:
        raise ValueError(f"Condition missing 'path': {cond}")

    path = cond["path"]

    def _unwrap(val):
        """Auto-unwrap ast.Constant to its Python value for scalar comparison.

        This lets schema authors write `equals: true` instead of
        `equals: ast.Constant(value=True)`. Also unwraps for `in:` lists.
        """
        if isinstance(val, ast.Constant):
            return val.value
        return val

    if "equals" in cond:
        target = cond["equals"]
        # null in YAML -> None
        if target == "null" or target is None:
            target = None

        def check(node, adapter):
            val = _unwrap(adapter.navigate(node, path))
            return val == target
        return check

    if "in" in cond:
        targets = list(cond["in"])

        def check(node, adapter):
            val = _unwrap(adapter.navigate(node, path))
            return val in targets
        return check

    if "type_is" in cond:
        type_name = cond["type_is"]

        def check(node, adapter):
            val = adapter.navigate(node, path)
            if val is None or val is _MISSING:
                return False
            # If val is already a string (from _class/_pytype navigation),
            # compare directly. Otherwise, take its Python class name.
            if isinstance(val, str):
                return val == type_name
            return type(val).__name__ == type_name
        return check

    raise ValueError(f"Condition has no operator (equals/in/type_is): {cond}")


def _compile_match_block(match: dict | list | None) -> Callable[[Any, AstAdapter], bool]:
    """Compile a match block (with 'all'/'any' keys) or list (implicit 'all')."""
    # No match block — always true (matches any node of the right type)
    if not match:
        return lambda node, adapter: True

    # List form: implicit 'all'
    if isinstance(match, list):
        checks = [_compile_condition(c) for c in match]

        def combined(node, adapter):
            return all(c(node, adapter) for c in checks)
        return combined

    # Dict form: explicit 'all' / 'any'
    if isinstance(match, dict):
        if "all" in match:
            sub = _compile_match_block(match["all"])

            def all_check(node, adapter):
                return sub(node, adapter)
            return all_check

        if "any" in match:
            checks = [_compile_condition(c) for c in match["any"]]

            def any_check(node, adapter):
                return any(c(node, adapter) for c in checks)
            return any_check

        # Single-condition dict — treat as 'all' of one
        if "path" in match:
            single = _compile_condition(match)

            def single_check(node, adapter):
                return single(node, adapter)
            return single_check

    raise ValueError(f"Cannot compile match block: {match}")


def _compile_captures(captures: dict) -> Callable[[Any, AstAdapter], dict]:
    """Compile captures dict to a function that returns {name: value}."""
    if not captures:
        return lambda node, adapter: {}

    items = list(captures.items())

    def extract(node, adapter):
        out = {}
        for name, path in items:
            val = adapter.navigate(node, path)
            if val is _MISSING:
                val = None
            out[name] = val
        return out
    return extract


def _compile_pattern(pattern: dict) -> tuple[Callable[[Any, AstAdapter], Optional[dict]], bool]:
    """Compile a pattern dict. Returns (check_fn, is_custom).

    For AST patterns:
        check_fn(node, adapter) -> Optional[dict]  (dict = captures if match)
    For custom patterns (node_type=='Custom'):
        check_fn is a placeholder; SmartInterpreter dispatches via custom name.
    For AST patterns WITH a `custom` predicate (e.g. R010):
        Both the AST match AND the custom predicate must pass.
    """
    if pattern.get("node_type") == "Custom":
        custom_name = pattern.get("custom", {}).get("name", "")
        custom_params = pattern.get("custom", {}).get("params", {})

        def custom_check(node, adapter):
            fn = CUSTOM_CHECKS.get(custom_name)
            if fn is None:
                return None
            return fn(node, adapter, custom_params)
        return custom_check, True

    node_type = pattern["node_type"]
    match = pattern.get("match", {})
    match_fn = _compile_match_block(match)
    captures_fn = _compile_captures(pattern.get("captures", {}))

    # Optional custom predicate (runs after AST match passes)
    custom_def = pattern.get("custom")
    custom_fn = None
    if custom_def:
        custom_name = custom_def.get("name", "")
        custom_params = custom_def.get("params", {})
        custom_fn = CUSTOM_CHECKS.get(custom_name)

    def ast_check(node, adapter):
        # Fast reject: type must match (use adapter method for cross-adapter support)
        if adapter.get_node_type(node) != node_type:
            return None
        if not match_fn(node, adapter):
            return None
        # If there's a custom predicate, it must also pass
        # Custom predicate may return its own captures (merged into main captures)
        if custom_fn is not None:
            custom_caps = custom_fn(node, adapter, custom_params)
            if custom_caps is None:
                return None
            # Merge: custom captures win on conflict
            caps = captures_fn(node, adapter)
            caps.update(custom_caps)
            return caps
        return captures_fn(node, adapter)

    return ast_check, False


# =============================================================================
# Top-level compile
# =============================================================================

def load_schema(path: str) -> dict:
    """Load schema YAML from a path."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def compile_rules(schema: dict, adapter: AstAdapter) -> tuple[list[CompiledRule], list[str]]:
    """Compile all rules in schema against the given adapter.

    Filters rules by `interpreter_targets` (Stage 2). Rules without explicit
    `interpreter_targets` apply to all interpreters in schema's
    `default_interpreter_targets`.

    For multi-interpreter rules, `patterns_by_interpreter` provides per-adapter
    patterns. If absent, top-level `pattern` is used.

    Rules referencing unknown node types are skipped with a warning.
    """
    rules: list[CompiledRule] = []
    warnings = list(adapter.validate_schema(schema))

    default_targets = schema.get("default_interpreter_targets", [adapter.NAME])
    adapter_name = adapter.NAME

    for rule_def in schema.get("rules", []):
        # Stage 2: filter by interpreter_targets
        targets = rule_def.get("interpreter_targets", default_targets)
        if adapter_name not in targets:
            continue  # rule not for this interpreter

        # Stage 2: pick pattern — per-interpreter or top-level
        patterns_by_interp = rule_def.get("patterns_by_interpreter")
        if patterns_by_interp and adapter_name in patterns_by_interp:
            pattern = patterns_by_interp[adapter_name]
        else:
            pattern = rule_def.get("pattern")
        if not pattern:
            warnings.append(f"{rule_def['id']}: no pattern for {adapter_name} — skipped")
            continue

        node_type = pattern.get("node_type", "")

        # Skip rules whose node type isn't available in this interpreter
        if node_type != "Custom" and not adapter.has_node_type(node_type):
            continue

        check_fn, is_custom = _compile_pattern(pattern)

        rules.append(CompiledRule(
            id=rule_def["id"],
            name=rule_def.get("name", rule_def["id"]),
            severity=rule_def["severity"],
            category=rule_def.get("category", "general"),
            why=rule_def["why"],
            fix_template=rule_def["fix_template"],
            manual_review=rule_def["manual_review"],
            poler_query=rule_def.get("poler_query"),
            node_type=node_type,
            check=check_fn,
            is_custom=is_custom,
            interpreter=adapter_name,
        ))

    return rules, warnings
