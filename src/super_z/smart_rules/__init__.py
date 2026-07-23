"""
smart_rules — declarative rule system for SmartInterpreter.

Architecture (Stage 2 — JS adapter added):
    adapter_protocol.py  (InterpreterAdapter Protocol — the ABI)
        ^
        |  implements
        |
    ast_adapter.py       (Python adapter — reference implementation)
    js_adapter.py        (JavaScript adapter — tree-sitter)
        |
        v
    generator.py         (compiles schema intents -> CompiledRules, filtered
                          by interpreter_targets + patterns_by_interpreter)
        |
        v
    cache.py             (in-memory memoization by interpreter+version+schema)
        |
        v
    schema.yaml          (rule intents — per-rule interpreter_targets,
                          per-interpreter patterns)

Public API:
    from super_z.smart_rules import (
        load_rules, get_adapter_for_language,
        InterpreterAdapter, AstAdapter, TreeSitterJsAdapter,
    )
    rules, warnings, meta = load_rules()  # uses default adapter (Python)
    js_adapter = TreeSitterJsAdapter()
    rules, _, _ = load_rules(adapter=js_adapter)  # explicit adapter
"""
from __future__ import annotations

import os
from pathlib import Path

from .adapter_protocol import InterpreterAdapter, Position, NodeInfo
from .ast_adapter import AstAdapter
from .generator import CompiledRule, compile_rules, load_schema, CUSTOM_CHECKS, register_custom
from .cache import load_or_compile, invalidate_cache, list_cache
from .adapter_protocol import NodeSpec, Transformation, TransformAction
from .transforms import (
    apply_transformation, apply_transformations,
    build_from_spec, resolve_placeholder, find_parent,
)

# Conditional import — tree-sitter may not be installed in all environments
try:
    from .js_adapter import TreeSitterJsAdapter
    _HAS_JS_ADAPTER = True
except ImportError:
    TreeSitterJsAdapter = None  # type: ignore
    _HAS_JS_ADAPTER = False


# Default schema path: alongside this package
_SCHEMA_PATH = Path(__file__).parent / "schema.yaml"


def get_adapter_for_language(language: str) -> InterpreterAdapter:
    """Factory: return adapter instance for the given language name.

    Args:
        language: "python" or "javascript" (case-insensitive)

    Returns:
        Adapter instance implementing InterpreterAdapter Protocol

    Raises:
        ValueError if language is not supported
    """
    language = language.lower().strip()
    if language in ("python", "py"):
        return AstAdapter()
    if language in ("javascript", "js"):
        if not _HAS_JS_ADAPTER:
            raise ValueError(
                "JavaScript adapter requires tree-sitter packages: "
                "pip install tree_sitter tree_sitter_javascript"
            )
        return TreeSitterJsAdapter()  # type: ignore
    raise ValueError(f"Unsupported language: {language!r}. Supported: python, javascript")


def load_rules(schema_path: str | None = None,
               adapter: InterpreterAdapter | None = None,
               force_recompile: bool = False) -> tuple[list[CompiledRule], list[str], dict]:
    """Load compiled rules for the given (or default) interpreter.

    Args:
        schema_path: override default schema.yaml location
        adapter: explicit adapter instance (default: AstAdapter for Python)
        force_recompile: bypass cache, regenerate rules

    Returns:
        (rules, warnings, meta)
    """
    path = str(schema_path) if schema_path else str(_SCHEMA_PATH)
    return load_or_compile(path, adapter=adapter, force_recompile=force_recompile)


__all__ = [
    # Protocol + dataclasses
    "InterpreterAdapter",
    "Position",
    "NodeInfo",
    # Concrete adapters
    "AstAdapter",
    "TreeSitterJsAdapter",
    # Factory
    "get_adapter_for_language",
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
    # Stage 4: IR transforms
    "NodeSpec",
    "Transformation",
    "TransformAction",
    "apply_transformation",
    "apply_transformations",
    "build_from_spec",
    "resolve_placeholder",
    "find_parent",
]
