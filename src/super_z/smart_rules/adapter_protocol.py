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


# =============================================================================>
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


# =============================================================================>
# Stage 4: IR Transform specification — declarative node + action
# =============================================================================

class TransformAction:
    """Enumeration of supported IR transform actions.

    Stage 4: these are the only ways the IR Transform Engine can mutate
    a tree. Every fix in schema.yaml maps to exactly one action.

    REPLACE       — swap target_node with new_node
    INSERT_BEFORE — insert new_node as previous sibling of target_node
    INSERT_AFTER  — insert new_node as next sibling of target_node
    REMOVE        — delete target_node (no new_node needed)
    WRAP          — replace target_node with new_node, where new_node
                    has a placeholder field that target_node is moved into
                    (e.g. wrap `eval(x)` with `ast.literal_eval(<<target>>)`)
    """
    REPLACE = "replace"
    INSERT_BEFORE = "insert_before"
    INSERT_AFTER = "insert_after"
    REMOVE = "remove"
    WRAP = "wrap"


@dataclass(frozen=True)
class NodeSpec:
    """Declarative blueprint for constructing a new node via adapter.build().

    Fields:
        node_type: canonical type name the adapter understands
                   (e.g. "Call" for Python, "call_expression" for JS)
        fields: dict of field_name -> value
                - values may be:
                  * strings/ints/bools (literal scalars)
                  * NodeSpec (recursively constructed sub-nodes)
                  * "$capture:NAME" (resolved from violation captures at
                    transform-application time)
                  * "$node:TARGET" (refers to the original target node —
                    used by WRAP actions to embed the original node inside
                    a new wrapper)
                - field "args"/"body" etc. accept lists of the above

    Example (replace eval(x) with ast.literal_eval(x)):
        NodeSpec(
            node_type="Call",
            fields={
                "func": NodeSpec("Attribute", {
                    "value": NodeSpec("Name", {"id": "ast"}),
                    "attr": "literal_eval",
                }),
                "args": ["$capture:arg0"],
            },
        )
    """
    node_type: str
    fields: dict


@dataclass(frozen=True)
class Transformation:
    """Declarative IR transformation.

    A CompiledRule with a `transform` field produces one of these per
    matched violation. SmartInterpreter.apply_transformation() consumes
    it through the adapter — never touching source text directly.

    Fields:
        action: one of TransformAction.*
        target_node: the AST node to act on (passed at runtime, not in schema)
        new_node: NodeSpec for the new node (REPLACE/INSERT_*/WRAP)
                  None for REMOVE
        wrap_field: for WRAP — name of the field in new_node where
                    target_node should be inserted (defaults to "args.0"
                    for Python calls, "arguments.0" for JS calls)
    """
    action: str
    target_node: Any
    new_node: NodeSpec | None = None
    wrap_field: str | None = None


# =============================================================================>
# The contract
# =============================================================================>

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
    # Stage 4: IR transforms — build/emit/query now REQUIRED (no longer stubs)
    # ------------------------------------------------------------------ #

    def build(self, node_type: str, fields: dict) -> Any:
        """Construct a new node of the given type with the given fields.

        Stage 4: enables AST->AST transformations (replace eval(x) with
        ast.literal_eval(x) at the IR level, not as text replacement).

        `fields` may contain:
          - native scalar values (str/int/bool/None)
          - native child nodes (already-constructed AST nodes from this adapter)
          - lists of the above
        Unknown/unsupported fields are silently ignored (lenient parsing).
        """
        ...

    def emit(self, tree: Any) -> str:
        """Emit source code from a tree. Inverse of parse().

        Stage 4: enables 'apply fix' workflow — transform tree, emit,
        write back to file. Without this, fixes can only be suggested
        as text, not applied.
        """
        ...

    def query(self, tree: Any, pattern: dict) -> Iterator[Any]:
        """Query tree for nodes matching a pattern. Declarative alternative
        to manual walk + check.

        Stage 4: enables performance optimizations (e.g. tree-sitter
        queries, XPath over XML-like IRs) and rule composition.

        Default implementation walks the tree and applies a generic
        matcher — adapters with native query languages may override.
        """
        ...

    # ------------------------------------------------------------------ #
    # Stage 4: Tree mutation — required for applying transformations
    # ------------------------------------------------------------------ #

    def replace_node(self, parent: Any, field_name: str, old_node: Any,
                     new_node: Any) -> bool:
        """Replace `old_node` with `new_node` in `parent[field_name]`.

        Returns True on success, False if old_node not found.

        For list fields, this scans the list and replaces the matching
        item (by identity). For scalar fields, replaces the value.

        Adapters MUST implement this so the IR Transform Engine can
        apply REPLACE/INSERT/REMOVE actions without knowing the
        concrete tree representation.
        """
        ...
