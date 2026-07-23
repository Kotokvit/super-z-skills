"""
transforms.py — IR Transform Engine.

Applies Transformation objects (from generator/compiled rules) to a parsed
tree via the adapter Protocol. NEVER touches source text directly — every
mutation goes through adapter.replace_node() + adapter.build().

The engine is language-agnostic: it works identically for Python (ast)
and JS (tree-sitter) because it only uses Protocol methods.

Pipeline:
    SmartInterpreter.find_violations()
        -> list of violation dicts (each may carry a Transformation)
    SmartInterpreter.apply_transformations(violations)
        -> for each violation with a Transformation:
              1. Resolve NodeSpec placeholders ($capture:NAME, $node:TARGET)
              2. Build new node via adapter.build(spec.node_type, spec.fields)
              3. Find parent of target_node (via _find_parent)
              4. Dispatch on action:
                   REPLACE      -> adapter.replace_node(parent, field, old, new)
                   INSERT_BEFORE/INSERT_AFTER -> handled at block level
                   REMOVE       -> adapter.replace_node(parent, field, old, None)
                   WRAP         -> build wrapper, embed target in wrap_field,
                                   replace target with wrapper
              5. Return (source_before, source_after) for diff display

Stage 4: this file is the heart of "fixes as AST->AST transforms, not text".
"""
from __future__ import annotations

from typing import Any, Optional

from .adapter_protocol import (
    InterpreterAdapter, NodeSpec, Transformation, TransformAction,
)


# =============================================================================
# Placeholder resolution — $capture:NAME and $node:TARGET
# =============================================================================

CAPTURE_PREFIX = "$capture:"
NODE_REF_PREFIX = "$node:"


def resolve_placeholder(value: Any, captures: dict, target_node: Any,
                        adapter: InterpreterAdapter | None = None) -> Any:
    """Resolve a NodeSpec field value that may be a placeholder.

    - "$capture:NAME"        -> captures[NAME] (an AST node or scalar)
    - "$node:TARGET"         -> target_node (the original node being transformed)
    - "$node:TARGET.a.b.c"   -> adapter.navigate(target_node, "a.b.c") if
                                adapter provided; else error
    - "$field:NAME"          -> shortcut for "$node:TARGET.NAME"
    - NodeSpec               -> recursively resolve its fields, then return as-is
                                (build happens at the top level in build_from_spec)
    - list/tuple             -> recursively resolve each element
    - other                  -> return unchanged (scalars, already-built nodes)

    The `adapter` arg is required for $node:TARGET.path navigation. It's
    optional for the simple $node:TARGET case (no nested path).
    """
    if isinstance(value, str):
        if value.startswith(CAPTURE_PREFIX):
            name = value[len(CAPTURE_PREFIX):]
            return captures.get(name)
        if value.startswith(NODE_REF_PREFIX):
            rest = value[len(NODE_REF_PREFIX):]
            if rest == "" or rest == "TARGET":
                return target_node
            # Strip optional "TARGET." prefix to get the navigation path
            if rest.startswith("TARGET."):
                rest = rest[len("TARGET."):]
            if adapter is None:
                raise ValueError(
                    f"resolve_placeholder: {value!r} requires an adapter "
                    "for nested path navigation"
                )
            return adapter.navigate(target_node, rest)
        if value.startswith("$field:"):
            field = value[len("$field:"):]
            if adapter is None:
                raise ValueError(
                    f"resolve_placeholder: {value!r} requires an adapter"
                )
            return adapter.navigate(target_node, field)
        return value
    if isinstance(value, NodeSpec):
        # Recursively resolve, but return as NodeSpec so build() can construct
        return NodeSpec(
            node_type=value.node_type,
            fields={
                k: resolve_placeholder(v, captures, target_node, adapter)
                for k, v in value.fields.items()
            }
        )
    if isinstance(value, list):
        return [resolve_placeholder(v, captures, target_node, adapter) for v in value]
    if isinstance(value, tuple):
        return tuple(resolve_placeholder(v, captures, target_node, adapter) for v in value)
    return value


def build_from_spec(spec: NodeSpec, adapter: InterpreterAdapter,
                    captures: dict, target_node: Any) -> Any:
    """Build a new node from a NodeSpec, resolving all placeholders.

    Walks the spec tree, resolves $capture/$node placeholders, then calls
    adapter.build() on the resolved spec. Nested NodeSpecs are passed to
    adapter.build() which itself recursively builds (via _resolve_build_value
    in AstAdapter, or _resolve_emit_value in TreeSitterJsAdapter).
    """
    resolved_fields = {}
    for k, v in spec.fields.items():
        resolved_fields[k] = resolve_placeholder(v, captures, target_node, adapter)
    return adapter.build(spec.node_type, resolved_fields)


# =============================================================================
# Parent finding — needed for adapter.replace_node()
# =============================================================================

def find_parent(tree: Any, target: Any, adapter: InterpreterAdapter) -> tuple[Optional[Any], Optional[str]]:
    """Walk tree to find the parent of `target` and the field name it lives under.

    Returns (parent, field_name) or (None, None) if not found.

    For list fields, field_name is the list's attribute name (e.g. "body",
    "args"). The caller can then call adapter.replace_node(parent, field_name,
    target, new) which scans the list for the matching item by identity.
    """
    if target is None:
        return None, None

    # Python ast case — use ast.iter_fields to enumerate (field_name, value) pairs
    try:
        import ast as _ast
        if isinstance(tree, _ast.AST):
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.AST):
                    continue
                for fname, fval in _ast.iter_fields(node):
                    if fval is target:
                        return node, fname
                    if isinstance(fval, list) and target in fval:
                        return node, fname
                    if isinstance(fval, tuple) and target in fval:
                        return node, fname
            return None, None
    except ImportError:
        pass

    # Tree-sitter case — walk via adapter, then for each node check its children
    # Tree-sitter nodes have children (named + anonymous) but no field iteration
    # by default. We compare by start_byte/end_byte for identity.
    target_start = getattr(target, "start_byte", None)
    target_end = getattr(target, "end_byte", None)
    if target_start is None:
        return None, None

    for node in adapter.walk(tree):
        # Tree-sitter: iterate named children + their field names
        if hasattr(node, "children"):
            for child in node.children:
                if (getattr(child, "start_byte", None) == target_start
                        and getattr(child, "end_byte", None) == target_end):
                    # field_name is unknown for tree-sitter (we use byte splice)
                    return node, None

    return None, None


# =============================================================================
# Action application
# =============================================================================

def apply_transformation(tree: Any, transform: Transformation,
                         adapter: InterpreterAdapter,
                         captures: dict) -> tuple[bool, str]:
    """Apply a single transformation to the tree.

    Args:
        tree: parsed tree (will be mutated for Python; for tree-sitter,
              the source is spliced and a new tree is cached on the adapter)
        transform: Transformation with action, target_node, new_node, wrap_field
        adapter: language adapter
        captures: dict of named values from the rule's captures block

    Returns:
        (success, message) — message is human-readable for logging
    """
    if transform.target_node is None:
        return False, "transform.target_node is None"

    action = transform.action

    if action == TransformAction.REMOVE:
        return _apply_remove(tree, transform, adapter)

    if transform.new_node is None:
        return False, f"action={action} requires new_node, got None"

    if action == TransformAction.REPLACE:
        return _apply_replace(tree, transform, adapter, captures)

    if action == TransformAction.WRAP:
        return _apply_wrap(tree, transform, adapter, captures)

    if action in (TransformAction.INSERT_BEFORE, TransformAction.INSERT_AFTER):
        return _apply_insert(tree, transform, adapter, captures, action)

    return False, f"unknown action: {action!r}"


def _apply_replace(tree: Any, transform: Transformation,
                   adapter: InterpreterAdapter, captures: dict) -> tuple[bool, str]:
    """REPLACE: swap target_node with new_node in parent[field_name]."""
    parent, field_name = find_parent(tree, transform.target_node, adapter)
    if parent is None or field_name is None:
        # Tree-sitter: parent may be None because we use byte splice instead
        if hasattr(transform.target_node, "start_byte"):
            new_node = build_from_spec(transform.new_node, adapter, captures, transform.target_node)
            ok = adapter.replace_node(None, "", transform.target_node, new_node)
            return ok, "splice" if ok else "splice failed"
        return False, "parent not found"

    new_node = build_from_spec(transform.new_node, adapter, captures, transform.target_node)
    ok = adapter.replace_node(parent, field_name, transform.target_node, new_node)
    return ok, "replace" if ok else "replace failed"


def _apply_remove(tree: Any, transform: Transformation,
                  adapter: InterpreterAdapter) -> tuple[bool, str]:
    """REMOVE: delete target_node from parent[field_name]."""
    parent, field_name = find_parent(tree, transform.target_node, adapter)
    if parent is None or field_name is None:
        return False, "parent not found"
    # For REMOVE, we replace with None — adapter handles list filtering
    # (Python: list.remove; tree-sitter: splice with empty bytes)
    ok = adapter.replace_node(parent, field_name, transform.target_node, None)
    return ok, "remove" if ok else "remove failed"


def _apply_wrap(tree: Any, transform: Transformation,
                adapter: InterpreterAdapter, captures: dict) -> tuple[bool, str]:
    """WRAP: replace target_node with new_node, where new_node has a field
    that holds target_node (specified by transform.wrap_field).

    Example: wrap eval(x) with ast.literal_eval(<<eval(x)>>)
        new_node = NodeSpec("Call", {
            "func": NodeSpec("Attribute", {...}),
            "args": ["$node:TARGET"],   # this is where target goes
        })
        wrap_field = "args.0"
    """
    if transform.wrap_field is None:
        # Default: first positional arg
        if adapter.NAME == "python":
            wrap_field = "args.0"
        else:
            wrap_field = "arguments.0"
    else:
        wrap_field = transform.wrap_field

    # Build the wrapper, but first inject $node:TARGET into the wrap_field
    spec = transform.new_node
    field_path = wrap_field.split(".")
    field_name = field_path[0]

    # Inject target_node at the specified field
    new_fields = dict(spec.fields)
    if len(field_path) == 1:
        # Scalar field — direct assignment
        new_fields[field_name] = "$node:TARGET"
    elif len(field_path) == 2 and field_path[1].isdigit():
        # List field with index — append or set at index
        idx = int(field_path[1])
        existing = new_fields.get(field_name, [])
        if not isinstance(existing, list):
            existing = [existing]
        # Extend list to required size
        while len(existing) <= idx:
            existing.append(None)
        existing[idx] = "$node:TARGET"
        new_fields[field_name] = existing
    else:
        return False, f"unsupported wrap_field format: {wrap_field!r}"

    wrapped_spec = NodeSpec(node_type=spec.node_type, fields=new_fields)
    new_node = build_from_spec(wrapped_spec, adapter, captures, transform.target_node)

    parent, parent_field = find_parent(tree, transform.target_node, adapter)
    if parent is None or parent_field is None:
        if hasattr(transform.target_node, "start_byte"):
            ok = adapter.replace_node(None, "", transform.target_node, new_node)
            return ok, "wrap-splice" if ok else "wrap-splice failed"
        return False, "parent not found for wrap"

    ok = adapter.replace_node(parent, parent_field, transform.target_node, new_node)
    return ok, "wrap" if ok else "wrap failed"


def _apply_insert(tree: Any, transform: Transformation,
                  adapter: InterpreterAdapter, captures: dict,
                  action: str) -> tuple[bool, str]:
    """INSERT_BEFORE / INSERT_AFTER: insert new_node as sibling of target.

    For Python ast: requires parent to be a block (Module, FunctionDef, etc.)
    with a `body` list. We insert before/after target in that list.

    For tree-sitter: similar — find the parent statement_block and splice.

    Currently a simplified implementation — only supports inserting at
    the same level as target_node within a body list.
    """
    parent, field_name = find_parent(tree, transform.target_node, adapter)
    if parent is None or field_name is None:
        return False, "parent not found for insert"

    # Find the body list of the parent
    body = getattr(parent, "body", None) or getattr(parent, "children", None)
    if not isinstance(body, list):
        return False, f"parent has no list body/children for insert"

    new_node = build_from_spec(transform.new_node, adapter, captures, transform.target_node)

    # Find index of target in body
    try:
        idx = body.index(transform.target_node)
    except ValueError:
        return False, "target not in parent body"

    if action == TransformAction.INSERT_BEFORE:
        body.insert(idx, new_node)
    else:  # INSERT_AFTER
        body.insert(idx + 1, new_node)

    return True, f"insert_{action}"


# =============================================================================
# Batch application — for SmartInterpreter
# =============================================================================

def apply_transformations(tree: Any, transforms: list[Transformation],
                          adapter: InterpreterAdapter,
                          captures_list: list[dict]) -> list[dict]:
    """Apply multiple transformations to a tree.

    For Python ast: mutates `tree` in place. Returns list of result dicts.
    For tree-sitter: each replace_node splice creates a new tree cached
    on the adapter; the caller must call adapter.get_current_tree() and
    adapter.get_current_source() after batch application.

    IMPORTANT: transformations are applied in REVERSE order of original
    detection (last detected first). This prevents byte offsets / list
    indices from being invalidated by earlier transformations.

    Args:
        tree: parsed tree
        transforms: list of Transformation objects
        adapter: language adapter
        captures_list: list of capture dicts (parallel to transforms)

    Returns:
        list of {success, message, transform_index} dicts for logging
    """
    results = []
    # Reverse order — apply last transform first to keep earlier indices valid
    indices = list(range(len(transforms) - 1, -1, -1))

    for i in indices:
        t = transforms[i]
        caps = captures_list[i] if i < len(captures_list) else {}
        try:
            ok, msg = apply_transformation(tree, t, adapter, caps)
        except Exception as e:
            ok, msg = False, f"exception: {e}"
        results.append({
            "index": i,
            "success": ok,
            "message": msg,
            "action": t.action,
        })

    return results
