"""
smart_rules — declarative rule system for SmartInterpreter.

Rule intents live in schema.yaml (language-agnostic).
AstAdapter introspects current interpreter's ast module.
Generator compiles intents -> executable CompiledRules.
Cache invalidates on interpreter/version/schema change.

Public API:
    from super_z.smart_rules import load_rules
    rules, warnings, meta = load_rules()
    # rules: list[CompiledRule]
    # rules[i].check(node, adapter) -> Optional[dict]  (dict = captures)
"""
from __future__ import annotations

import os
from pathlib import Path

from .ast_adapter import AstAdapter
from .generator import CompiledRule, compile_rules, load_schema, CUSTOM_CHECKS, register_custom
from .cache import load_or_compile, invalidate_cache, list_cache


# Default schema path: alongside this package
_SCHEMA_PATH = Path(__file__).parent / "schema.yaml"


def load_rules(schema_path: str | None = None,
               force_recompile: bool = False) -> tuple[list[CompiledRule], list[str], dict]:
    """Load compiled rules for the current interpreter.

    Args:
        schema_path: override default schema.yaml location
        force_recompile: bypass cache, regenerate rules

    Returns:
        (rules, warnings, meta)
    """
    path = str(schema_path) if schema_path else str(_SCHEMA_PATH)
    return load_or_compile(path, force_recompile=force_recompile)


__all__ = [
    "AstAdapter",
    "CompiledRule",
    "compile_rules",
    "load_rules",
    "load_schema",
    "invalidate_cache",
    "list_cache",
    "register_custom",
    "CUSTOM_CHECKS",
]
