"""
smart_rules — declarative rule system for SmartInterpreter.

Architecture (Stage 1 — formalized adapter contract):
    adapter_protocol.py  (InterpreterAdapter Protocol — the ABI)
        ^
        |  implements
        |
    ast_adapter.py       (Python adapter — reference implementation)
        |
        v
    generator.py         (compiles schema intents -> CompiledRules)
        |
        v
    cache.py             (in-memory memoization by interpreter+version+schema)
        |
        v
    schema.yaml          (rule intents — stable across versions)

Public API:
    from super_z.smart_rules import load_rules, InterpreterAdapter, AstAdapter
    rules, warnings, meta = load_rules()
    # rules: list[CompiledRule]
    # rules[i].check(node, adapter) -> Optional[dict]  (dict = captures)
"""
from __future__ import annotations

import os
from pathlib import Path

from .adapter_protocol import InterpreterAdapter, Position, NodeInfo
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
    # Protocol + dataclasses
    "InterpreterAdapter",
    "Position",
    "NodeInfo",
    # Concrete adapter
    "AstAdapter",
    # Generator
    "CompiledRule",
    "compile_rules",
    "load_rules",
    "load_schema",
    "register_custom",
    "CUSTOM_CHECKS",
    # Cache
    "invalidate_cache",
    "list_cache",
]
