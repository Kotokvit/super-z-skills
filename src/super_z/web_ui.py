"""
web_ui.py — Accept/Reject UI for the smart interpreter (Stage 5).

A Flask app that lets a user:
  1. Paste or upload source code (Python or JavaScript)
  2. Run the analyzer — gets back violations (Stage 1-3)
  3. Preview each violation's diff (Stage 4: apply_fix in isolation)
  4. Accept or reject each fix individually, or in batch
  5. Download the patched source (Stage 4: apply_all_fixes)

Endpoints:
  GET  /                  — main UI (single-page app)
  POST /api/analyze       — analyze source, return violations + per-violation diffs
  POST /api/apply         — apply accepted fixes, return patched source
  POST /api/diff          — preview full-file diff for a set of decisions
  GET  /api/health        — health check
  GET  /api/sample/<name> — built-in samples (bad_python, bad_js)

Architecture:
  - The UI is vanilla HTML+JS (no build step) — see templates/accept_reject.html
  - Uses Stage 4 SmartInterpreter.apply_fix() for per-violation preview
    (creates a fresh SmartInterpreter for each preview to avoid state pollution)
  - Uses Stage 4 SmartInterpreter.apply_all_fixes() for batch apply
  - Per-violation diff computed via diff_engine.violation_diff()

Run:
  python -m super_z.web_ui
  # open http://localhost:5000
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

from flask import Flask, request, jsonify, render_template

from .poler_smart_interpreter import SmartInterpreter, analyze_code
from .smart_rules.diff_engine import unified_diff, violation_diff


app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

# In-memory session — one analysis at a time. Fine for a single-user CLI tool.
# For multi-user, replace with a session store keyed by cookie.
_SESSION: dict[str, Any] = {}


# =============================================================================
# Routes
# =============================================================================

@app.route("/")
def index() -> str:
    """Main UI page."""
    return render_template("accept_reject.html")


@app.route("/api/analyze", methods=["POST"])
def api_analyze() -> Any:
    """Analyze source code and return violations with per-violation diffs.

    Request JSON:
        {
            "source": "...",
            "filename": "example.py",
            "language": "python"  # or "javascript"
        }

    Response JSON:
        {
            "ok": true,
            "filename": "example.py",
            "language": "python",
            "summary": {...},
            "violations": [{
                "id": "v0",
                "rule_id": "R001",
                "severity": "CRITICAL",
                "why": "...",
                "fix_template": "ast.literal_eval({{arg0}})  # safe",
                "manual_review": "...",
                "has_transform": true,
                "line": 12, "col": 0, "end_line": 12, "end_col": 19,
                "source_snippet": "eval(user_input)",
                "diff": {"before": "...", "after": "...", "segments": [...]},
                "poler": {...} | null
            }, ...],
            "rules_meta": {...}
        }

    Per-violation diff strategy:
        For each violation that has a Stage 4 transformation, create a
        FRESH SmartInterpreter (so apply_fix doesn't pollute state for
        the next preview) and call apply_fix once. The result's
        source_before/source_after are passed to violation_diff().
    """
    try:
        data = request.get_json(force=True)
        source = data.get("source", "")
        filename = data.get("filename", "<code>")
        language = data.get("language", "python")

        if not source.strip():
            return jsonify({"ok": False, "error": "Source is empty"}), 400

        # Run the analyzer — language selects the adapter
        report = analyze_code(source, filename=filename, language=language)

        if not report["syntax_ok"]:
            return jsonify({
                "ok": False,
                "error": "Syntax error in source",
                "syntax_error": report["syntax_error"],
            }), 400

        # Assign unique IDs and strip non-serializable fields (_node, transformation)
        violations = report["violations"]
        clean_violations = []
        server_violations = {}  # id -> full violation dict (with _node, transformation)

        for i, v in enumerate(violations):
            vid = f"v{i}"
            v["id"] = vid
            server_violations[vid] = v  # keep full version server-side

            has_transform = v.get("transformation") is not None

            # Compute per-violation diff if there's a transform
            diff_payload = None
            if has_transform:
                try:
                    # Fresh interpreter for each preview — apply_fix mutates state
                    preview_interp = SmartInterpreter(
                        source, filename=filename, language=language
                    )
                    # Find the matching violation in the fresh interpreter's run
                    fresh_violations = preview_interp.find_violations()
                    # Match by rule_id + line + col (positional identity doesn't
                    # carry across interpreter instances)
                    match = None
                    for fv in fresh_violations:
                        if (fv.get("rule_id") == v["rule_id"]
                                and fv.get("line") == v["line"]
                                and fv.get("col") == v["col"]):
                            match = fv
                            break
                    if match is not None:
                        result = preview_interp.apply_fix(match)
                        diff_payload = violation_diff(
                            source_before=result["source_before"],
                            source_after=result["source_after"],
                            has_transform=True,
                        )
                    else:
                        diff_payload = {
                            "before": v.get("source_snippet", ""),
                            "after": v.get("fix", ""),
                            "segments": [],
                            "has_transform": True,
                            "error": "preview match not found",
                        }
                except Exception as e:
                    diff_payload = {
                        "before": v.get("source_snippet", ""),
                        "after": v.get("fix", ""),
                        "segments": [],
                        "has_transform": True,
                        "error": f"preview failed: {e}",
                    }
            else:
                diff_payload = {
                    "before": v.get("source_snippet", ""),
                    "after": "",
                    "segments": [],
                    "has_transform": False,
                }

            # Build the clean violation dict for the UI
            clean_violations.append({
                "id": vid,
                "rule_id": v["rule_id"],
                "rule_name": v["rule_name"],
                "severity": v["severity"],
                "category": v["category"],
                "why": v["why"],
                "fix_template": v["fix"],
                "manual_review": v["manual_review"],
                "has_transform": has_transform,
                "line": v["line"],
                "col": v["col"],
                "end_line": v["end_line"],
                "end_col": v["end_col"],
                "source_snippet": v["source_snippet"],
                "ast_node_type": v["ast_node_type"],
                "diff": diff_payload,
                "poler": v.get("poler"),
            })

        # Save session for /api/apply and /api/diff
        _SESSION["source"] = source
        _SESSION["filename"] = filename
        _SESSION["language"] = language
        _SESSION["violations"] = server_violations  # id -> full violation dict

        return jsonify({
            "ok": True,
            "filename": filename,
            "language": language,
            "summary": report["summary"],
            "violations": clean_violations,
            "rules_meta": report.get("rules_meta"),
            "rules_warnings": report.get("rules_warnings", []),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/apply", methods=["POST"])
def api_apply() -> Any:
    """Apply accepted fixes and return the patched source.

    Request JSON:
        {
            "decisions": {"v0": "accept", "v1": "reject", ...}
        }

    Response JSON:
        {
            "ok": true,
            "patched_source": "...",
            "applied_count": 5,
            "rejected_count": 7,
            "failed_count": 1,
            "total_count": 13,
            "diff": "..."  # unified diff between original and patched
        }

    Strategy:
        Create a fresh SmartInterpreter, filter to accepted violations,
        call apply_all_fixes(). Stage 4 handles reverse-order application
        and per-violation error reporting.
    """
    try:
        if "source" not in _SESSION:
            return jsonify({"ok": False, "error": "No active analysis. Run /api/analyze first."}), 400

        data = request.get_json(force=True)
        decisions = data.get("decisions", {})

        source = _SESSION["source"]
        filename = _SESSION["filename"]
        language = _SESSION["language"]
        server_violations = _SESSION["violations"]

        # Build the list of accepted violations (with their _node + transformation)
        accepted = []
        rejected_count = 0
        for vid, decision in decisions.items():
            if decision == "accept":
                v = server_violations.get(vid)
                if v is not None:
                    accepted.append(v)
            else:
                rejected_count += 1

        # Create a fresh interpreter — apply_all_fixes mutates self.tree/source
        interp = SmartInterpreter(source, filename=filename, language=language)
        # Re-find violations in the fresh interpreter — the _node references
        # in the session are from the original interpreter's tree, which the
        # fresh interpreter doesn't have. We need to match by position.
        fresh_violations = interp.find_violations()
        matched = []
        for accepted_v in accepted:
            for fv in fresh_violations:
                if (fv.get("rule_id") == accepted_v["rule_id"]
                        and fv.get("line") == accepted_v["line"]
                        and fv.get("col") == accepted_v["col"]):
                    matched.append(fv)
                    break

        result = interp.apply_all_fixes(matched)
        patched = result["final_source"]

        diff_text = unified_diff(source, patched, filename)

        return jsonify({
            "ok": True,
            "patched_source": patched,
            "applied_count": result["applied"],
            "failed_count": result["failed"],
            "rejected_count": rejected_count,
            "total_count": result["total"] + rejected_count,
            "diff": diff_text,
            "filename": filename,
            "results": [
                {
                    "rule_id": r.get("rule_id"),
                    "line": r.get("line"),
                    "success": r.get("success"),
                    "message": r.get("message"),
                    "action": r.get("action"),
                }
                for r in result.get("results", [])
            ],
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/diff", methods=["POST"])
def api_diff() -> Any:
    """Preview the diff WITHOUT applying — useful for "preview accept-all".

    Same request/response shape as /api/apply, but doesn't mutate session.
    """
    try:
        if "source" not in _SESSION:
            return jsonify({"ok": False, "error": "No active analysis."}), 400

        data = request.get_json(force=True)
        decisions = data.get("decisions", {})

        source = _SESSION["source"]
        filename = _SESSION["filename"]
        language = _SESSION["language"]
        server_violations = _SESSION["violations"]

        accepted = []
        for vid, decision in decisions.items():
            if decision == "accept":
                v = server_violations.get(vid)
                if v is not None:
                    accepted.append(v)

        interp = SmartInterpreter(source, filename=filename, language=language)
        fresh_violations = interp.find_violations()
        matched = []
        for accepted_v in accepted:
            for fv in fresh_violations:
                if (fv.get("rule_id") == accepted_v["rule_id"]
                        and fv.get("line") == accepted_v["line"]
                        and fv.get("col") == accepted_v["col"]):
                    matched.append(fv)
                    break

        result = interp.apply_all_fixes(matched)
        patched = result["final_source"]
        diff_text = unified_diff(source, patched, filename)

        return jsonify({
            "ok": True,
            "diff": diff_text,
            "applied_count": result["applied"],
            "failed_count": result["failed"],
            "total_count": result["total"],
            "filename": filename,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/health")
def api_health() -> Any:
    """Health check."""
    return jsonify({"ok": True, "has_session": "source" in _SESSION})


# =============================================================================
# Sample loader — convenience for local testing
# =============================================================================

@app.route("/api/sample/<name>")
def api_sample(name: str) -> Any:
    """Return a built-in sample source for quick UI testing.

    Available samples:
      - bad_python — collection of Python rule violations
      - bad_js     — collection of JS rule violations
    """
    samples = {
        "bad_python": BAD_PYTHON_SAMPLE,
        "bad_js": BAD_JS_SAMPLE,
    }
    if name not in samples:
        return jsonify({"ok": False, "error": f"Unknown sample: {name}"}), 404
    return jsonify({
        "ok": True,
        "source": samples[name],
        "language": "python" if "python" in name else "javascript",
    })


# =============================================================================
# Built-in samples — for quick local testing without finding a file
# =============================================================================

BAD_PYTHON_SAMPLE = '''"""Sample with multiple violations for testing the Accept/Reject UI."""
import subprocess
import pickle

def danger(user_input):
    # R001 — eval with user input (has Stage 4 transform)
    result = eval(user_input)
    return result


def swallow_error():
    # R006 — bare except (has Stage 4 transform)
    try:
        do_something()
    except:
        pass
'''

BAD_JS_SAMPLE = '''// Sample with JS violations for testing the Accept/Reject UI.

function danger(userInput) {
    // R001 / J-eval — eval with user input (has Stage 4 transform)
    eval(userInput);

    // J002 — innerHTML XSS (has Stage 4 transform)
    document.body.innerHTML = userInput;

    // Safe — should not trigger
    console.log(userInput);
    document.body.textContent = userInput;
}
'''


# =============================================================================
# CLI entry point
# =============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Smart interpreter Accept/Reject UI (Stage 5)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Port (default 5000)")
    parser.add_argument("--debug", action="store_true", help="Flask debug mode")
    args = parser.parse_args()

    print(f"\nSmart Interpreter — Accept/Reject UI (Stage 5)")
    print(f"==============================================")
    print(f"Open in browser:  http://localhost:{args.port}")
    print(f"Sample (Python):  http://localhost:{args.port}/api/sample/bad_python")
    print(f"Sample (JS):      http://localhost:{args.port}/api/sample/bad_js")
    print(f"\nPress Ctrl+C to stop.\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
