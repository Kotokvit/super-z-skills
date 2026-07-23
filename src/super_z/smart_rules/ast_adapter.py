"""
ast_adapter.py — Adapter that bridges schema intents to current interpreter's AST.

The adapter does NOT know about specific rules. It only knows:
  1. What AST node types exist in this interpreter (introspected at construction).
  2. How to navigate paths through a node (e.g. "func.value.id").
  3. How to unparse a sub-node back to source text.

When the interpreter changes (Python 3.11 -> 3.13, or a different language),
the adapter is re-created. The generator then re-compiles the schema against
the new adapter. The schema YAML does not change.

For non-Python interpreters (tree-sitter for JS, rust-analyzer, etc.), a sibling
adapter implementing the same interface can be plugged in. The schema's
`interpreter_target: python` annotation filters rules to those whose node_type
is meaningful for this adapter.
"""
from __future__ import annotations

import ast
import inspect
import sys
from typing import Any, Iterator


# Path DSL operators that generator.py understands:
#   "func.id"          -> attribute chain
#   "args.0"           -> numeric index into list/tuple
#   "keywords.shell.value" -> keyword lookup by .arg, then .value
#   "body.length"      -> len() of a list
#   "body.0.type"      -> type(node).__name__ of indexed child
#   "names.join"       -> ", ".join(list_of_strings)
#   "id.lower"         -> str.lower() of a string field
#   "value.value.kind" -> type(x).__name__ of nested constant


class AstAdapter:
    """Bridge between schema paths and concrete AST nodes for this interpreter.

    The adapter is the ONLY thing that knows about Python's `ast` module.
    Everything else (schema, generator, smart interpreter) is language-neutral.
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
    # Path navigation — the heart of the pattern DSL
    # ------------------------------------------------------------------ #

    def navigate(self, node: Any, path: str) -> Any:
        """Walk a dotted path from `node`, return the resolved value or None.

        Path syntax:
          "func.id"             -> node.func.id
          "args.0"              -> node.args[0]      (numeric index)
          "args.0.type"         -> type(node.args[0]).__name__
          "body.length"         -> len(node.body)
          "body.0.type"         -> type(node.body[0]).__name__
          "keywords.shell.value"-> find kw with .arg=='shell', return .value
          "names.join"          -> ", ".join(node.names) (strings)
          "id.lower"            -> node.id.lower()   (string method call)
          "value.value.kind"    -> type(node.value.value).__name__

        Navigation never raises — missing path returns None. This lets rules
        declare patterns that match across optional fields.
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

        # Special: "_class" — AST class name (use _class to avoid clash with
        # the ExceptHandler.type field, which is the exception type or None).
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

    def walk(self, tree: ast.AST) -> Iterator[ast.AST]:
        """Iterate all nodes in the tree. Adapter exposes this so non-Python
        adapters can plug in their own traversal (e.g. tree-sitter cursors)."""
        return ast.walk(tree)

    def iter_children(self, node: ast.AST) -> Iterator[ast.AST]:
        """Iterate direct children of a node."""
        return ast.iter_child_nodes(node)

    def parse(self, source: str, filename: str = "<code>") -> ast.AST:
        """Parse source into AST. Raises SyntaxError on invalid syntax."""
        return ast.parse(source, filename=filename)


# Sentinel for "path does not resolve" — distinct from None which is a valid value
_MISSING = object()
