from __future__ import annotations

import os
import shutil
import subprocess
from .base import LLMBackend


class ZaiCliBackend(LLMBackend):
    name = "zai_cli"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or os.getenv("SUPER_Z_ZAI_CLI") or shutil.which("z-ai") or "/usr/local/bin/z-ai"

    def chat(self, system_prompt: str, user_prompt: str, timeout: int = 120, **kwargs) -> str:
        if not self._is_available():
            raise RuntimeError("z-ai CLI is not available")
        cmd = [self.executable, "chat", "--prompt", user_prompt, "--system", system_prompt]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "z-ai CLI failed")
        output = proc.stdout.strip()
        if not output:
            return ""
        start = output.find("{")
        if start >= 0:
            try:
                payload = __import__("json").loads(output[start:])
            except Exception:
                return output
            if isinstance(payload, dict) and "choices" in payload:
                content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return output

    def health_check(self) -> bool:
        return self._is_available()

    def _is_available(self) -> bool:
        return bool(self.executable and shutil.which(self.executable) is not None) or os.path.exists(self.executable)
