"""
adapter_protocol.py — Formal contract for all language adapters.

This is the ABI of the smart interpreter system. Everything above the
adapter (SmartInterpreter, generator, schema.yaml) operates ONLY through
this interface. Everything below the adapter (Python ast, tree-sitter,
LLVM IR, Roslyn) is an implementation detail.

Stage 1 (this file): define the contract.
Stage 2: JS adapter (tree-sitter) implements it.
Stage 3: schema.yaml becomes truly language-agnostic (rules describe
         intents, not syntax — patterns are per-interpreter).
Stage 4: build/emit/query methods become real, enabling IR transforms.
Stage 5: Accept/Reject UI on top of stable architecture.

A valid adapter MUST implement all methods marked "required".
Methods marked "Stage 4" raise NotImplementedError until Stage 4 lands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Protocol, runtime_checkable


# =============================================================================
# Canonical data structures — adapter-agnostic
# =============================================================================

@dataclass(frozen=True)
class Position:
    """Source position of a node. Line is 1-indexed, col is 0-indexed.

    All adapters MUST return Position for any node they expose. This is the
    only way SmartInterpreter can report "line N" in violation records
    without knowing how the underlying parser numbers lines.
    """
    line: int           # 1-indexed start line
    col: int            # 0-indexed start column
    end_line: int       # 1-indexed end line (inclusive)
    end_col: int        # 0-indexed end column (exclusive)


@dataclass(frozen=True)
class NodeInfo:
    """Snapshot of a node's metadata. Adapter-agnostic.

    SmartInterpreter uses this to build violation records. It does NOT
    need to know the node's native type — only the canonical node_type
    string that the adapter exposes.
    """
    node_type: str      # canonical type name ("Call", "Function", etc.)
    position: Position
    source_text: str    # source snippet for this node (may be multi-line)


# =============================================================================
# The contract
# =============================================================================

@runtime_checkable
class InterpreterAdapter(Protocol):
    """ABI between smart interpreter core and language-specific parsers.

    Implementations:
      - AstAdapter (Python, via `ast` module) — Stage 1
      - TreeSitterJsAdapter (JavaScript, via tree-sitter) — Stage 2
      - Future: RustAdapter (rust-analyzer), LlvmAdapter (LLVM IR), etc.

    The Protocol is runtime_checkable so consumers can verify
    `isinstance(adapter, InterpreterAdapter)` before use.
    """

    # ------------------------------------------------------------------ #
    # Identity — used for cache keying and schema filtering
    # ------------------------------------------------------------------ #

    NAME: str               # e.g. "python", "javascript"
    version: tuple          # e.g. (3, 12, 1)
    version_str: str        # e.g. "3.12.1"

    # ------------------------------------------------------------------ #
    # Introspection — what this interpreter can parse
    # ------------------------------------------------------------------ #

    def has_node_type(self, name: str) -> bool:
        """True if this interpreter's parser defines a node type by this name.

        Used by generator to skip rules that reference node types not
        available in the current interpreter (e.g. a Python 3.13-only
        node when running on 3.11, or a JS-specific node when running
        Python adapter).
        """
        ...

    def validate_schema(self, schema: dict) -> list[str]:
        """Return warnings for schema rules that reference unknown node types.

        Non-fatal: bad rules are skipped, good rules still compile.
        """
        ...

    # ------------------------------------------------------------------ #
    # Parsing & traversal
    # ------------------------------------------------------------------ #

    def parse(self, source: str, filename: str = "<code>") -> Any:
        """Parse source code into a tree. Raises SyntaxError on invalid input.

        The return type is `Any` because each adapter returns its native
        tree type (ast.Module for Python, tree_sitter.Tree for JS, etc.).
        SmartInterpreter never inspects the tree directly — it only calls
        adapter.walk() on it.
        """
        ...

    def walk(self, tree: Any) -> Iterator[Any]:
        """Iterate all nodes in a tree, depth-first.

        Adapters MAY use a different traversal order if their language
        has idiomatic conventions (e.g. tree-sitter's cursor walk).
        SmartInterpreter does not depend on order — only completeness.
        """
        ...

    def iter_children(self, node: Any) -> Iterator[Any]:
        """Iterate direct children of a node. Used for recursive custom checks
        (e.g. deep_nesting)."""
        ...

    # ------------------------------------------------------------------ #
    # Path navigation DSL — language-agnostic
    # ------------------------------------------------------------------ #

    def navigate(self, node: Any, path: str) -> Any:
        """Walk a dotted path from `node`, return the resolved value or None.

        Path syntax is defined per-adapter (Python uses `func.id`,
        tree-sitter JS may use `function.text`), but the SEMANTICS are
        universal: navigate a tree of named fields and list indices.

        Special segments all adapters MUST support:
          - "_class"  -> canonical node type name of current node
          - "_pytype" -> Python type name of current value (for scalar checks)
          - "length"  -> len() of list/tuple
          - "<digit>" -> numeric index into list/tuple
          - "join"    -> ", ".join(list_of_strings)
          - "lower/upper/strip" -> string methods

        Adapters MAY add their own special segments (e.g. tree-sitter's
        `text` to get source text of a node).
        """
        ...

    # ------------------------------------------------------------------ #
    # Source reconstruction
    # ------------------------------------------------------------------ #

    def unparse(self, node: Any) -> str:
        """Reconstruct source text from a node. Returns '?' on failure.

        Used to fill in `{{arg0}}` placeholders in fix templates.
        Adapters that don't support unparse should return the source
        snippet for the node's position range.
        """
        ...

    # ------------------------------------------------------------------ #
    # Node metadata — used by SmartInterpreter for violation records
    # ------------------------------------------------------------------ #

    def get_position(self, node: Any) -> Position:
        """Return source position of a node. All adapters MUST implement this
        so violation records have correct line/col info."""
        ...

    def get_node_type(self, node: Any) -> str:
        """Return canonical node type name. Used to record `ast_node_type`
        in violation dicts. Should match the names used in schema.yaml
        `node_type` field."""
        ...

    def get_source_text(self, node: Any, source_lines: list[str]) -> str:
        """Return the source line(s) for this node. Used for the
        `source_snippet` field in violation dicts."""
        ...

    # ------------------------------------------------------------------ #
    # Stage 4: IR transforms — stubs, NotImplementedError until Stage 4
    # ------------------------------------------------------------------ #

    def build(self, node_type: str, fields: dict) -> Any:
        """Construct a new node of the given type with the given fields.

        Stage 4: enables AST→AST transformations (replace eval(x) with
        ast.literal_eval(x) at the IR level, not as text replacement).
        """
        raise NotImplementedError(
            f"{self.NAME} adapter: build() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )

    def emit(self, tree: Any) -> str:
        """Emit source code from a tree. Inverse of parse().

        Stage 4: enables 'apply fix' workflow — transform tree, emit,
        write back to file. Without this, fixes can only be suggested
        as text, not applied.
        """
        raise NotImplementedError(
            f"{self.NAME} adapter: emit() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )

    def query(self, tree: Any, pattern: dict) -> Iterator[Any]:
        """Query tree for nodes matching a pattern. Declarative alternative
        to manual walk + check.

        Stage 4: enables performance optimizations (e.g. tree-sitter
        queries, XPath over XML-like IRs) and rule composition.
        """
        raise NotImplementedError(
            f"{self.NAME} adapter: query() is Stage 4 (IR transforms). "
            "Not implemented yet."
        )
