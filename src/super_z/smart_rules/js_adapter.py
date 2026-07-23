"""
js_adapter.py — JavaScript adapter via tree-sitter.

Implements InterpreterAdapter Protocol (see adapter_protocol.py) for
JavaScript source code, using tree-sitter as the underlying parser.

This is the SECOND adapter implementation, proving that the Protocol
defined in Stage 1 is truly language-agnostic. SmartInterpreter does
not change a single line — only this adapter class is added.

Key differences from AstAdapter (Python):
  - parse() returns tree_sitter.Tree, not ast.Module
  - walk() uses tree_sitter.Node.walk() cursor traversal
  - navigate() uses child_by_field_name() instead of attribute access
  - Punctuation nodes ( '(', ')', ';' ) are filtered out of walk() —
    they're syntactic noise that Python ast doesn't expose
  - Source text comes from node.text (bytes) instead of ast.unparse
  - Position comes from start_point/end_point (row, col) Points

Tree-sitter node types relevant to our schema rules:
  - call_expression         (eval(...), fn(...))
  - member_expression       (obj.method, obj.prop)
  - assignment_expression   (x = y)
  - identifier              (bare name)
  - property_identifier     (name after .)
  - arguments               (parenthesized args)
  - string                  (string literal)
  - program                 (root)

Path navigation examples (JS-specific):
  - "function"              -> child_by_field_name("function")
  - "function.text"         -> source text of function field
  - "arguments.0"           -> first child of arguments (skip punctuation)
  - "left"                  -> child_by_field_name("left") for binary/assign
  - "right"                 -> child_by_field_name("right")
  - "operator"              -> child_by_field_name("operator") (text)
"""
from __future__ import annotations

import sys
from typing import Any, Iterator

# tree-sitter imports — installed via pip in Codespace
import tree_sitter_javascript as tsj
from tree_sitter import Language, Parser, Node

from .adapter_protocol import InterpreterAdapter, Position


# Construct the parser ONCE at module import — heavy operation
_JS_LANG = Language(tsj.language())
_PARSER = Parser(_JS_LANG)


# Punctuation node types — filtered from walk() because Python ast doesn't
# expose them and rules shouldn't need to match on them
_PUNCTUATION_TYPES = frozenset({
    "(", ")", "[", "]", "{", "}", ";", ",", ".", ":", "?",
    "=", "==", "===", "!=", "!==",
    "+", "-", "*", "/", "%", "**",
    "+=", "-=", "*=", "/=", "%=", "**=",
    "<", ">", "<=", ">=",
    "&&", "||", "!", "&", "|", "^", "~",
    "<<", ">>", ">>>",
    "=>", "...", "?.", "??",
})


class TreeSitterJsAdapter:
    """JavaScript adapter via tree-sitter. Implements InterpreterAdapter Protocol.

    The adapter does NOT know about specific rules. It only knows:
      1. How to parse JS source via tree-sitter
      2. How to walk tree-sitter nodes (filtering punctuation)
      3. How to navigate field paths (using child_by_field_name)
      4. How to extract position / type / source text from a Node
    """

    NAME = "javascript"

    def __init__(self) -> None:
        # JS doesn't have a "version" in the same sense Python does.
        # We use the tree-sitter-javascript package version instead.
        import tree_sitter_javascript
        self.version_str = getattr(tree_sitter_javascript, "__version__", "0.25.0")
        # Parse version string into tuple for cache keying
        parts = self.version_str.split(".")
        try:
            self.version = tuple(int(p) for p in parts[:3])
        except ValueError:
            self.version = (0, 25, 0)

        # Discover available node types — tree-sitter exposes them via the
        # language. We use a curated list since tree-sitter's introspection
        # is more limited than Python ast's inspect.getmembers.
        self.node_types: set[str] = self._discover_node_types()

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _discover_node_types() -> set[str]:
        """Return known tree-sitter JS node types.

        Tree-sitter doesn't expose a clean API for this in all versions,
        so we use a curated list. This is the one place where the adapter
        is less auto-discovering than AstAdapter — but the list is stable
        across tree-sitter versions.
        """
        return _JS_NODE_TYPES

    def has_node_type(self, name: str) -> bool:
        """True if this interpreter's parser defines a node type by this name."""
        return name in self.node_types

    def validate_schema(self, schema: dict) -> list[str]:
        """Return list of warnings for schema rules referencing unknown nodes."""
        warnings = []
        for rule in schema.get("rules", []):
            node_type = rule.get("pattern", {}).get("node_type", "")
            if node_type == "Custom":
                continue
            if not self.has_node_type(node_type):
                warnings.append(
                    f"{rule['id']}: node_type '{node_type}' not in "
                    f"{self.NAME} {self.version_str} — rule skipped"
                )
        return warnings

    # ------------------------------------------------------------------ #
    # Parsing & traversal
    # ------------------------------------------------------------------ #

    def parse(self, source: str, filename: str = "<code>") -> Any:
        """Parse JS source into tree-sitter Tree.

        Tree-sitter is more lenient than Python ast — it produces a tree
        even for syntax errors (with ERROR nodes). We don't raise on
        syntax errors; rules just won't match inside ERROR subtrees.
        """
        if isinstance(source, str):
            source = source.encode("utf-8")
        return _PARSER.parse(source)

    def walk(self, tree: Any) -> Iterator[Any]:
        """Iterate all nodes in the tree, filtering punctuation.

        Tree-sitter exposes punctuation as separate nodes ('(', ';', etc.).
        These have no semantic meaning for our rules — they're syntactic
        noise. We filter them out so rules don't accidentally match on
        them and so the walk resembles Python ast.walk more closely.
        """
        cursor = tree.walk()
        visited = set()

        # Depth-first traversal
        def traverse(node):
            if id(node) in visited:
                return
            visited.add(id(node))
            if node.type not in _PUNCTUATION_TYPES and node.type != "ERROR":
                yield node
            for child in node.children:
                yield from traverse(child)

        yield from traverse(tree.root_node)

    def iter_children(self, node: Any) -> Iterator[Any]:
        """Iterate direct children, filtering punctuation."""
        for child in node.children:
            if child.type not in _PUNCTUATION_TYPES and child.type != "ERROR":
                yield child

    # ------------------------------------------------------------------ #
    # Path navigation — language-agnostic DSL
    # ------------------------------------------------------------------ #

    def navigate(self, node: Any, path: str) -> Any:
        """Walk a dotted path from `node`, return the resolved value or None.

        Path syntax (JS-specific additions over base DSL):
          - "function"             -> child_by_field_name("function")
          - "arguments"            -> child_by_field_name("arguments")
          - "left" / "right"       -> for binary/assignment expressions
          - "text"                 -> source text of node (bytes -> str)
          - "<field_name>.0"       -> numeric index into named children
          - Standard segments: _class, _pytype, length, join, lower/upper
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

        # Special: "_class" — canonical node type name
        if segment == "_class":
            return node.type if hasattr(node, "type") else type(node).__name__

        # Special: "_pytype" — Python type name (for scalar checks)
        if segment == "_pytype":
            return type(node).__name__

        # Special: "length" — size of a list/tuple
        if segment == "length":
            if isinstance(node, (list, tuple)):
                return len(node)
            # For tree-sitter nodes, length = number of named children
            if hasattr(node, "named_children"):
                return len(node.named_children)
            return _MISSING

        # Special: "join" — list-of-strings -> single string
        if segment == "join":
            if isinstance(node, (list, tuple)) and all(isinstance(x, str) for x in node):
                return ", ".join(node)
            return _MISSING

        # Special: string method shortcuts — .lower / .upper / .strip
        if segment in ("lower", "upper", "strip") and isinstance(node, str):
            return getattr(node, segment)()

        # Special: "text" — tree-sitter specific — source text of node
        if segment == "text":
            if hasattr(node, "text"):
                return node.text.decode("utf-8", errors="replace")
            return _MISSING

        # Numeric index into list/tuple (for arguments.0 etc.)
        if segment.isdigit():
            if isinstance(node, (list, tuple)):
                idx = int(segment)
                if 0 <= idx < len(node):
                    return node[idx]
                return _MISSING
            # For tree-sitter node, get nth named child (skip punctuation)
            if hasattr(node, "named_children"):
                named = node.named_children
                idx = int(segment)
                if 0 <= idx < len(named):
                    return named[idx]
                return _MISSING
            return _MISSING

        # Tree-sitter field access via child_by_field_name
        if hasattr(node, "child_by_field_name"):
            child = node.child_by_field_name(segment)
            if child is not None:
                return child
            # Field doesn't exist on this node — fall through to None
            return _MISSING

        # Fallback: attribute access (for non-tree-sitter objects)
        if hasattr(node, segment):
            return getattr(node, segment)

        return _MISSING

    # ------------------------------------------------------------------ #
    # Source reconstruction
    # ------------------------------------------------------------------ #

    def unparse(self, node: Any) -> str:
        """Reconstruct source text from a node.

        For tree-sitter nodes, this is just node.text — no generation
        needed because tree-sitter preserves source ranges.
        """
        if node is None or node is _MISSING:
            return "?"
        if hasattr(node, "text"):
            try:
                return node.text.decode("utf-8", errors="replace")
            except Exception:
                return "?"
        if isinstance(node, str):
            return node
        return "?"

    # ------------------------------------------------------------------ #
    # Node metadata — Protocol methods
    # ------------------------------------------------------------------ #

    def get_position(self, node: Any) -> Position:
        """Return source position of a node.

        Tree-sitter uses (row, col) Points. Row is 0-indexed in
        tree-sitter, 1-indexed in our Position. We convert here.
        """
        if not hasattr(node, "start_point"):
            return Position(line=1, col=0, end_line=1, end_col=0)
        sp = node.start_point
        ep = node.end_point
        return Position(
            line=sp[0] + 1,    # 0-indexed -> 1-indexed
            col=sp[1],         # 0-indexed (same)
            end_line=ep[0] + 1,
            end_col=ep[1],
        )

    def get_node_type(self, node: Any) -> str:
        """Return canonical node type name."""
        if hasattr(node, "type"):
            return node.type
        return type(node).__name__

    def get_source_text(self, node: Any, source_lines: list[str]) -> str:
        """Return the source line for this node, stripped.

        For multi-line nodes, returns the start line.
        """
        if not hasattr(node, "start_point"):
            return ""
        line = node.start_point[0]  # 0-indexed
        if 0 <= line < len(source_lines):
            return source_lines[line].strip()
        return ""

    # ------------------------------------------------------------------ #
    # Stage 4: IR transforms — stubs, NotImplementedError until Stage 4
    # ------------------------------------------------------------------ #

    def build(self, node_type: str, fields: dict) -> Any:
        """Construct a new tree-sitter node. Stage 4 — not yet implemented.

        Tree-sitter nodes are immutable parsed-tree nodes — building new
        ones requires the (unstable) edit API + re-parse. Stage 4 will
        implement this via tree-sitter's edit operations.
        """
        raise NotImplementedError(
            f"{self.NAME} adapter: build() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )

    def emit(self, tree: Any) -> str:
        """Emit source code from a tree-sitter tree. Stage 4 — not yet implemented.

        For tree-sitter, this is straightforward: walk the tree and emit
        node.text for leaves in order. But it's only meaningful after
        tree edits (Stage 4).
        """
        raise NotImplementedError(
            f"{self.NAME} adapter: emit() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )

    def query(self, tree: Any, pattern: dict) -> Iterator[Any]:
        """Query tree for nodes matching pattern. Stage 4 — not yet implemented.

        Tree-sitter has a powerful query language (tree-sitter queries)
        that will be exposed here in Stage 4 for performance and rule
        composition.
        """
        raise NotImplementedError(
            f"{self.NAME} adapter: query() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )


# =============================================================================
# Known tree-sitter JS node types — curated list
# =============================================================================

_JS_NODE_TYPES = frozenset({
    # Top-level
    "program", "expression_statement", "statement_block",
    # Declarations
    "variable_declaration", "lexical_declaration", "variable_declarator",
    "function_declaration", "function", "function_expression",
    "arrow_function", "generator_function", "generator_function_declaration",
    "class_declaration", "class", "class_body",
    "method_definition", "field_definition",
    # Expressions
    "call_expression", "member_expression", "assignment_expression",
    "binary_expression", "unary_expression", "update_expression",
    "logical_expression", "conditional_expression",
    "new_expression", "await_expression", "yield_expression",
    "sequence_expression", "parenthesized_expression", "spread_element",
    "template_literal", "template_string", "template_substitution",
    # Literals
    "string", "string_fragment", "number", "regex", "true", "false", "null",
    "undefined", "this", "super",
    # Identifiers
    "identifier", "property_identifier", "shorthand_property_identifier",
    "private_property_identifier",
    # Control flow
    "if_statement", "else", "for_statement", "for_in_statement",
    "while_statement", "do_statement", "switch_statement", "case",
    "break_statement", "continue_statement", "return_statement",
    "throw_statement", "try_statement", "catch", "finally",
    # Patterns
    "object_pattern", "array_pattern", "rest_pattern", "assignment_pattern",
    "object", "array", "pair", "pair_pattern",
    # Imports/exports
    "import_statement", "import_clause", "named_imports", "namespace_import",
    "export_statement",
    # Misc
    "arguments", "formal_parameters", "comment",
})


# Sentinels
_MISSING = object()

# Verify TreeSitterJsAdapter satisfies the Protocol (runtime check)
assert isinstance(TreeSitterJsAdapter(), InterpreterAdapter), \
    "TreeSitterJsAdapter does not implement InterpreterAdapter Protocol"
