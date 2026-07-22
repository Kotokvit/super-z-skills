from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
import importlib.util
from typing import Any

from pathlib import Path

try:
    from sandbox.poler_enhanced import POLEREnhanced
except ImportError:
    _POLER_PATH = Path(__file__).resolve().parents[2] / "skills" / "_shared" / "sandbox" / "poler_enhanced.py"
    _POLER_SPEC = importlib.util.spec_from_file_location("super_z._poler_enhanced", _POLER_PATH)
    if _POLER_SPEC is None or _POLER_SPEC.loader is None:
        raise ImportError(f"POLEREnhanced not found at {_POLER_PATH}")
    _POLER_MODULE = importlib.util.module_from_spec(_POLER_SPEC)
    _POLER_SPEC.loader.exec_module(_POLER_MODULE)
    POLEREnhanced = _POLER_MODULE.POLEREnhanced


class PolerEdit:
    """Central POLER router for literature, text, and source-code analysis."""

    def __init__(self, text: str = "", query: str = "", source: str = "") -> None:
        self.text = text or ""
        self.query = query or ""
        self.source = source or ""

    def analyze(self) -> dict[str, Any]:
        result = POLEREnhanced(self.text, self.query, source=self.source).analyze()
        result["mode"] = self.detect_mode(self.text, self.source)
        if result["mode"] == "python":
            result["code_diagnostics"] = self._check_python()
        elif result["mode"] == "code":
            result["code_diagnostics"] = self._check_other_language()
        else:
            result["code_diagnostics"] = {"status": "not_applicable", "issues": []}
        return result

    def process(self, system_prompt: str = "", user_prompt: str = "", text: str = "") -> dict[str, Any]:
        """Process every request through one POLER pass."""
        content = "\n\n".join(part.strip() for part in (system_prompt, user_prompt, text) if part and part.strip())
        return PolerEdit(content, user_prompt, source=self.source or "request").analyze()

    @staticmethod
    def detect_mode(text: str, source: str = "") -> str:
        suffix = Path(source).suffix.lower()
        if suffix == ".py" or text.lstrip().startswith(("#!", "import ", "from ")) and ("def " in text or "class " in text):
            return "python"
        if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rb", ".pl", ".php", ".go", ".rs", ".java", ".c", ".cpp", ".h"}:
            return "code"
        return "text"

    def _check_python(self) -> dict[str, Any]:
        try:
            ast.parse(self.text, filename=self.source or "<input>")
        except SyntaxError as error:
            return {"status": "error", "interpreter": shutil.which("python3") or "python", "issues": [{"line": error.lineno, "column": error.offset, "message": error.msg}]}
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
