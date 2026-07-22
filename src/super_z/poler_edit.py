from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Use the canonical POLER API (PolerAnalyzer from poler_enhanced.py v3.0).
# This is the original API introduced in POLER[n] v2.0 and extended in v3.0
# with semantic veins. The previous "POLEREnhanced" class was a misnamed
# stub that did not match the real POLER architecture.
try:
    from sandbox.poler_enhanced import PolerAnalyzer
except ImportError:
    _POLER_PATH = Path(__file__).resolve().parents[2] / "skills" / "_shared" / "sandbox" / "poler_enhanced.py"
    _POLER_SPEC = importlib.util.spec_from_file_location("super_z._poler_enhanced", _POLER_PATH)
    if _POLER_SPEC is None or _POLER_SPEC.loader is None:
        raise ImportError(f"PolerAnalyzer not found at {_POLER_PATH}")
    _POLER_MODULE = importlib.util.module_from_spec(_POLER_SPEC)
    _POLER_SPEC.loader.exec_module(_POLER_MODULE)
    PolerAnalyzer = _POLER_MODULE.PolerAnalyzer


class PolerEdit:
    """Central POLER router for literature, text, and source-code analysis.

    Wraps PolerAnalyzer (POLER v3.0) with mode detection and code diagnostics.
    For text inputs, uses build_veins() for semantic-vein analysis.
    For code inputs, additionally runs syntax checks via the appropriate
    interpreter (python3, node, ruby, perl, php).
    """

    def __init__(self, text: str = "", query: str = "", source: str = "") -> None:
        self.text = text or ""
        self.query = query or ""
        self.source = source or ""

    def analyze(self) -> dict[str, Any]:
        analyzer = PolerAnalyzer()
        keywords = [self.query] if self.query else None
        try:
            poler_v3 = analyzer.build_veins(
                self.text, keywords=keywords, source_file=self.source
            )
        except Exception as exc:  # noqa: BLE001 - keep resilient for CLI use
            poler_v3 = {"veins": [], "stats": {}, "error": str(exc)}

        veins = poler_v3.get("veins", []) if isinstance(poler_v3, dict) else []
        # Translate v3.0 veins into the compact fragment/summary/scores shape
        # that downstream consumers (SandboxAgentBackend) expect.
        fragments = [
            (v.get("cleaned_text") or v.get("raw_text") or v.get("text") or "")[:400]
            for v in veins[:5]
        ]
        summary = " | ".join(f for f in fragments[:3] if f) or (self.text[:400] if self.text else "")
        epsilons = [float(v.get("epsilon", 0.0)) for v in veins[:5]]
        resonances = [float(v.get("resonance", 0.0)) for v in veins[:5]]

        result: dict[str, Any] = {
            "fragments": fragments,
            "summary": summary[:800],
            "scores": resonances,
            "epsilon": epsilons,
            "resonance": resonances,
            "selected": veins[:5],
            "poler_v3": poler_v3,
            "mode": self.detect_mode(self.text, self.source),
        }

        if result["mode"] == "python":
            result["code_diagnostics"] = self._check_python()
        elif result["mode"] == "code":
            result["code_diagnostics"] = self._check_other_language()
        else:
            result["code_diagnostics"] = {"status": "not_applicable", "issues": []}
        return result

    def process(self, system_prompt: str = "", user_prompt: str = "", text: str = "") -> dict[str, Any]:
        """Process every request through one POLER pass."""
        content = "\n\n".join(
            part.strip() for part in (system_prompt, user_prompt, text) if part and part.strip()
        )
        return PolerEdit(content, user_prompt, source=self.source or "request").analyze()

    @staticmethod
    def detect_mode(text: str, source: str = "") -> str:
        suffix = Path(source).suffix.lower()
        if suffix == ".py" or (
            text.lstrip().startswith(("#!", "import ", "from "))
            and ("def " in text or "class " in text)
        ):
            return "python"
        if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rb", ".pl", ".php", ".go", ".rs", ".java", ".c", ".cpp", ".h"}:
            return "code"
        return "text"

    def _check_python(self) -> dict[str, Any]:
        try:
            ast.parse(self.text, filename=self.source or "<input>")
        except SyntaxError as error:
            return {
                "status": "error",
                "interpreter": shutil.which("python3") or "python",
                "issues": [{"line": error.lineno, "column": error.offset, "message": error.msg}],
            }
        return {"status": "ok", "interpreter": shutil.which("python3") or "python", "issues": []}

    def _check_other_language(self) -> dict[str, Any]:
        suffix = Path(self.source).suffix.lower()
        commands = {
            ".js": (["node", "--check"],), ".mjs": (["node", "--check"],), ".cjs": (["node", "--check"],),
            ".rb": (["ruby", "-c"],), ".pl": (["perl", "-c"],), ".php": (["php", "-l"],),
        }
        command_group = commands.get(suffix)
        if not command_group or not shutil.which(command_group[0][0]):
            return {"status": "unavailable", "interpreter": None, "issues": [], "message": "Syntax interpreter is not installed"}
        with tempfile.NamedTemporaryFile("w", suffix=suffix, encoding="utf-8", delete=False) as handle:
            handle.write(self.text)
            path = handle.name
        try:
            command = [*command_group[0], path]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
            output = (completed.stderr or completed.stdout).strip()
            return {"status": "ok" if completed.returncode == 0 else "error", "interpreter": command_group[0][0], "issues": [], "message": output}
        finally:
            Path(path).unlink(missing_ok=True)
