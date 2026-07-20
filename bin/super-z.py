#!/usr/bin/env python3
"""
super-z.py — Cross-platform Python entry point for Super-Z CLI.

This is the Python equivalent of bin/super-z (bash). It works on Linux,
macOS, and Windows without any shell dependency. The bash version remains
available for Unix users who prefer it; this Python version is the
canonical cross-platform entry point.

Usage:
    python3 super-z.py "your request"
    python3 super-z.py --brief
    python3 super-z.py --skills
    python3 super-z.py --signals
    python3 super-z.py --run <skill> "query"
    python3 super-z.py --watch
    python3 super-z.py --enqueue "message"
    python3 super-z.py --self-context "message"
    python3 super-z.py --daemon start|stop|restart|status|foreground
    python3 super-z.py --help

Author: Vitalij Kotok (vitalijkotok18@gmail.com)
License: GPL v3
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ─── Resolve project root ───────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "bin" else SCRIPT_DIR
SKILLS_DIR = PROJECT_ROOT / "skills"
ORCHESTRATOR = SKILLS_DIR / "_orchestrator" / "scripts"
CONTEXT_DIR = PROJECT_ROOT / ".context"
BRIEF_FILE = CONTEXT_DIR / "context_brief.json"

# Use venv if available, else system Python
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python3"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


# ─── ANSI colors (disabled on Windows if no color support) ──────────────
def _supports_color() -> bool:
    if os.name == "nt":  # Windows
        return os.environ.get("ANSICON") or os.environ.get("WT_SESSION") \
            or "PROMPT" in os.environ  # basic heuristic
    return sys.stdout.isatty()

if _supports_color():
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
    BLUE = "\033[34m"; BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"
else:
    RED = GREEN = YELLOW = BLUE = BOLD = DIM = RESET = ""


# ─── Commands ───────────────────────────────────────────────────────────

def show_help() -> int:
    print(f"""{BOLD}super-z{RESET} — Self-regulating skill orchestrator

{BOLD}Usage:{RESET}
  super-z "your request"             {DIM}# one-shot: watcher + orchestrator${RESET}
  super-z --watch                    {DIM}# interactive stdin loop${RESET}
  super-z --brief                    {DIM}# show current context_brief.json${RESET}
  super-z --skills                   {DIM}# list all registered skills${RESET}
  super-z --signals                  {DIM}# show watcher signal patterns${RESET}
  super-z --run <skill> "query"      {DIM}# run a specific skill directly${RESET}
  super-z --enqueue "message"        {DIM}# drop msg into inbox (daemon picks up)${RESET}
  super-z --self-context "message"   {DIM}# enqueue + wait for brief (agent hook)${RESET}
  super-z --daemon start|stop|status {DIM}# manage watcher daemon${RESET}
  super-z --help                     {DIM}# this help${RESET}

{BOLD}How it works:{RESET}
  1. Watcher scans your message for signals (URLs, keywords, intents)
  2. Matching skills run in background → produce Pattern 1 briefs
  3. Briefs accumulate in {DIM}{BRIEF_FILE}{RESET}
  4. Agent reads briefs BEFORE answering → strategy decision, not data-gathering

{BOLD}Examples:{RESET}
  super-z "напиши пост про ИИ в медицине"
  super-z "посмотри это https://youtube.com/watch?v=xxx"
  super-z "что подарить маме на 60 лет"
  super-z --run blog-writer "напиши 800 слов про продуктивность"

{BOLD}Skills:{RESET} {sum(1 for _ in SKILLS_DIR.iterdir() if _.is_dir() and _.name not in ('_orchestrator', '_shared'))} registered
{BOLD}Author:{RESET} Vitalij Kotok <vitalijkotok18@gmail.com>
{BOLD}License:{RESET} GPL v3""")
    return 0


def cmd_brief() -> int:
    if BRIEF_FILE.exists():
        try:
            data = json.loads(BRIEF_FILE.read_text(encoding="utf-8"))
            print(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            print(BRIEF_FILE.read_text(encoding="utf-8"))
    else:
        print(f"{DIM}(no context brief yet){RESET}")
    return 0


def cmd_skills() -> int:
    print(f"{BOLD}Registered skills:{RESET}")
    print()
    skills = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir() or d.name in ("_orchestrator", "_shared"):
            continue
        has_run = (d / "scripts" / "run.py").exists()
        has_manifest = (d / "manifest.json").exists()
        if has_run:
            status = f"{GREEN}✓ executable{RESET}"
        elif has_manifest:
            status = f"{YELLOW}○ docs_only{RESET}"
        else:
            status = f"{RED}✗ no manifest{RESET}"
        desc = ""
        if has_manifest:
            try:
                m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                desc = (m.get("description") or "")[:80]
            except Exception:
                pass
        skills.append((d.name, status, desc))

    # Strip ANSI for width calculation
    import re
    for name, status, desc in skills:
        ansi = re.compile(r"\x1b\[[0-9;]*m")
        status_plain = ansi.sub("", status)
        print(f"  {name:<30s} {status_plain}  {DIM}{desc}{RESET}")
    return 0


def cmd_signals() -> int:
    watcher = ORCHESTRATOR / "watcher.py"
    if not watcher.exists():
        print(f"{RED}watcher.py not found at {watcher}{RESET}")
        return 1
    result = subprocess.run(
        [PYTHON, str(watcher), "--list-signals"],
        capture_output=False
    )
    return result.returncode


def cmd_run_skill(args) -> int:
    if not args.skill or not args.query:
        print(f'{RED}Usage: super-z --run <skill> "query"{RESET}')
        return 1
    skill_dir = SKILLS_DIR / args.skill
    if not skill_dir.exists():
        print(f"{RED}Skill not found: {args.skill}{RESET}")
        return 1
    run_py = skill_dir / "scripts" / "run.py"
    backend_flag = getattr(args, 'backend', None)
    if run_py.exists():
        cmd = [PYTHON, str(run_py), args.query]
        if backend_flag:
            cmd.extend(["--backend", backend_flag])
        result = subprocess.run(cmd)
        return result.returncode
    llm_wrapper = SKILLS_DIR / "_shared" / "llm_wrapper.py"
    if llm_wrapper.exists():
        print(f"{YELLOW}Skill '{args.skill}' has no executable wrapper. "
              f"Using LLM wrapper directly.{RESET}")
        cmd = [PYTHON, str(llm_wrapper), "--skill", args.skill, "--query", args.query]
        if backend_flag:
            cmd.extend(["--backend", backend_flag])
        result = subprocess.run(cmd)
        return result.returncode
    print(f"{RED}No run.py or llm_wrapper.py found{RESET}")
    return 1


def cmd_watch() -> int:
    print(f"{BOLD}Super-Z interactive watcher{RESET} "
          f"{DIM}(type messages, Ctrl-D to exit){RESET}")
    print()
    watcher = ORCHESTRATOR / "watcher.py"
    result = subprocess.run([PYTHON, str(watcher), "--stdin"])
    return result.returncode


def cmd_daemon(action: str) -> int:
    daemon = ORCHESTRATOR / "watcher_daemon.py"
    if not daemon.exists():
        print(f"{RED}watcher_daemon.py not found at {daemon}{RESET}")
        return 1
    valid = {"start", "stop", "restart", "status", "foreground"}
    if action not in valid:
        print(f"{RED}Usage: super-z --daemon start|stop|restart|status|foreground{RESET}")
        return 1
    flag = f"--{action}"
    result = subprocess.run([PYTHON, str(daemon), flag])
    return result.returncode


def cmd_enqueue(message: str) -> int:
    if not message:
        print(f'{RED}Usage: super-z --enqueue "message"{RESET}')
        return 1
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    inbox = CONTEXT_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    msg_id = f"msg-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    payload = {
        "id": msg_id,
        "ts": ts,
        "message": message,
        "wait_for_brief": False,
    }
    (inbox / f"{msg_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(msg_id)
    return 0


def cmd_self_context(message: str) -> int:
    """Agent self-trigger: enqueue + wait for brief + print new entries."""
    if not message:
        print(f"{DIM}(no message to enqueue){RESET}")
        return 0
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    inbox = CONTEXT_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    # Count current entries
    def count_brief() -> int:
        try:
            return len(json.loads(BRIEF_FILE.read_text(encoding="utf-8")).get("entries", []))
        except Exception:
            return 0

    before = count_brief()
    ts = int(time.time())
    msg_id = f"msg-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    payload = {
        "id": msg_id,
        "ts": ts,
        "message": message,
        "wait_for_brief": True,
        "session_id": "self-context",
    }
    (inbox / f"{msg_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Wait up to 12s for the brief to be updated
    deadline = ts + 12
    while time.time() < deadline:
        after = count_brief()
        if after > before:
            # Print new entries
            try:
                b = json.loads(BRIEF_FILE.read_text(encoding="utf-8"))
                entries = b.get("entries", [])
                for e in entries[before:]:
                    msg_preview = (e.get("message_preview") or "")[:80]
                    print(f"=== message: {msg_preview} ===")
                    results = e.get("results") or {}
                    for skill, out in results.items():
                        if not isinstance(out, dict):
                            continue
                        status = out.get("status", "?")
                        conf = out.get("confidence", "?")
                        summ = (out.get("summary") or "").strip()
                        ents = out.get("entities", []) or []
                        srcs = out.get("sources", []) or []
                        warns = out.get("warnings", []) or []
                        arts = out.get("artifacts", []) or []
                        print(f"[{skill}] status={status} conf={conf}")
                        if summ:
                            print(f"  summary: {summ[:400]}")
                        for ent in ents[:5]:
                            if isinstance(ent, dict):
                                print(f'  entity: {ent.get("type","?")}/'
                                      f'{ent.get("name", ent.get("value","?"))}')
                        for src in srcs[:3]:
                            if isinstance(src, dict):
                                title = src.get("title", src.get("url", str(src)))
                                print(f"  source: {str(title)[:120]}")
                        for w in warns[:3]:
                            print(f"  warn: {str(w)[:120]}")
                        for a in arts[:3]:
                            if isinstance(a, dict):
                                p = a.get("path", a.get("type", str(a)))
                                print(f"  artifact: {str(p)[:120]}")
            except Exception as e:
                print(f"{RED}brief parse error: {e}{RESET}")
            return 0
        time.sleep(0.3)

    print(f"{DIM}(no new brief entries within 12s){RESET}")
    return 0


def cmd_oneshot(query: str) -> int:
    """One-shot: run watcher on the message, then read brief."""
    if not query:
        print(f'{RED}Usage: super-z "your request"{RESET}')
        return 1
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{DIM}[1/2] Scanning message for signals and dispatching skills...{RESET}")
    watcher = ORCHESTRATOR / "watcher.py"
    subprocess.run(
        [PYTHON, str(watcher), "--process", query, "--wait", "8"],
        capture_output=True, text=True
    )

    print(f"{DIM}[2/2] Reading brief...{RESET}")
    if not BRIEF_FILE.exists():
        print(f"{DIM}(no brief yet){RESET}")
        return 0
    try:
        b = json.loads(BRIEF_FILE.read_text(encoding="utf-8"))
        entries = b.get("entries", [])
        if not entries:
            print(f"{DIM}(no entries in brief){RESET}")
            return 0
        for e in entries[-5:]:
            msg_preview = (e.get("message_preview") or "")[:80]
            print(f"=== {msg_preview} ===")
            for skill, out in (e.get("results") or {}).items():
                if isinstance(out, dict):
                    summ = (out.get("summary") or "").strip()
                    conf = out.get("confidence", "?")
                    print(f"  [{skill}] conf={conf} {summ[:200]}")
    except Exception as e:
        print(f"{RED}brief parse error: {e}{RESET}")
    return 0


# ─── Main ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Super-Z Skill Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("message", nargs="*", help="user request (one-shot)")
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("--brief", action="store_true")
    parser.add_argument("--skills", action="store_true")
    parser.add_argument("--signals", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--run", metavar="SKILL", help="run a specific skill")
    parser.add_argument("--enqueue", action="store_true",
                        help="drop message into inbox (async)")
    parser.add_argument("--self-context", action="store_true",
                        help="enqueue + wait for brief (agent hook)")
    parser.add_argument("--daemon", metavar="ACTION",
                        help="start|stop|restart|status|foreground")
    parser.add_argument("--backend", default=None,
                        choices=["zai_cli", "sandbox", "mock"],
                        help="LLM backend: zai_cli (default), sandbox (internal agents), mock (placeholder)")
    args = parser.parse_args()

    if args.help:
        return show_help()
    if args.brief:
        return cmd_brief()
    if args.skills:
        return cmd_skills()
    if args.signals:
        return cmd_signals()
    if args.watch:
        return cmd_watch()
    if args.run:
        # --run SKILL "query..."
        query_text = " ".join(args.message) if args.message else ""
        # Use a simple namespace instead of inner class (avoids closure issue)
        import types
        run_args = types.SimpleNamespace(
            skill=args.run,
            query=query_text,
            backend=args.backend,
        )
        return cmd_run_skill(run_args)
    if args.daemon:
        return cmd_daemon(args.daemon)
    if args.enqueue:
        message = " ".join(args.message) if args.message else ""
        return cmd_enqueue(message)
    if args.self_context:
        message = " ".join(args.message) if args.message else ""
        return cmd_self_context(message)
    if args.message:
        return cmd_oneshot(" ".join(args.message))
    return show_help()


if __name__ == "__main__":
    sys.exit(main())
