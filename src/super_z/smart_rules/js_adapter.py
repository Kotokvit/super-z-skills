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

Stage 4 additions:
  - build() — tree-sitter nodes are immutable. We build by emitting
    source text from a NodeSpec and re-parsing. The returned node is
    a synthetic single-expression tree's root.
  - emit() — for tree-sitter, just decode tree.root_node.text. After
    a tree edit, re-parse the modified source to get a fresh tree.
  - query() — default impl: walk + pattern match (delegates to
    generator's matcher). Tree-sitter's native query language is a
    future optimization.
  - replace_node() — tree-sitter nodes are immutable. We splice
    source bytes: take original source, replace [old.start_byte,
    old.end_byte) with new_node's text, re-parse the result.
    Returns True on success; caller is responsible for re-emitting
    the modified tree.

Tree-sitter node types relevant to our schema rules:
  - call_expression         (eval(...), fn(...))
  - member_expression       (obj.method, obj.prop)
  - assignment_expression   (x = y)
  - identifier              (bare name)
  - property_identifier     (name after .)
  - arguments               (parenthesized args)
  - string                  (string literal)
  - program                 (root)
"""
from __future__ import annotations

import sys
from typing import Any, Iterator

# tree-sitter imports — installed via pip in Codespace
import tree_sitter_javascript as tsj
from tree_sitter import Language, Parser, Node

from .adapter_protocol import InterpreterAdapter, Position, NodeSpec


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

        # Stage 4: source bytes cache for replace_node() splicing.
        # parse() stores the original bytes here so replace_node can splice.
        self._current_source: bytes | None = None

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _discover_node_types() -> set[str]:
        """Return known tree-sitter JS node types."""
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
        # Stage 4: cache source bytes for replace_node() splicing
        self._current_source = source
        return _PARSER.parse(source)

    def walk(self, tree: Any) -> Iterator[Any]:
        """Iterate all nodes in the tree, filtering punctuation."""
        cursor = tree.walk()
        visited = set()

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
        """Walk a dotted path from `node`, return the resolved value or None."""
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
            line=sp[0] + 1,
            col=sp[1],
            end_line=ep[0] + 1,
            end_col=ep[1],
        )

    def get_node_type(self, node: Any) -> str:
        """Return canonical node type name."""
        if hasattr(node, "type"):
            return node.type
        return type(node).__name__

    def get_source_text(self, node: Any, source_lines: list[str]) -> str:
        """Return the source line for this node, stripped."""
        if not hasattr(node, "start_point"):
            return ""
        line = node.start_point[0]
        if 0 <= line < len(source_lines):
            return source_lines[line].strip()
        return ""

    # ------------------------------------------------------------------ #
    # Stage 4: IR transforms — build / emit / query / replace_node
    # ------------------------------------------------------------------ #

    def build(self, node_type: str, fields: dict) -> Any:
        """Construct a new tree-sitter node by type name + fields.

        Tree-sitter nodes are immutable — we can't construct them
        in-memory like Python ast nodes. Instead, we emit source text
        from the NodeSpec and re-parse it, then return the root node
        of the resulting tree.

        This is less efficient than Python's build() but produces a
        real tree-sitter Node with valid positions, text, etc.

        Args:
            node_type: tree-sitter node type, e.g. "call_expression"
            fields: dict of {field_name: value} where values may be
                    scalars, NodeSpecs, or lists of the above

        Returns:
            tree_sitter.Node — root of a freshly-parsed synthetic tree
        """
        src = self._emit_node_source(node_type, fields)
        # Wrap in expression statement and parse
        tree = _PARSER.parse(src.encode("utf-8"))
        # For expression-statement nodes, root_node is program -> expr_stmt -> expr
        # We unwrap to the actual node the caller asked for
        return self._unwrap_synthetic(tree.root_node, node_type)

    def _emit_node_source(self, node_type: str, fields: dict) -> str:
        """Emit source text for a synthetic node of given type + fields.

        Handles common JS node types explicitly. Unknown types fall back
        to a generic best-effort template.
        """
        # Resolve NodeSpec values recursively first
        resolved = {}
        for k, v in fields.items():
            resolved[k] = self._resolve_emit_value(v)

        if node_type == "identifier":
            return str(resolved.get("text", resolved.get("name", "x")))

        if node_type == "string":
            text = resolved.get("text", "")
            # Strip outer quotes if present
            if len(text) >= 2 and text[0] in ('"', "'", "`") and text[-1] == text[0]:
                pass
            else:
                text = f'"{text}"'
            return text

        if node_type == "number":
            return str(resolved.get("text", "0"))

        if node_type == "member_expression":
            obj = resolved.get("object", "")
            prop = resolved.get("property", "")
            obj_src = obj if isinstance(obj, str) else self.unparse(obj)
            prop_src = prop if isinstance(prop, str) else self.unparse(prop)
            return f"{obj_src}.{prop_src}"

        if node_type == "call_expression":
            func = resolved.get("function", "")
            args = resolved.get("arguments", [])
            func_src = func if isinstance(func, str) else self.unparse(func)
            if isinstance(args, list):
                args_src = ", ".join(
                    a if isinstance(a, str) else self.unparse(a) for a in args
                )
            else:
                args_src = self.unparse(args)
            return f"{func_src}({args_src})"

        if node_type == "assignment_expression":
            left = resolved.get("left", "")
            right = resolved.get("right", "")
            left_src = left if isinstance(left, str) else self.unparse(left)
            right_src = right if isinstance(right, str) else self.unparse(right)
            return f"{left_src} = {right_src}"

        if node_type == "expression_statement":
            expr = resolved.get("expression", "")
            expr_src = expr if isinstance(expr, str) else self.unparse(expr)
            return f"{expr_src};"

        # Generic fallback — best-effort concatenation
        parts = []
        for k, v in resolved.items():
            v_src = v if isinstance(v, str) else self.unparse(v)
            parts.append(f"/* {k}={v_src} */")
        return f"/* unknown:{node_type} */ " + " ".join(parts)

    def _resolve_emit_value(self, v: Any) -> Any:
        """Resolve NodeSpec into source string for emit; pass through nodes/strings."""
        if isinstance(v, NodeSpec):
            # Build a synthetic node, then unparse it back to source
            sub_node = self.build(v.node_type, v.fields)
            return self.unparse(sub_node)
        if isinstance(v, list):
            return [self._resolve_emit_value(x) for x in v]
        return v

    def _unwrap_synthetic(self, root: Any, target_type: str) -> Any:
        """Walk synthetic tree to find first node matching target_type."""
        if root.type == target_type:
            return root
        for child in root.children:
            if child.type == target_type:
                return child
            result = self._unwrap_synthetic(child, target_type)
            if result is not None:
                return result
        # If target_type not found, return first named child (best-effort)
        named = root.named_children if hasattr(root, "named_children") else []
        if named:
            return named[0]
        return root

    def emit(self, tree: Any) -> str:
        """Emit source code from a tree-sitter tree.

        For tree-sitter, this is just decoding tree.root_node.text.
        After a replace_node() splice, the tree's root_node.text already
        reflects the modified source.
        """
        try:
            return tree.root_node.text.decode("utf-8", errors="replace")
        except Exception as e:
            return f"/* emit error: {e} */"

    def query(self, tree: Any, pattern: dict) -> Iterator[Any]:
        """Query tree for nodes matching pattern. Default: walk + match.

        Tree-sitter has a native query language (tree-sitter queries)
        that could be used for performance, but for Stage 4 we use the
        same walk + match approach as AstAdapter for consistency.
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
        """Replace old_node with new_node by splicing source bytes.

        Tree-sitter nodes are immutable — we cannot mutate them in place.
        Instead, we splice the source: take the original source bytes,
        replace [old_node.start_byte, old_node.end_byte) with the new
        node's source text, and re-parse.

        IMPORTANT: This method invalidates the `parent` and `old_node`
        references — the caller MUST re-walk the returned tree (or
        use the cache_source() pattern below).

        Returns True on success, False if splice failed.

        Note: Because tree-sitter nodes are immutable, the standard
        Protocol signature (parent, field_name, old, new) is misleading
        for JS — we only use old_node's byte range. The parent and
        field_name args are ignored.
        """
        if self._current_source is None:
            return False
        if not hasattr(old_node, "start_byte") or not hasattr(old_node, "end_byte"):
            return False

        # Get new_node's source text
        if hasattr(new_node, "text"):
            new_text = new_node.text
            if isinstance(new_text, str):
                new_text = new_text.encode("utf-8")
        elif isinstance(new_node, str):
            new_text = new_node.encode("utf-8")
        else:
            return False

        # Splice: original[:start] + new + original[end:]
        src = self._current_source
        start = old_node.start_byte
        end = old_node.end_byte
        new_src = src[:start] + new_text + src[end:]

        # Re-parse and update cache
        self._current_source = new_src
        # NOTE: we cannot replace `tree` reference in caller's scope.
        # Caller must call adapter.get_current_tree() to retrieve the
        # updated tree. This is a known limitation of immutable-AST adapters.
        self._last_tree = _PARSER.parse(new_src)
        return True

    def get_current_tree(self) -> Any:
        """Return the most recently re-parsed tree after a replace_node().

        Tree-sitter nodes are immutable, so replace_node() can't mutate
        the existing tree. Instead, it re-parses the spliced source and
        caches the new tree here. SmartInterpreter calls this after
        applying transformations to get the updated tree.
        """
        return getattr(self, "_last_tree", None)

    def get_current_source(self) -> str:
        """Return the current source bytes (post-splice) as string."""
        if self._current_source is None:
            return ""
        return self._current_source.decode("utf-8", errors="replace")


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
