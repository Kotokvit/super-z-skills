"""
cache.py — Rule cache keyed by interpreter + version.

Cache invalidation trigger:
    - Interpreter name changes (python -> javascript-via-tree-sitter)
    - Interpreter version changes (Python 3.11.7 -> 3.13.0)
    - Schema file mtime changes (manual rule edit)

Cache strategy: IN-MEMORY memoization.
    CompiledRule objects contain closures (check functions) which can't be
    pickled. Instead, we memoize at module level keyed by
    (interpreter_name, interpreter_version, schema_path, schema_mtime).

    If the key matches, the cached list of CompiledRules is returned directly.
    If anything changed, the rules are recompiled — automatically adapting
    to the new interpreter/version.

    A small on-disk fingerprint file is also written so that the first
    compilation timestamp and reason are observable, but it's not used
    for restoring compiled rules (which is impossible with closures).
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Optional

from .ast_adapter import AstAdapter
from .generator import CompiledRule, compile_rules, load_schema


# Module-level cache — survives across multiple SmartInterpreter instantiations
# within the same Python process. Cleared automatically when process restarts
# (which is also when interpreter version could realistically change).
_MEMORY_CACHE: dict[str, dict] = {}


def _cache_dir() -> Path:
    """Determine cache directory. Honors XDG_CACHE_HOME on Linux."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
    else:
        base = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    cache_dir = Path(base) / "smart_rules"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_key(adapter: AstAdapter, schema_path: str, schema_mtime: float) -> str:
    """Build a cache key from interpreter + version + schema fingerprint.

    Format: rules_<interpreter>_<major>_<minor>_<patch>_<schema_hash>
    Any of these changing produces a new key -> cache miss -> regenerate.
    """
    schema_hash_input = f"{schema_path}:{schema_mtime}"
    schema_hash = hashlib.md5(schema_hash_input.encode("utf-8")).hexdigest()[:8]
    return f"rules_{adapter.NAME}_{adapter.version[0]}_{adapter.version[1]}_{adapter.version[2]}_{schema_hash}"


def load_or_compile(schema_path: str,
                    adapter: Optional[AstAdapter] = None,
                    force_recompile: bool = False) -> tuple[list[CompiledRule], list[str], dict]:
    """Load compiled rules from in-memory cache, or compile fresh and cache them.

    Returns:
        (rules, warnings, meta)
        meta includes cache_hit, cache_key, compiled_at, schema_path,
        interpreter, interpreter_version, recompile_reason
    """
    if adapter is None:
        adapter = AstAdapter()

    schema_mtime = os.path.getmtime(schema_path)
    key = _cache_key(adapter, schema_path, schema_mtime)

    meta = {
        "cache_key": key,
        "interpreter": adapter.NAME,
        "interpreter_version": adapter.version_str,
        "schema_path": schema_path,
        "schema_mtime": schema_mtime,
    }

    # Cache hit?
    if not force_recompile and key in _MEMORY_CACHE:
        cached = _MEMORY_CACHE[key]
        meta["cache_hit"] = True
        meta["compiled_at"] = cached["compiled_at"]
        meta["recompile_reason"] = None
        return cached["rules"], cached["warnings"], meta

    # Cache miss — determine reason
    if force_recompile:
        meta["recompile_reason"] = "force_recompile"
    elif any(k.startswith(f"rules_{adapter.NAME}_") and k != key for k in _MEMORY_CACHE):
        # Different key with same interpreter name — version or schema changed
        meta["recompile_reason"] = "interpreter_version_or_schema_changed"
    else:
        meta["recompile_reason"] = "first_compile"

    # Compile fresh
    schema = load_schema(schema_path)
    rules, warnings = compile_rules(schema, adapter)

    compiled_at = time.time()
    _MEMORY_CACHE[key] = {
        "rules": rules,
        "warnings": warnings,
        "compiled_at": compiled_at,
        "interpreter": adapter.NAME,
        "interpreter_version": adapter.version_str,
    }

    # Also write a fingerprint file to disk (observable but not used for restore)
    fingerprint_path = _cache_dir() / f"{key}.fingerprint"
    try:
        with open(fingerprint_path, "w", encoding="utf-8") as f:
            f.write(f"interpreter: {adapter.NAME}\n")
            f.write(f"version: {adapter.version_str}\n")
            f.write(f"schema: {schema_path}\n")
            f.write(f"schema_mtime: {schema_mtime}\n")
            f.write(f"compiled_at: {compiled_at}\n")
            f.write(f"rule_count: {len(rules)}\n")
            if warnings:
                f.write(f"warnings: {len(warnings)}\n")
                for w in warnings:
                    f.write(f"  - {w}\n")
    except Exception:
        pass  # fingerprint write failure is non-fatal

    meta["cache_hit"] = False
    meta["compiled_at"] = compiled_at
    return rules, warnings, meta


def invalidate_cache() -> int:
    """Clear in-memory cache. Returns count of entries cleared."""
    count = len(_MEMORY_CACHE)
    _MEMORY_CACHE.clear()
    return count


def list_cache() -> list[dict]:
    """List in-memory cache entries."""
    return [
        {
            "cache_key": k,
            "interpreter": v["interpreter"],
            "interpreter_version": v["interpreter_version"],
            "compiled_at": v["compiled_at"],
            "rule_count": len(v["rules"]),
            "warnings": len(v["warnings"]),
        }
        for k, v in _MEMORY_CACHE.items()
    ]
