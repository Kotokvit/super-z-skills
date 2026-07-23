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
    # Stage 4: IR transforms — inherited from Protocol as NotImplementedError
    # ------------------------------------------------------------------ #
    # build(), emit(), query() — inherited stubs raise NotImplementedError

    def build(self, node_type: str, fields: dict) -> Any:
        """Construct a new AST node. Stage 4 — not yet implemented."""
        raise NotImplementedError(
            f"{self.NAME} adapter: build() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )

    def emit(self, tree: Any) -> str:
        """Emit source code from AST. Stage 4 — not yet implemented."""
        raise NotImplementedError(
            f"{self.NAME} adapter: emit() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )

    def query(self, tree: Any, pattern: dict) -> Iterator[Any]:
        """Query AST for nodes matching pattern. Stage 4 — not yet implemented."""
        raise NotImplementedError(
            f"{self.NAME} adapter: query() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )


# Sentinels
_MISSING = object()

# Verify AstAdapter satisfies the Protocol (runtime check)
assert isinstance(AstAdapter(), InterpreterAdapter), \
    "AstAdapter does not implement InterpreterAdapter Protocol"
