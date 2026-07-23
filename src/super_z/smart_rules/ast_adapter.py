"""
ast_adapter.py — Python `ast` module adapter.

Implements InterpreterAdapter Protocol (see adapter_protocol.py) for the
Python interpreter. This is the reference implementation — when writing
new adapters (tree-sitter JS, etc.), use this as a template.

The adapter does NOT know about specific rules. It only knows:
  1. What AST node types exist in this interpreter (introspected at construction).
  2. How to navigate paths through a node (e.g. "func.value.id").
  3. How to unparse a sub-node back to source text.
  4. How to extract position / node type / source text from a node.

Stage 4 additions:
  5. build() — construct new AST nodes by type name + fields.
  6. emit() — full tree -> source via ast.unparse + ast.fix_missing_locations.
  7. query() — walk + pattern match (delegates to generator's matcher).
  8. replace_node() — mutate parent[field_name] to swap/insert/remove.

When the interpreter changes (Python 3.11 -> 3.13, or a different language),
a different adapter is plugged in. The Protocol stays the same.
"""
from __future__ import annotations

import ast
import inspect
import sys
from typing import Any, Iterator

from .adapter_protocol import InterpreterAdapter, Position


# Path DSL operators that generator.py understands:
#   "func.id"          -> attribute chain
#   "args.0"           -> numeric index into list/tuple
#   "keywords.shell.value" -> keyword lookup by .arg, then .value
#   "body.length"      -> len() of a list
#   "body.0._class"    -> canonical type name of indexed child
#   "names.join"       -> ", ".join(list_of_strings)
#   "id.lower"         -> str.lower() of a string field
#   "value.value._pytype" -> Python type name of nested constant


class AstAdapter:
    """Python `ast` module adapter. Implements InterpreterAdapter Protocol.

    The adapter is the ONLY thing that knows about Python's `ast` module.
    Everything else (schema, generator, smart interpreter) is language-neutral
    and operates through the Protocol interface.
    """

    NAME = "python"

    def __init__(self) -> None:
        # Introspect available AST node types — this is what makes the system
        # auto-adapt: when Python adds/removes node types, this dict changes.
        self.node_types: dict[str, type] = self._discover_node_types()
        self.version: tuple[int, int, int] = sys.version_info[:3]
        self.version_str = f"{self.version[0]}.{self.version[1]}.{self.version[2]}"

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _discover_node_types() -> dict[str, type]:
        """Return {class_name: cls} for every AST node type in this interpreter.

        This is the key piece of auto-discovery. If Python 3.14 adds a new node
        type, it shows up here automatically — schema rules referencing it
        become active without code changes.
        """
        out = {}
        for name, cls in inspect.getmembers(ast, inspect.isclass):
            if issubclass(cls, ast.AST) and cls is not ast.AST:
                out[name] = cls
        return out

    def has_node_type(self, name: str) -> bool:
        """True if this interpreter's AST defines a node type by this name."""
        return name in self.node_types

    def validate_schema(self, schema: dict) -> list[str]:
        """Return list of warnings for schema rules referencing unknown nodes."""
        warnings = []
        for rule in schema.get("rules", []):
            node_type = rule.get("pattern", {}).get("node_type", "")
            if node_type == "Custom":
                continue  # handled by custom check, not AST node
            if not self.has_node_type(node_type):
                warnings.append(
                    f"{rule['id']}: node_type '{node_type}' not in "
                    f"{self.NAME} {self.version_str} — rule skipped"
                )
        return warnings

    # ------------------------------------------------------------------ #
    # Parsing & traversal
    # ------------------------------------------------------------------ #

    def parse(self, source: str, filename: str = "<code>") -> ast.AST:
        """Parse source into AST. Raises SyntaxError on invalid syntax."""
        return ast.parse(source, filename=filename)

    def walk(self, tree: ast.AST) -> Iterator[ast.AST]:
        """Iterate all nodes in the tree."""
        return ast.walk(tree)

    def iter_children(self, node: ast.AST) -> Iterator[ast.AST]:
        """Iterate direct children of a node."""
        return ast.iter_child_nodes(node)

    # ------------------------------------------------------------------ #
    # Path navigation — the heart of the pattern DSL
    # ------------------------------------------------------------------ #

    def navigate(self, node: Any, path: str) -> Any:
        """Walk a dotted path from `node`, return the resolved value or None.

        See adapter_protocol.py for path syntax specification.
        """
        if path == "":
            return node
        current = node
        for segment in path.split("."):
            current = self._step(current, segment)
            if current is _MISSING:
                return None
        return current

    def _step(self, node: Any, segment: str) -> Any:
        """Apply one navigation step. Returns _MISSING sentinel on failure."""
        if node is None:
            return _MISSING

        # Special: "length" — size of a list/tuple
        if segment == "length":
            if isinstance(node, (list, tuple)):
                return len(node)
            return _MISSING

        # Special: "_class" — canonical node type name (use _class to avoid
        # clash with the ExceptHandler.type field, which is the exception
        # type or None).
        if segment == "_class":
            return type(node).__name__

        # Special: "_pytype" — Python type name (str/int/bool/None) — for
        # checking Constant.value's Python type.
        if segment == "_pytype":
            return type(node).__name__

        # Special: "join" — list-of-strings -> single string
        if segment == "join":
            if isinstance(node, (list, tuple)) and all(isinstance(x, str) for x in node):
                return ", ".join(node)
            return _MISSING

        # Special: string method shortcuts — .lower / .upper
        if segment in ("lower", "upper", "strip") and isinstance(node, str):
            return getattr(node, segment)()

        # Numeric index into list/tuple: args.0, body.0
        if segment.isdigit():
            if isinstance(node, (list, tuple)):
                idx = int(segment)
                if 0 <= idx < len(node):
                    return node[idx]
            return _MISSING

        # Keyword lookup: keywords.shell -> find kw with .arg==segment
        if segment and isinstance(node, (list, tuple)) and node and isinstance(node[0], ast.keyword):
            for kw in node:
                if getattr(kw, "arg", None) == segment:
                    return kw
            return _MISSING

        # Attribute access — works on AST nodes and on most objects
        if hasattr(node, segment):
            return getattr(node, segment)

        return _MISSING

    # ------------------------------------------------------------------ #
    # Source reconstruction
    # ------------------------------------------------------------------ #

    def unparse(self, node: Any) -> str:
        """Reconstruct source text from a node. Returns '?' on failure."""
        if node is None or node is _MISSING:
            return "?"
        try:
            return ast.unparse(node)
        except Exception:
            return "?"

    # ------------------------------------------------------------------ #
    # Node metadata — Protocol methods
    # ------------------------------------------------------------------ #

    def get_position(self, node: Any) -> Position:
        """Return source position of a node.

        Python ast nodes have lineno (1-indexed), col_offset (0-indexed),
        end_lineno (1-indexed), end_col_offset (0-indexed).
        """
        line = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0)
        end_line = getattr(node, "end_lineno", line)
        end_col = getattr(node, "end_col_offset", col)
        return Position(line=line, col=col, end_line=end_line, end_col=end_col)

    def get_node_type(self, node: Any) -> str:
        """Return canonical node type name (Python ast class name)."""
        return type(node).__name__

    def get_source_text(self, node: Any, source_lines: list[str]) -> str:
        """Return the source line(s) for this node, stripped.

        For single-line nodes, returns the stripped line. For multi-line,
        returns the first line (sufficient for violation display).
        """
        line = getattr(node, "lineno", 1)
        if 0 < line <= len(source_lines):
            return source_lines[line - 1].strip()
        return ""

    # ------------------------------------------------------------------ #
    # Stage 4: IR transforms — build / emit / query / replace_node
    # ------------------------------------------------------------------ #

    def build(self, node_type: str, fields: dict) -> Any:
        """Construct a new AST node by type name + fields.

        Lenient: unknown field names are silently ignored (so a schema
        written for a future Python version doesn't break on current one).

        Args:
            node_type: ast class name, e.g. "Call", "Name", "Constant"
            fields: dict of {field_name: value}
                - scalars (str/int/bool/None) assigned directly
                - other ast.AST nodes assigned directly
                - lists of the above assigned as new lists
                - NodeSpec instances are recursively built via build()

        Returns:
            New ast.AST instance, with locations set to (1, 0, 1, 0)
            (caller is responsible for ast.copy_location if needed).

        Raises:
            ValueError if node_type is not a known ast class.
        """
        cls = self.node_types.get(node_type)
        if cls is None:
            raise ValueError(
                f"{self.NAME} adapter: unknown node_type {node_type!r}. "
                f"Known types: {len(self.node_types)} (call has_node_type to check)."
            )

        # Resolve NodeSpec values recursively; pass through native nodes/scalars
        resolved = {}
        # _fields is the canonical tuple of field names for this AST class
        valid_fields = set(cls._fields) if hasattr(cls, "_fields") else set()

        for k, v in fields.items():
            if valid_fields and k not in valid_fields:
                # Lenient: skip unknown field for this AST class
                continue
            resolved[k] = self._resolve_build_value(v)

        node = cls(**resolved)

        # Set dummy locations so ast.unparse / ast.fix_missing_locations work
        # (real locations are irrelevant — caller will replace_node + re-emit)
        try:
            node.lineno = 1
            node.col_offset = 0
            node.end_lineno = 1
            node.end_col_offset = 0
        except AttributeError:
            pass  # some nodes (e.g. Module) don't have lineno

        return node

    def _resolve_build_value(self, v: Any) -> Any:
        """Resolve a value for build() — recursively build NodeSpec, pass through rest."""
        # Lazy import to avoid circular dep at module load time
        from .adapter_protocol import NodeSpec
        if isinstance(v, NodeSpec):
            return self.build(v.node_type, v.fields)
        if isinstance(v, list):
            return [self._resolve_build_value(x) for x in v]
        if isinstance(v, tuple):
            return tuple(self._resolve_build_value(x) for x in v)
        # ast.AST, str, int, bool, None — pass through
        return v

    def emit(self, tree: Any) -> str:
        """Emit source code from a tree. Inverse of parse().

        Calls ast.fix_missing_locations first (transforms may have created
        nodes without lineno/col_offset), then ast.unparse.
        """
        try:
            ast.fix_missing_locations(tree)
            return ast.unparse(tree)
        except Exception as e:
            return f"# emit error: {e}"

    def query(self, tree: Any, pattern: dict) -> Iterator[Any]:
        """Query AST for nodes matching a pattern dict.

        Default implementation: walk tree, return nodes whose type matches
        pattern['node_type'] and (if pattern has 'match') whose conditions
        all pass. Reuses generator._compile_match_block to avoid duplication.
        """
        node_type = pattern.get("node_type")
        match_def = pattern.get("match", {})

        # Lazy import to avoid circular dep
        from .generator import _compile_match_block
        match_fn = _compile_match_block(match_def)

        for node in self.walk(tree):
            if node_type and self.get_node_type(node) != node_type:
                continue
            if match_fn(node, self):
                yield node

    def replace_node(self, parent: Any, field_name: str,
                     old_node: Any, new_node: Any) -> bool:
        """Replace old_node with new_node in parent[field_name].

        Returns True on success, False if old_node not found.

        For list fields: scans list, replaces by identity (is).
        For scalar fields: replaces by value (assigns new_node).
        """
        if parent is None:
            return False

        if not hasattr(parent, field_name):
            return False

        field_val = getattr(parent, field_name)

        # List field — scan and replace by identity
        if isinstance(field_val, list):
            for i, item in enumerate(field_val):
                if item is old_node:
                    if new_node is None:
                        # REMOVE: delete from list
                        del field_val[i]
                    else:
                        field_val[i] = new_node
                    return True
            return False

        # Tuple field — convert to list, replace, convert back
        if isinstance(field_val, tuple):
            lst = list(field_val)
            for i, item in enumerate(lst):
                if item is old_node:
                    if new_node is None:
                        del lst[i]
                    else:
                        lst[i] = new_node
                    setattr(parent, field_name, tuple(lst))
                    return True
            return False

        # Scalar field — direct assignment (only if old matches)
        if field_val is old_node:
            setattr(parent, field_name, new_node)
            return True

        return False


# Sentinels
_MISSING = object()

# Verify AstAdapter satisfies the Protocol (runtime check)
assert isinstance(AstAdapter(), InterpreterAdapter), \
    "AstAdapter does not implement InterpreterAdapter Protocol"
