#!/usr/bin/env python3
"""
executor.py — Runs a skill via subprocess and validates its output.

Used by the Orchestrator. Takes a skill name and input data, looks up
the skill's entry point in the manifest, runs it as a subprocess
(JSON over stdin/stdout), and validates the output against the skill's
declared schema using its validator.py.

Usage (as a module):
    from executor import Executor
    from registry import SkillRegistry
    reg = SkillRegistry(Path(__file__).resolve().parents[2] / "skills")
    ex = Executor(reg)
    result = ex.run("poler-toolkit", {"input": "notes.md", "json": True})

CLI:
    python3 executor.py SKILL_NAME --input '{"input": "x.pdf"}'
    python3 executor.py poler-toolkit --input-file /path/to/data.json

Author: Task 9 (manifest-based architecture), 2026-07-03
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent.parent / "_shared"))

from registry import SkillRegistry  # noqa: E402

# v2.0 — try to import the new schema + runtime learning, but degrade gracefully
try:
    from skill_schema import SkillOutput, validate_output  # noqa: E402
    _HAS_SCHEMA = True
except ImportError:
    _HAS_SCHEMA = False

try:
    from runtime_learning import RuntimeLearning  # noqa: E402
    _TRACKER = RuntimeLearning()
except Exception:
    _TRACKER = None


class Executor:
    """Runs a skill and validates its output.

    v2.0 enhancements:
    - Normalizes every output to SkillOutput schema (skill_schema.py)
    - Logs invocations to runtime_learning.db for planner weight updates
    - Adds confidence + skill_name to output envelope
    """

    def __init__(self, registry: SkillRegistry, verbose: bool = False):
        self.registry = registry
        self.verbose = verbose

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def run(self, skill_name: str, input_data: Dict[str, Any],
            timeout: int = 300) -> Dict[str, Any]:
        """Execute a skill with the given input data.

        Args:
            skill_name: skill name (must be in registry)
            input_data: dict to pass as JSON to the skill's stdin
            timeout: max seconds before killing the subprocess

        Returns:
            The skill's output envelope {status, confidence, data, ...}.
            On error, returns {status: "error", confidence: 0.0, error: "..."}.

        Raises:
            KeyError: skill not in registry
            FileNotFoundError: skill's entry point script not found
        """
        manifest = self.registry.get_manifest(skill_name)
        if not manifest:
            raise KeyError(f"Skill '{skill_name}' not in registry")

        # Find entry point — try common names
        entry_point = self._find_entry_point(skill_name, manifest)
        if not entry_point or not entry_point.exists():
            raise FileNotFoundError(
                f"No entry point script found for skill '{skill_name}'. "
                f"Looked in scripts/ for ingest.py, main.py, run.py, {skill_name}.py"
            )

        if self.verbose:
            sys.stderr.write(
                f"[executor] running {skill_name} → {entry_point}\n"
            )

        # Build the command line args based on skill conventions
        cmd_args = self._build_cmd_args(skill_name, manifest, input_data)
        cmd = ["python3", str(entry_point)] + cmd_args

        # Pass input via stdin (for "-" args) and via CLI flags
        stdin_data = input_data.get("_stdin") if isinstance(input_data, dict) else None

        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=timeout,
                # Don't change cwd — keep relative paths working as user expects
            )
        except subprocess.TimeoutExpired:
            return self._error_envelope(
                f"Skill '{skill_name}' timed out after {timeout}s"
            )
        except Exception as e:
            return self._error_envelope(f"Skill '{skill_name}' crashed: {e}")

        elapsed = time.time() - t0

        if proc.returncode != 0:
            return self._error_envelope(
                f"Skill '{skill_name}' exit {proc.returncode}: "
                f"{proc.stderr[:500]}"
            )

        # Parse stdout as JSON
        try:
            output = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            return self._error_envelope(
                f"Skill '{skill_name}' did not emit valid JSON: {e}. "
                f"stdout[:200]={proc.stdout[:200]!r}"
            )

        # Add execution metadata
        if isinstance(output, dict):
            output.setdefault("_execution", {})["elapsed_sec"] = round(elapsed, 3)
            output["_execution"]["skill"] = skill_name
            output["_execution"]["entry_point"] = str(entry_point)

        # Validate output via the skill's validator.py
        validation = self._validate(skill_name, output)
        output["_validation"] = validation

        # v2.0: normalize to SkillOutput schema
        if _HAS_SCHEMA:
            try:
                norm = SkillOutput.from_dict(output) if isinstance(output, dict) else SkillOutput.error("non-dict output")
                norm.skill_name = skill_name
                # carry over execution metadata
                norm.metrics.setdefault("elapsed_sec", round(elapsed, 3))
                output = norm.to_dict()
                output["_execution"] = {
                    "elapsed_sec": round(elapsed, 3),
                    "skill": skill_name,
                    "entry_point": str(entry_point),
                }
                output["_validation"] = validation
            except Exception as e:
                if self.verbose:
                    sys.stderr.write(f"[executor] schema normalization failed: {e}\n")

        # v2.0: log to runtime learning
        if _TRACKER:
            try:
                status = output.get("status", "ok") if isinstance(output, dict) else "error"
                conf = float(output.get("confidence", 0.0)) if isinstance(output, dict) else 0.0
                # derive capability from manifest if available
                cap = ""
                caps = manifest.get("capabilities") if manifest else None
                if caps and isinstance(caps, list) and caps:
                    cap = caps[0].get("name", "") if isinstance(caps[0], dict) else str(caps[0])
                _TRACKER.log(
                    skill=skill_name,
                    capability=cap,
                    query=str(input_data.get("input", ""))[:200],
                    status=status,
                    confidence=conf,
                    duration_ms=int(elapsed * 1000),
                )
            except Exception:
                pass

        if self.verbose:
            sys.stderr.write(
                f"[executor] {skill_name} done in {elapsed:.2f}s, "
                f"validation: {validation['status']}\n"
            )

        return output

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _find_entry_point(self, skill_name: str,
                          manifest: Dict[str, Any]) -> Optional[Path]:
        """Find the entry point script for a skill."""
        # 1. Try manifest's entry_points (in priority order)
        entry_points = manifest.get("entry_points", {})
        # Priority order of entry names
        priority_names = ["ingest", "extract", "analyze", "render",
                          "create", "build", "chat", "search", "cli", "main"]
        for name in priority_names:
            if name in entry_points:
                p = self.registry.skills_dir / skill_name / entry_points[name]
                if p.exists():
                    return p
        # 2. Try common script names in scripts/
        scripts_dir = self.registry.skills_dir / skill_name / "scripts"
        if scripts_dir.exists():
            for candidate in ["ingest.py", "main.py", "run.py",
                              f"{skill_name}.py", "ocr_pdf.py"]:
                p = scripts_dir / candidate
                if p.exists():
                    return p
            # 3. Take any .py in scripts/
            py_files = sorted(scripts_dir.glob("*.py"))
            if py_files:
                # Filter out helpers (validator, doctor, _meta, etc.)
                main_candidates = [
                    f for f in py_files
                    if f.stem not in ("validator", "doctor", "__init__",
                                      "topic_common", "topic_local", "topic_llm",
                                      "z_ai_api", "lens_query", "registry",
                                      "planner", "executor", "aggregator",
                                      "orchestrator")
                ]
                if main_candidates:
                    return main_candidates[0]
                return py_files[0]
        return None

    def _build_cmd_args(self, skill_name: str, manifest: Dict[str, Any],
                        input_data: Dict[str, Any]) -> list:
        """Build CLI args for the skill's entry point.

        Convention:
          - poler-toolkit/ingest.py: <input> --json
          - pdf-ocr/ocr_pdf.py: <input> --json
          - Other skills: try --json flag + --input <path>
        """
        args: list = []
        # Get the input value (path or "-" for stdin)
        input_value = input_data.get("input") if isinstance(input_data, dict) else None

        if input_value:
            args.append(str(input_value))

        # Add --json flag (most skills support it)
        if "json" in input_data and input_data["json"]:
            args.append("--json")
        else:
            # Default to --json for orchestrator mode
            args.append("--json")

        # Pass-through other known flags
        for flag in ("--llm", "--no-text", "--no-clusters", "--no-keywords",
                     "--verbose", "-v", "--force-ocr"):
            key = flag.lstrip("-").replace("-", "_")
            if input_data.get(key):
                args.append(flag)

        # Numeric args
        for arg_name, cli_name in [("max_pages", "--max-pages"),
                                   ("max_chars", "--max-chars")]:
            val = input_data.get(arg_name)
            if isinstance(val, int):
                args.extend([cli_name, str(val)])

        return args

    def _validate(self, skill_name: str,
                  output: Dict[str, Any]) -> Dict[str, Any]:
        """Run the skill's validator.py on the output."""
        validator_path = self.registry.get_validator_path(skill_name)
        if not validator_path or not validator_path.exists():
            return {
                "status": "skipped",
                "message": f"No validator.py for {skill_name}",
            }

        try:
            # Import the validator module dynamically
            spec = importlib.util.spec_from_file_location(
                f"{skill_name}_validator", validator_path
            )
            if not spec or not spec.loader:
                return {"status": "error", "message": "Could not load validator module"}
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "validate"):
                return {
                    "status": "skipped",
                    "message": f"{validator_path.name} has no validate() function",
                }

            ok, msg = module.validate(output)
            return {
                "status": "valid" if ok else "invalid",
                "message": msg,
                "validator": str(validator_path.relative_to(
                    self.registry.skills_dir)),
            }
        except (ValueError, TypeError) as e:
            # validator returned non-tuple or wrong-length tuple
            return {
                "status": "skipped",
                "message": f"validator returned non-unpackable result: {e}",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"validator crashed: {e}",
            }

    def _error_envelope(self, msg: str) -> Dict[str, Any]:
        return {
            "status": "error",
            "confidence": 0.0,
            "data": None,
            "error": msg,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Execute a single skill")
    ap.add_argument("skill", help="Skill name (e.g., poler-toolkit)")
    ap.add_argument("--input", help="JSON string with input data")
    ap.add_argument("--input-file", help="Path to JSON file with input data")
    ap.add_argument("--skills-dir", default=str(Path(__file__).resolve().parents[2] / "skills"))
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    if args.input_file:
        input_data = json.loads(Path(args.input_file).read_text(encoding="utf-8"))
    elif args.input:
        input_data = json.loads(args.input)
    else:
        # Default: read JSON from stdin
        input_data = json.loads(sys.stdin.read())

    reg = SkillRegistry(args.skills_dir)
    ex = Executor(reg, verbose=args.verbose)
    result = ex.run(args.skill, input_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # Return 0 on success, 1 on error
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
