"""
diff_engine.py — Generate diffs for the Accept/Reject UI (Stage 5).

Two output formats:

1. unified_diff(before, after, filename) -> str
   Standard unified diff text (like `git diff`). Used for previewing
   the patched file in a single text block.

2. line_segments(before, after) -> list[Segment]
   Inline word-level diff for a single line. Used by the UI to
   highlight which parts of the line changed (red for removed,
   green for added). Powered by difflib.SequenceMatcher.

The UI consumes a JSON representation of these segments — see
templates/accept_reject.html for the rendering side.

This module is independent of Stage 4's transform engine — it operates
purely on text strings (source_before, source_after) that come back
from SmartInterpreter.apply_fix().
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Literal


# =============================================================================
# Unified diff — full-file preview
# =============================================================================

def unified_diff(before: str, after: str, filename: str = "<file>") -> str:
    """Generate a unified diff between two source strings.

    Args:
        before: original source
        after: patched source
        filename: label for the diff headers

    Returns:
        Unified diff text. Empty string if no changes.
    """
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    # Ensure both end with newline for clean diff output
    if before_lines and not before_lines[-1].endswith("\n"):
        before_lines[-1] += "\n"
    if after_lines and not after_lines[-1].endswith("\n"):
        after_lines[-1] += "\n"

    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    )
    return "".join(diff)


# =============================================================================
# Inline line diff — for per-violation display
# =============================================================================

@dataclass
class Segment:
    """A single segment of an inline diff line."""
    kind: Literal["equal", "remove", "add"]
    text: str


def line_segments(before: str, after: str) -> list[Segment]:
    """Word-level inline diff between two (possibly multi-line) strings.

    Returns a flat list of segments. For multi-line inputs, newlines are
    embedded in segment text — the caller decides how to wrap.

    Algorithm: difflib.SequenceMatcher on word tokens, then reconstruct.
    """
    if before == after:
        return [Segment(kind="equal", text=before)]

    # Tokenize: keep whitespace as separate tokens so word boundaries
    # are preserved in the diff
    def tokenize(s: str) -> list[str]:
        tokens = []
        cur = ""
        cur_kind = None  # 'word' or 'ws'
        for ch in s:
            if ch.isspace():
                kind = "ws"
            else:
                kind = "word"
            if kind != cur_kind and cur:
                tokens.append(cur)
                cur = ""
            cur_kind = kind
            cur += ch
        if cur:
            tokens.append(cur)
        return tokens

    before_tokens = tokenize(before)
    after_tokens = tokenize(after)

    matcher = difflib.SequenceMatcher(a=before_tokens, b=after_tokens, autojunk=False)
    segments: list[Segment] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            segments.append(Segment(
                kind="equal",
                text="".join(before_tokens[i1:i2]),
            ))
        elif tag == "replace":
            segments.append(Segment(
                kind="remove",
                text="".join(before_tokens[i1:i2]),
            ))
            segments.append(Segment(
                kind="add",
                text="".join(after_tokens[j1:j2]),
            ))
        elif tag == "delete":
            segments.append(Segment(
                kind="remove",
                text="".join(before_tokens[i1:i2]),
            ))
        elif tag == "insert":
            segments.append(Segment(
                kind="add",
                text="".join(after_tokens[j1:j2]),
            ))

    return segments


def segments_to_json(segments: list[Segment]) -> list[dict]:
    """Serialize segments to JSON-friendly list of dicts."""
    return [{"kind": s.kind, "text": s.text} for s in segments]


# =============================================================================
# Per-violation diff — for the Accept/Reject card
# =============================================================================

def violation_diff(source_before: str, source_after: str,
                   has_transform: bool) -> dict:
    """Build the diff representation for a single violation.

    Args:
        source_before: original source (full file before fix applied)
        source_after: source after this single fix applied (full file)
        has_transform: True if the rule has a Stage 4 transform; False if
                       the rule is informational only (e.g. deep_nesting)

    Returns:
        {
            "before": "source span text (the part that changed)",
            "after": "new source span text",
            "segments": [...]  # inline diff segments
            "has_transform": bool
        }

    Note: source_before/source_after here are FULL FILE strings. We
    extract just the changed region for display. If they're identical
    (no change), returns empty diff.
    """
    if not has_transform:
        return {
            "before": "",
            "after": "",
            "segments": [],
            "has_transform": False,
        }

    if source_before == source_after:
        return {
            "before": source_before,
            "after": source_after,
            "segments": [{"kind": "equal", "text": source_before}],
            "has_transform": True,
        }

    # Find the changed region — use difflib to get the first non-equal opcode
    # This is approximate; for display purposes it's fine
    matcher = difflib.SequenceMatcher(
        a=source_before.splitlines(),
        b=source_after.splitlines(),
        autojunk=False,
    )
    # Collect all changed line ranges
    before_changed = []
    after_changed = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before_changed.extend(source_before.splitlines()[i1:i2])
        after_changed.extend(source_after.splitlines()[j1:j2])

    before_text = "\n".join(before_changed) if before_changed else source_before
    after_text = "\n".join(after_changed) if after_changed else source_after

    return {
        "before": before_text,
        "after": after_text,
        "segments": segments_to_json(line_segments(before_text, after_text)),
        "has_transform": True,
    }
