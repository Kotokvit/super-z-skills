from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path
from typing import Optional

from .config import get_brief_file, get_context_dir, get_skills_dir, load_config
from . import __version__


def _run_python_script(script: Path, *args: str) -> int:
    cmd = [sys.executable, str(script), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


def _list_skill_names(skills_dir: Path) -> list[str]:
    names: list[str] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        if (child / "manifest.json").exists() or (child / "SKILL.md").exists():
            names.append(child.name)
    return names


def cmd_skills(skills_dir: Path) -> int:
    names = _list_skill_names(skills_dir)
    print(f"{len(names)} skills registered:")
    for name in names:
        print(name)
    return 0


def cmd_run(skill_name: str, query: str, skills_dir: Path, backend: str = "mock") -> int:
    skill_dir = skills_dir / skill_name
    if not skill_dir.exists():
        print(f"Skill not found: {skill_name}", file=sys.stderr)
        return 1

    run_script = skill_dir / "scripts" / "run.py"
    if run_script.exists():
        proc = subprocess.run(
            [sys.executable, str(run_script), query, "--backend", backend],
            capture_output=True,
            text=True,
        )
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        return proc.returncode

    llm_wrapper = skills_dir / "_shared" / "llm_wrapper.py"
    if llm_wrapper.exists():
        proc = subprocess.run(
            [sys.executable, str(llm_wrapper), "--skill", skill_name, "--query", query, "--backend", backend],
            capture_output=True,
            text=True,
        )
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        return proc.returncode

    print(f"No runnable entry point for skill {skill_name}", file=sys.stderr)
    return 1


def cmd_query(query: str, skills_dir: Path) -> int:
    brief_file = get_brief_file()
    brief_file.parent.mkdir(parents=True, exist_ok=True)
    if brief_file.exists():
        try:
            payload = json.loads(brief_file.read_text(encoding="utf-8"))
        except Exception:
            payload = {"entries": []}
    else:
        payload = {"entries": []}

    entries = payload.get("entries", [])
    if entries:
        context = []
        for entry in entries[-3:]:
            context.append(f"[{entry.get('skill', '?')}] {entry.get('brief', '')}")
        prompt = (
            "You are Super-Z. Use the following pre-gathered context to answer the user.\n\n"
            + "\n\n".join(context)
            + f"\n\nUSER REQUEST:\n{query}"
        )
    else:
        prompt = f"You are Super-Z. Respond to the user request.\n\nUSER REQUEST:\n{query}"

    print(json.dumps({"status": "ok", "query": query, "context_entries": len(entries), "prompt": prompt}, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="super-z", description="Super-Z skill orchestrator")
    parser.add_argument("query", nargs="?", help="One-shot query")
    parser.add_argument("--version", action="version", version=f"super-z {__version__}")
    parser.add_argument("--skills", action="store_true", help="List registered skills")
    parser.add_argument("--run", nargs=2, metavar=("SKILL", "QUERY"), help="Run a specific skill")
    parser.add_argument("--brief", action="store_true", help="Show current context brief")
    parser.add_argument("--watch", action="store_true", help="Watch for inputs")
    parser.add_argument("--signals", action="store_true", help="Show signal patterns")
    parser.add_argument("--daemon", nargs="*", metavar="ACTION", help="Daemon commands")
    parser.add_argument("--enqueue", nargs="+", help="Enqueue a message")
    parser.add_argument("--self-context", nargs="+", help="Enqueue and wait for brief")
    parser.add_argument("--skills-dir", default=str(get_skills_dir()), help="Override skills directory")
    parser.add_argument("--backend", default=None, help="LLM backend to use for skill execution")
    parser.add_argument("--config", default=None, help="Optional path to a TOML config file")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.backend:
        config.backend = args.backend

    skills_dir = Path(args.skills_dir).expanduser().resolve()
    if not skills_dir.exists():
        skills_dir = config.skills_dir

    if args.skills:
        return cmd_skills(skills_dir)
    if args.run:
        skill_name, query = args.run
        return cmd_run(skill_name, query, skills_dir, backend=config.backend)
    if args.brief:
        path = get_brief_file()
        if path.exists():
            print(path.read_text(encoding="utf-8"))
        else:
            print("{}")
        return 0
    if args.watch:
        print("watch mode is not yet implemented; use the existing shell wrapper for interactive mode")
        return 0
    if args.signals:
        print("signal patterns are available from the orchestrator modules")
        return 0
    if args.daemon is not None:
        print("daemon mode is not yet implemented")
        return 0
    if args.enqueue is not None:
        print("".join(args.enqueue))
        return 0
    if args.self_context is not None:
        print("".join(args.self_context))
        return 0
    if args.query:
        return cmd_query(args.query, skills_dir)

    parser.print_help()
    return 0
