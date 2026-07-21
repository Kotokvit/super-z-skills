#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
super_z_bridge.py — Bridge between AI's Native Tools and LocalExecutor
======================================================================

THE PROBLEM:
    When Super Z Core runs inside an AI (GLM, Claude, GPT), the AI already
    has its OWN tools: Bash, Read, Write, etc. But super_z_core.py doesn't
    know how to use them — it only knows about Python functions and CLI.

THE SOLUTION:
    This bridge ADAPTS the AI's native tool interface to the LocalExecutor
    interface. When super_z_core wants to:
    - Read a file → Bridge calls AI's Read tool
    - Write a file → Bridge calls AI's Write tool
    - Run a command → Bridge calls AI's Bash tool
    - Analyze text with POLER → Bridge imports poler_enhanced directly

    The bridge makes the AI's native tools available to the core WITHOUT
    going through CLI. Everything stays in-process. Everything is FREE.

ARCHITECTURE:
    ┌─────────────┐     ┌────────────────┐     ┌──────────────┐
    │ SuperZCore  │────>│  super_z_bridge │────>│ AI's native  │
    │ (routing)   │     │  (adapter)      │     │ tools        │
    └─────────────┘     └────────────────┘     │ (Bash/Read/  │
                            │    │              │  Write)      │
                            │    │              └──────────────┘
                            v    v
                    ┌──────────┐ ┌───────────────┐
                    │ poler_   │ │ Python        │
                    │ enhanced │ │ subprocess    │
                    └──────────┘ └───────────────┘

USAGE (inside AI):
    # The AI sets up the bridge before calling super_z_core
    from super_z_bridge import AIBridge

    bridge = AIBridge(
        bash_tool=my_bash_function,
        read_tool=my_read_function,
        write_tool=my_write_function,
    )

    # Now super_z_core can use the AI's tools
    result = bridge.execute_skill("poler-psi", "Analyze this text")

USAGE (standalone, no AI):
    # Falls back to Python subprocess and file I/O
    bridge = AIBridge()
    result = bridge.execute_skill("poler-psi", "Analyze this text")

Author: Super-Z team + Qwen Coder, 2026-07-21
Version: 1.0.0
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


__version__ = "1.0.0"
__author__ = "Super-Z team + Qwen Coder"


# ═══════════════════════════════════════════════════════════════════════════
# Tool Protocol — defines the interface for each native tool
# ═══════════════════════════════════════════════════════════════════════════

class ToolProtocol:
    """Base protocol for AI native tools.

    Each tool is a callable that takes specific parameters and returns
    a result string. The bridge wraps these callables to provide a
    uniform interface for LocalExecutor.
    """

    @staticmethod
    def bash_default(command: str, timeout: int = 60) -> str:
        """Default Bash implementation using Python subprocess.

        This is used when no AI-provided Bash tool is available.

        Args:
            command: Shell command to execute.
            timeout: Timeout in seconds.

        Returns:
            Command output (stdout + stderr combined).
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr] {result.stderr}"
            return output.strip() if output.strip() else "(no output)"
        except subprocess.TimeoutExpired:
            return f"[ERROR] Command timed out after {timeout}s"
        except Exception as e:
            return f"[ERROR] {e}"

    @staticmethod
    def read_default(filepath: str) -> str:
        """Default Read implementation using Python file I/O.

        Args:
            filepath: Path to the file to read.

        Returns:
            File contents as string.
        """
        try:
            path = Path(filepath)
            if not path.exists():
                return f"[ERROR] File not found: {filepath}"
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"[ERROR] {e}"

    @staticmethod
    def write_default(filepath: str, content: str) -> str:
        """Default Write implementation using Python file I/O.

        Args:
            filepath: Path to the file to write.
            content: Content to write.

        Returns:
            Status message.
        """
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"OK: wrote {len(content)} chars to {filepath}"
        except Exception as e:
            return f"[ERROR] {e}"


# ═══════════════════════════════════════════════════════════════════════════
# AIBridge — the main adapter
# ═══════════════════════════════════════════════════════════════════════════

class AIBridge:
    """Bridge between SuperZCore and AI's native tools.

    This is the KEY component that makes Local-First routing work
    inside an AI platform. Without it, super_z_core would fall back
    to CLI for everything.

    The bridge provides three capabilities:
    1. Direct POLER execution (import poler_enhanced, call it as library)
    2. AI tool delegation (forward Read/Write/Bash to AI's own tools)
    3. Python fallback (subprocess/file I/O when no AI tools available)

    Usage:
        # Inside an AI with native tools
        bridge = AIBridge(
            bash_tool=ai_bash,    # AI's Bash function
            read_tool=ai_read,    # AI's Read function
            write_tool=ai_write,  # AI's Write function
        )

        # Standalone (no AI tools)
        bridge = AIBridge()

        # Execute a skill through the bridge
        result = bridge.execute_skill("poler-psi", text, skill_md)
    """

    def __init__(
        self,
        bash_tool: Optional[Callable[[str], str]] = None,
        read_tool: Optional[Callable[[str], str]] = None,
        write_tool: Optional[Callable[[str, str], str]] = None,
        verbose: bool = False,
    ):
        """Initialize the bridge.

        Args:
            bash_tool: AI's Bash function (command: str) -> output: str.
            read_tool: AI's Read function (filepath: str) -> content: str.
            write_tool: AI's Write function (filepath: str, content: str) -> status: str.
            verbose: Print debug information.
        """
        # Use AI's tools if provided, otherwise use Python defaults
        self._bash = bash_tool or ToolProtocol.bash_default
        self._read = read_tool or ToolProtocol.read_default
        self._write = write_tool or ToolProtocol.write_default
        self._verbose = verbose

        # Track what tools are available
        self._has_ai_tools = any([
            bash_tool is not None,
            read_tool is not None,
            write_tool is not None,
        ])

        # POLER analyzer (lazy init)
        self._poler_analyzer = None

        # Stats
        self._stats = {
            "poler_calls": 0,
            "bash_calls": 0,
            "read_calls": 0,
            "write_calls": 0,
            "ai_tool_calls": 0,
            "python_fallback_calls": 0,
        }

    @property
    def has_ai_tools(self) -> bool:
        """Whether AI native tools are available."""
        return self._has_ai_tools

    # ─── Tool Methods ─────────────────────────────────────────────────────

    def bash(self, command: str, timeout: int = 60) -> str:
        """Execute a shell command through the AI's Bash tool.

        Uses the AI's native Bash if available (FREE), otherwise
        falls back to Python subprocess.

        Args:
            command: Shell command to execute.
            timeout: Timeout in seconds.

        Returns:
            Command output string.
        """
        self._stats["bash_calls"] += 1
        if self._has_ai_tools and self._bash != ToolProtocol.bash_default:
            self._stats["ai_tool_calls"] += 1
            if self._verbose:
                print(f"[bridge] Bash (AI tool): {command[:80]}...",
                      file=sys.stderr)
        else:
            self._stats["python_fallback_calls"] += 1
            if self._verbose:
                print(f"[bridge] Bash (Python): {command[:80]}...",
                      file=sys.stderr)

        return self._bash(command) if not hasattr(self._bash, '__code__') or \
            self._bash.__code__.co_argcount == 1 else \
            self._bash(command, timeout)

    def read(self, filepath: str) -> str:
        """Read a file through the AI's Read tool.

        Args:
            filepath: Path to the file.

        Returns:
            File contents string.
        """
        self._stats["read_calls"] += 1
        if self._verbose:
            print(f"[bridge] Read: {filepath}", file=sys.stderr)
        return self._read(filepath)

    def write(self, filepath: str, content: str) -> str:
        """Write a file through the AI's Write tool.

        Args:
            filepath: Path to the file.
            content: Content to write.

        Returns:
            Status message string.
        """
        self._stats["write_calls"] += 1
        if self._verbose:
            print(f"[bridge] Write: {filepath} ({len(content)} chars)",
                  file=sys.stderr)
        return self._write(filepath, content)

    # ─── POLER Direct Execution ───────────────────────────────────────────

    def execute_poler(
        self,
        text: str,
        query: str = "",
        options: Optional[Dict] = None,
    ) -> str:
        """Execute POLER analysis directly as a Python library.

        This is the FAST PATH. No CLI, no LLM, no API call.
        Just import poler_enhanced and call it. FREE.

        The POLER engine does text resonance analysis:
        - Pattern detection (PII, structure, format)
        - Keyword extraction and frequency
        - Sentiment indicators
        - Cross-document resonance (if multiple texts)

        Args:
            text: The text to analyze.
            query: Optional query to focus the analysis.
            options: Optional analysis options.

        Returns:
            JSON string with analysis results.
        """
        self._stats["poler_calls"] += 1

        if self._verbose:
            print(f"[bridge] POLER analysis: {len(text)} chars, "
                  f"query='{query[:50]}'", file=sys.stderr)

        try:
            # Import POLER directly as a Python library
            analyzer = self._get_poler_analyzer()
            if analyzer is not None:
                result = analyzer.analyze_text(text, query)
                return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            if self._verbose:
                print(f"[bridge] POLER error: {e}", file=sys.stderr)

        # Fallback: basic text analysis without POLER
        return self._basic_text_analysis(text, query)

    def _get_poler_analyzer(self):
        """Get or create POLER analyzer (lazy init)."""
        if self._poler_analyzer is not None:
            return self._poler_analyzer

        try:
            # Try direct import from the same directory
            from poler_enhanced import PolerAnalyzer
            self._poler_analyzer = PolerAnalyzer()
            return self._poler_analyzer
        except ImportError:
            pass

        try:
            # Try with explicit path
            sandbox_dir = Path(__file__).resolve().parent
            if str(sandbox_dir) not in sys.path:
                sys.path.insert(0, str(sandbox_dir))
            from poler_enhanced import PolerAnalyzer
            self._poler_analyzer = PolerAnalyzer()
            return self._poler_analyzer
        except ImportError:
            pass

        if self._verbose:
            print("[bridge] POLER not available, using basic analysis",
                  file=sys.stderr)
        return None

    def _basic_text_analysis(self, text: str, query: str) -> str:
        """Basic text analysis when POLER is not available.

        This is a lightweight fallback that provides useful information
        without the full POLER engine.
        """
        import re
        from collections import Counter

        # Basic statistics
        words = re.findall(r'\w+', text.lower())
        word_freq = Counter(words).most_common(20)

        # Sentence detection
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        # Structure detection
        has_lists = bool(re.search(r'^\s*[-*•]\s', text, re.MULTILINE))
        has_headers = bool(re.search(r'^#+\s', text, re.MULTILINE))
        has_code = bool(re.search(r'```', text))
        has_links = bool(re.search(r'https?://\S+', text))

        # PII detection (basic)
        pii_found = []
        for pattern, label in [
            (r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', 'email'),
            (r'\+?\d{1,3}[-.\s]?\(?\d{2,3}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}', 'phone'),
        ]:
            if re.search(pattern, text):
                pii_found.append(label)

        return json.dumps({
            "status": "basic_analysis",
            "note": "POLER engine not available, using lightweight analysis",
            "text_length": len(text),
            "word_count": len(words),
            "sentence_count": len(sentences),
            "top_keywords": [{"word": w, "count": c} for w, c in word_freq],
            "structure": {
                "has_lists": has_lists,
                "has_headers": has_headers,
                "has_code": has_code,
                "has_links": has_links,
            },
            "pii_detected": pii_found,
            "query": query[:200] if query else "",
        }, ensure_ascii=False, indent=2)

    # ─── Skill Execution ──────────────────────────────────────────────────

    def execute_skill(
        self,
        skill_name: str,
        query: str,
        skill_md: str = "",
        skill_dir: Optional[str] = None,
        timeout_sec: int = 60,
    ) -> Optional[str]:
        """Execute a skill using the bridge.

        Routes the skill to the appropriate execution method:
        - POLER skills → direct library import (FREE)
        - File ops → AI's Read/Write tools (FREE)
        - Other local → Python subprocess (FREE)
        - Non-local → returns None (let super_z_core handle it)

        Args:
            skill_name: Name of the skill.
            query: User's query text.
            skill_md: Skill methodology document.
            skill_dir: Path to the skill directory.
            timeout_sec: Timeout for subprocess calls.

        Returns:
            Result text, or None if the skill cannot be handled by the bridge.
        """
        # POLER skills — direct library call
        if skill_name in ("poler-psi", "poler-toolkit"):
            return self.execute_poler(
                text=skill_md or query,
                query=query,
            )

        # Document triage — can be done with text analysis
        if skill_name == "doc-triage":
            return self._execute_doc_triage(query, skill_md)

        # File operations — use AI's Read/Write tools
        if skill_name == "file-ops":
            return self._execute_file_ops(query)

        # Version management — file system operations
        if skill_name == "version-management":
            return self._execute_version_mgmt(query, skill_dir)

        # Text analysis — basic local analysis
        if skill_name == "text-analysis":
            return self._basic_text_analysis(query, "")

        # Data transformation
        if skill_name == "data-transform":
            return self._execute_data_transform(query)

        # If we have a skill directory with a run script, try it
        if skill_dir:
            result = self._try_run_script(skill_dir, query, timeout_sec)
            if result is not None:
                return result

        # Cannot handle this skill through the bridge
        return None

    def _execute_doc_triage(self, query: str, skill_md: str) -> str:
        """Execute document triage (classification) locally.

        Uses rule-based heuristics to classify documents without LLM.
        """
        import re

        text = skill_md or query
        text_lower = text.lower()

        # Classification rules
        doc_type = "unknown"
        confidence = 0.0

        # Code detection
        code_indicators = ['def ', 'class ', 'import ', 'function ', 'var ', 'const ', '```']
        if sum(1 for ind in code_indicators if ind in text) >= 2:
            doc_type = "code"
            confidence = 0.85

        # API/technical docs
        api_indicators = ['endpoint', 'api', 'request', 'response', 'status code', 'http']
        if sum(1 for ind in api_indicators if ind in text_lower) >= 2:
            doc_type = "api_docs"
            confidence = 0.80

        # Configuration
        config_indicators = ['config', 'settings', 'environment', 'variables', 'yaml', 'json']
        if sum(1 for ind in config_indicators if ind in text_lower) >= 2:
            doc_type = "configuration"
            confidence = 0.75

        # Legal/contract
        legal_indicators = ['agreement', 'terms', 'liability', 'warranty', 'clause']
        if sum(1 for ind in legal_indicators if ind in text_lower) >= 2:
            doc_type = "legal"
            confidence = 0.80

        # Narrative/prose
        if doc_type == "unknown" and len(text.split()) > 100:
            doc_type = "narrative"
            confidence = 0.60

        return json.dumps({
            "doc_type": doc_type,
            "confidence": confidence,
            "text_length": len(text),
            "word_count": len(text.split()),
            "method": "rule_based_local",
        }, ensure_ascii=False, indent=2)

    def _execute_file_ops(self, query: str) -> str:
        """Execute file operations using AI's Read/Write tools."""
        import re

        # Parse the query for operation type
        query_lower = query.lower()

        if "read" in query_lower:
            # Extract file path from query
            match = re.search(r'["\']?(/[\w/.-]+)["\']?', query)
            if match:
                filepath = match.group(1)
                content = self.read(filepath)
                return json.dumps({
                    "operation": "read",
                    "filepath": filepath,
                    "content_length": len(content),
                    "content_preview": content[:500],
                }, ensure_ascii=False, indent=2)

        elif "write" in query_lower:
            match = re.search(r'["\']?(/[\w/.-]+)["\']?', query)
            if match:
                filepath = match.group(1)
                # Extract content (everything after "content:" or similar)
                content_match = re.search(r'content:\s*(.*)', query, re.IGNORECASE)
                content = content_match.group(1) if content_match else ""
                status = self.write(filepath, content)
                return json.dumps({
                    "operation": "write",
                    "filepath": filepath,
                    "status": status,
                }, ensure_ascii=False, indent=2)

        return json.dumps({
            "operation": "unknown",
            "message": "Could not parse file operation from query",
            "query": query[:200],
        }, ensure_ascii=False, indent=2)

    def _execute_version_mgmt(self, query: str, skill_dir: Optional[str]) -> str:
        """Execute version management (file system operations)."""
        # Simple version tracking via file timestamps
        if skill_dir:
            skill_path = Path(skill_dir)
            files = []
            for f in skill_path.rglob("*"):
                if f.is_file() and not f.name.startswith("."):
                    stat = f.stat()
                    files.append({
                        "path": str(f.relative_to(skill_path)),
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                    })
            return json.dumps({
                "operation": "version_check",
                "skill_dir": skill_dir,
                "files": files[:50],  # Limit to 50 files
                "total_files": len(files),
            }, ensure_ascii=False, indent=2)

        return json.dumps({
            "operation": "version_check",
            "message": "No skill directory provided",
        }, ensure_ascii=False, indent=2)

    def _execute_data_transform(self, query: str) -> str:
        """Execute data transformation (JSON, CSV, etc.) locally."""
        import re

        # Try to detect input format
        query_stripped = query.strip()

        # JSON → formatted
        if query_stripped.startswith("{") or query_stripped.startswith("["):
            try:
                data = json.loads(query_stripped)
                return json.dumps({
                    "operation": "json_format",
                    "formatted": json.dumps(data, ensure_ascii=False, indent=2),
                    "type": "object" if isinstance(data, dict) else "array",
                    "keys": list(data.keys()) if isinstance(data, dict) else None,
                }, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass

        # CSV-like
        if "," in query_stripped and "\n" in query_stripped:
            lines = query_stripped.split("\n")
            headers = lines[0].split(",")
            rows = [line.split(",") for line in lines[1:] if line.strip()]
            return json.dumps({
                "operation": "csv_parse",
                "headers": headers,
                "row_count": len(rows),
                "column_count": len(headers),
                "preview": rows[:5],
            }, ensure_ascii=False, indent=2)

        return json.dumps({
            "operation": "data_transform",
            "message": "Could not detect data format",
            "input_preview": query[:200],
        }, ensure_ascii=False, indent=2)

    def _try_run_script(
        self,
        skill_dir: str,
        query: str,
        timeout_sec: int,
    ) -> Optional[str]:
        """Try to run a skill's own script via subprocess."""
        run_py = Path(skill_dir) / "scripts" / "run.py"
        if not run_py.exists():
            return None

        try:
            cmd = [sys.executable, str(run_py), query]
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout_sec, cwd=str(skill_dir),
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, Exception):
            pass

        return None

    # ─── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return bridge execution statistics."""
        return {
            **self._stats,
            "has_ai_tools": self._has_ai_tools,
            "poler_available": self._poler_analyzer is not None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Module-level convenience functions
# ═══════════════════════════════════════════════════════════════════════════

# Module-level singleton bridge
_bridge_instance: Optional[AIBridge] = None


def get_bridge(
    bash_tool: Optional[Callable] = None,
    read_tool: Optional[Callable] = None,
    write_tool: Optional[Callable] = None,
    verbose: bool = False,
) -> AIBridge:
    """Get or create the module-level bridge singleton.

    Args:
        bash_tool: AI's Bash function.
        read_tool: AI's Read function.
        write_tool: AI's Write function.
        verbose: Print debug info.

    Returns:
        AIBridge instance.
    """
    global _bridge_instance
    if _bridge_instance is None or any([
        bash_tool is not None,
        read_tool is not None,
        write_tool is not None,
    ]):
        _bridge_instance = AIBridge(
            bash_tool=bash_tool,
            read_tool=read_tool,
            write_tool=write_tool,
            verbose=verbose,
        )
    return _bridge_instance


def bridge_execute_poler(text: str, query: str = "") -> str:
    """Convenience function: run POLER analysis through the bridge.

    Args:
        text: Text to analyze.
        query: Optional focus query.

    Returns:
        JSON string with analysis results.
    """
    bridge = get_bridge()
    return bridge.execute_poler(text, query)


def bridge_execute_skill(
    skill_name: str,
    query: str,
    skill_md: str = "",
    skill_dir: Optional[str] = None,
) -> Optional[str]:
    """Convenience function: execute a skill through the bridge.

    Args:
        skill_name: Name of the skill.
        query: User's query.
        skill_md: Skill methodology document.
        skill_dir: Path to skill directory.

    Returns:
        Result text, or None.
    """
    bridge = get_bridge()
    return bridge.execute_skill(skill_name, query, skill_md, skill_dir)


# ═══════════════════════════════════════════════════════════════════════════
# CLI — for testing
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Super Z Bridge — AI Native Tool Adapter"
    )
    parser.add_argument(
        "command",
        choices=["poler", "skill", "stats"],
        help="Command to execute",
    )
    parser.add_argument("--text", default="", help="Text to analyze")
    parser.add_argument("--query", default="", help="Query/focus")
    parser.add_argument("--skill", default="poler-psi", help="Skill name")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    bridge = AIBridge(verbose=args.verbose)

    if args.command == "poler":
        if not args.text:
            args.text = "This is a sample text for POLER analysis. " * 10
        result = bridge.execute_poler(args.text, args.query)
        print(result)

    elif args.command == "skill":
        result = bridge.execute_skill(
            args.skill, args.query or "test query",
        )
        if result:
            print(result)
        else:
            print("(no result — skill not handled by bridge)", file=sys.stderr)

    elif args.command == "stats":
        print(json.dumps(bridge.stats(), indent=2))
