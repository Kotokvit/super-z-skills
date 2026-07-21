#!/usr/bin/env python3
"""
watcher_daemon.py — Long-running background daemon that watches
`.context/inbox/` for incoming user messages and feeds them through
ConversationWatcher, so context_brief.json is kept warm "без воли"
(without the agent having to remember to call it).

Architecture
------------
                ┌──────────────────────────────────────┐
   enqueue ───► │  .context/inbox/<ts>.json            │
                └──────────────┬───────────────────────┘
                               │  (poll every POLL_SEC)
                               ▼
                ┌──────────────────────────────────────┐
                │  watcher_daemon.py (this process)    │
                │   ├─ parse message                   │
                │   ├─ ConversationWatcher.process_msg │
                │   └─ move file → processed/          │
                └──────────────┬───────────────────────┘
                               ▼
                ┌──────────────────────────────────────┐
                │  .context/context_brief.json         │  ◄── agent reads this
                │  .context/daemon.log                 │
                └──────────────────────────────────────┘

Inbox file format (JSON):
    {
        "id": "msg-20260704-153000-abc123",
        "ts": 1759650600.0,
        "message": "user message text",
        "session_id": "web-97dbac8f-...",
        "wait_for_brief": true,           # optional
        "expected_skills": ["web-search"] # optional hint
    }

Usage
-----
    # Start as background daemon:
    nohup python3 watcher_daemon.py > /dev/null 2>&1 &

    # Or in foreground (for debugging):
    python3 watcher_daemon.py --foreground

    # Stop:
    python3 watcher_daemon.py --stop

    # Status:
    python3 watcher_daemon.py --status

Author: Super-Z v2.0.1, 2026-07-04
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Make sibling modules importable
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from watcher import ConversationWatcher, DEFAULT_BRIEF_FILE  # noqa: E402

# ─────────────────────────────────────────────────────────────────────
# Paths and constants
# ─────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INBOX_DIR = PROJECT_ROOT / ".context" / "inbox"
PROCESSED_DIR = INBOX_DIR / "processed"
FAILED_DIR = INBOX_DIR / "failed"
LOG_FILE = PROJECT_ROOT / ".context" / "daemon.log"
PID_FILE = PROJECT_ROOT / ".context" / "watcher_daemon.pid"
STATUS_FILE = PROJECT_ROOT / ".context" / "watcher_daemon.status"
BRIEF_FILE = DEFAULT_BRIEF_FILE

POLL_SEC = 0.5           # how often to scan inbox
BRIEF_WAIT_SEC = 12      # how long to wait for skills to write back to brief
BRIEF_POLL_SEC = 0.3     # brief polling interval
STALE_SEC = 600          # messages older than this are auto-failed

for d in (INBOX_DIR, PROCESSED_DIR, FAILED_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────

def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    sys.stderr.write(line)
    sys.stderr.flush()


def write_status(state: str, **extra: Any) -> None:
    payload = {
        "state": state,
        "pid": os.getpid(),
        "ts": time.time(),
        "hostname": socket.gethostname(),
        **extra,
    }
    try:
        STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# PID file management
# ─────────────────────────────────────────────────────────────────────

def write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def read_pid() -> Optional[int]:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def clear_pid() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


# ─────────────────────────────────────────────────────────────────────
# Message processing
# ─────────────────────────────────────────────────────────────────────

def _move_file(src: Path, dest_dir: Path) -> Path:
    """Atomically move src into dest_dir with collision-safe name."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stem, ext = src.stem, src.suffix
        dest = dest_dir / f"{stem}-{int(time.time()*1000)%10000}{ext}"
    src.rename(dest)
    return dest


def _brief_entry_count() -> int:
    try:
        b = json.loads(BRIEF_FILE.read_text(encoding="utf-8"))
        return len(b.get("entries", []))
    except Exception:
        return 0


def process_inbox_file(path: Path, watcher: ConversationWatcher) -> bool:
    """Process one inbox file. Returns True on success."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"FAIL parse {path.name}: {e}", "ERROR")
        _move_file(path, FAILED_DIR)
        return False

    msg_id = payload.get("id", path.stem)
    message = payload.get("message", "")
    session_id = payload.get("session_id")
    wait = bool(payload.get("wait_for_brief", True))

    if not message:
        log(f"SKIP {msg_id}: empty message", "WARN")
        _move_file(path, PROCESSED_DIR)
        return True

    # Stale check
    ts = payload.get("ts", 0)
    if ts and (time.time() - ts > STALE_SEC):
        log(f"SKIP {msg_id}: stale ({int(time.time()-ts)}s old)", "WARN")
        _move_file(path, FAILED_DIR)
        return False

    log(f"PROCESS {msg_id} (session={session_id}, {len(message)} chars)")

    before = _brief_entry_count()

    try:
        report = watcher.process_message(message)
    except Exception as e:
        log(f"ERROR process_message {msg_id}: {e}\n{traceback.format_exc()}",
            "ERROR")
        _move_file(path, FAILED_DIR)
        return False

    dispatched = report.get("dispatched", [])
    n_signals = len(report.get("signals", []))
    log(f"DISPATCHED {msg_id}: {len(dispatched)} skill(s), "
        f"{n_signals} signal(s) → "
        f"{[d.get('skill') for d in dispatched]}")

    # Optionally wait for the brief to be updated by background skills
    if wait and dispatched:
        deadline = time.time() + BRIEF_WAIT_SEC
        while time.time() < deadline:
            after = _brief_entry_count()
            if after > before:
                log(f"BRIEF updated: {before} → {after} entries")
                break
            time.sleep(BRIEF_POLL_SEC)
        else:
            log(f"BRIEF not updated within {BRIEF_WAIT_SEC}s "
                f"(skills may still be running)", "WARN")

    _move_file(path, PROCESSED_DIR)
    return True


# ─────────────────────────────────────────────────────────────────────
# Daemon main loop
# ─────────────────────────────────────────────────────────────────────

class Daemon:
    def __init__(self) -> None:
        self.watcher = ConversationWatcher(
            verbose=False,
            max_workers=3,
            transient=False,
        )
        self._stop = False

    def handle_signal(self, signum, frame) -> None:  # noqa: ARG002
        log(f"Signal {signum} received, shutting down...", "WARN")
        self._stop = True

    def scan_once(self) -> int:
        """Scan inbox once, process all .json files. Returns count processed."""
        files = sorted(INBOX_DIR.glob("*.json"),
                       key=lambda p: p.stat().st_mtime)
        n = 0
        for f in files:
            if self._stop:
                break
            ok = process_inbox_file(f, self.watcher)
            if ok:
                n += 1
        return n

    def run_forever(self) -> int:
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

        write_pid()
        write_status("running", started_at=time.time())

        log(f"=== watcher_daemon STARTED (pid={os.getpid()}) ===")
        log(f"inbox: {INBOX_DIR}")
        log(f"brief: {BRIEF_FILE}")
        log(f"poll interval: {POLL_SEC}s")

        # Purge leftover stale files on startup
        try:
            self.watcher.purge_expired()
        except Exception as e:
            log(f"purge_expired failed: {e}", "WARN")

        processed_total = 0
        last_heartbeat = 0.0

        try:
            while not self._stop:
                n = self.scan_once()
                processed_total += n

                # Heartbeat every 30s
                now = time.time()
                if now - last_heartbeat > 30:
                    write_status(
                        "running",
                        processed_total=processed_total,
                        last_heartbeat=now,
                        inbox_pending=len(list(INBOX_DIR.glob("*.json"))),
                    )
                    last_heartbeat = now

                time.sleep(POLL_SEC)
        finally:
            log("Shutting down watcher pool...")
            try:
                self.watcher.shutdown()
            except Exception as e:
                log(f"watcher.shutdown() error: {e}", "WARN")
            clear_pid()
            write_status("stopped")
            log(f"=== watcher_daemon STOPPED "
                f"(processed {processed_total} messages total) ===")

        return 0


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def cmd_start(foreground: bool) -> int:
    pid = read_pid()
    if pid and is_running(pid):
        log(f"Daemon already running (pid={pid})", "WARN")
        return 1

    if foreground:
        return Daemon().run_forever()

    # Detach into background
    pid = os.fork()
    if pid > 0:
        # Parent
        time.sleep(0.3)
        new_pid = read_pid()
        log(f"Daemon started in background (pid={new_pid})", "INFO")
        return 0

    # Child — become session leader, redirect std streams
    os.setsid()
    try:
        os.close(0); os.close(1); os.close(2)
    except Exception:
        pass
    devnull = os.open("/dev/null", os.O_RDWR)
    os.dup2(devnull, 0)
    log_fd = open(LOG_FILE, "a", encoding="utf-8")
    os.dup2(log_fd.fileno(), 1)
    os.dup2(log_fd.fileno(), 2)
    return Daemon().run_forever()


def cmd_stop() -> int:
    pid = read_pid()
    if not pid:
        print("Daemon not running (no pid file).")
        return 1
    if not is_running(pid):
        clear_pid()
        print(f"Stale pid file (pid={pid} not alive). Cleared.")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"SIGTERM sent to pid={pid}")
    except Exception as e:
        print(f"Failed to stop: {e}", file=sys.stderr)
        return 1
    # Wait up to 5s for clean exit
    for _ in range(50):
        time.sleep(0.1)
        if not is_running(pid):
            clear_pid()
            print("Daemon stopped.")
            return 0
    # Force kill
    try:
        os.kill(pid, signal.SIGKILL)
        clear_pid()
        print(f"SIGKILL sent to pid={pid}")
        return 0
    except Exception:
        return 1


def cmd_status() -> int:
    pid = read_pid()
    running = bool(pid and is_running(pid))
    print(f"Running: {running}")
    if pid:
        print(f"PID: {pid}")
        print(f"Alive: {is_running(pid)}")
    if STATUS_FILE.exists():
        try:
            s = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            print(f"State: {s.get('state')}")
            print(f"Started: {datetime.fromtimestamp(s.get('started_at', 0))}")
            print(f"Processed total: {s.get('processed_total', '?')}")
            print(f"Inbox pending: {s.get('inbox_pending', '?')}")
            print(f"Last heartbeat: "
                  f"{datetime.fromtimestamp(s.get('last_heartbeat', 0))}")
        except Exception as e:
            print(f"Status parse error: {e}")
    if LOG_FILE.exists():
        size = LOG_FILE.stat().st_size
        print(f"Log: {LOG_FILE} ({size} bytes)")
    return 0 if running else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--start", action="store_true",
                   help="Start daemon in background (default)")
    g.add_argument("--foreground", action="store_true",
                   help="Run daemon in foreground (debug)")
    g.add_argument("--stop", action="store_true", help="Stop daemon")
    g.add_argument("--status", action="store_true", help="Show daemon status")
    g.add_argument("--restart", action="store_true", help="Restart daemon")
    args = ap.parse_args()

    if args.stop:
        return cmd_stop()
    if args.status:
        return cmd_status()
    if args.restart:
        cmd_stop()
        time.sleep(0.5)
        return cmd_start(foreground=False)
    if args.foreground:
        return cmd_start(foreground=True)
    # Default: start in background
    return cmd_start(foreground=False)


if __name__ == "__main__":
    sys.exit(main())
