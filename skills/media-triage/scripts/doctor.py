#!/usr/bin/env python3
"""
doctor.py — Self-test for media-triage skill.

Checks:
  1. Python version >= 3.8
  2. yt-dlp binary available
  3. ffmpeg binary available
  4. z-ai CLI available
  5. poler-toolkit ingest.py exists
  6. Cache dir writable
  7. URL extraction regex works (smoke test)
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"

checks_passed = 0
checks_failed = 0
checks_warning = 0


def ok(name: str, msg: str = ""):
    global checks_passed
    checks_passed += 1
    print(f"  ✓ {name}{': ' + msg if msg else ''}")


def fail(name: str, msg: str):
    global checks_failed
    checks_failed += 1
    print(f"  ✗ {name}: {msg}")


def warn(name: str, msg: str):
    global checks_warning
    checks_warning += 1
    print(f"  ⚠ {name}: {msg}")


def run_check(name: str, fn):
    try:
        result = fn()
        if result is True:
            ok(name)
        elif isinstance(result, tuple) and result[0]:
            ok(name, result[1])
        else:
            fail(name, str(result) if not isinstance(result, tuple) else result[1])
    except Exception as e:
        fail(name, str(e))


# 1. Python version
def check_python():
    v = sys.version_info
    if v >= (3, 8):
        return True, f"{v.major}.{v.minor}.{v.micro}"
    return False, f"Python {v.major}.{v.minor} too old (need >=3.8)"


# 2. yt-dlp binary
def check_yt_dlp():
    path = shutil.which("yt-dlp") or str(Path.home() / ".local/bin/yt-dlp")
    if not Path(path).exists():
        return False, f"yt-dlp not found at {path}"
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            return True, f"v{proc.stdout.strip()}"
        return False, f"yt-dlp exited {proc.returncode}"
    except Exception as e:
        return False, f"yt-dlp failed: {e}"


# 3. ffmpeg
def check_ffmpeg():
    path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    if not Path(path).exists():
        return False, f"ffmpeg not found at {path}"
    return True, path


# 4. z-ai CLI
def check_z_ai():
    path = shutil.which("z-ai") or "/usr/local/bin/z-ai"
    if not Path(path).exists():
        return False, f"z-ai not found at {path}"
    # Quick smoke test — just check --help works
    try:
        proc = subprocess.run([path, "asr", "--help"], capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 or "Speech to Text" in proc.stdout:
            return True, path
        return False, f"z-ai asr --help exited {proc.returncode}"
    except Exception as e:
        return False, f"z-ai failed: {e}"


# 5. poler-toolkit ingest.py
def check_poler():
    poler = SKILL_DIR.parent / "poler-toolkit" / "scripts" / "ingest.py"
    if not poler.exists():
        warn("poler-toolkit", f"ingest.py not found at {poler} — fallback summary will be used")
        return True, "fallback mode"
    return True, str(poler.relative_to(SKILL_DIR.parent.parent))


# 6. Cache dir
def check_cache_dir():
    cache = Path("/tmp/media_triage_cache")
    try:
        cache.mkdir(parents=True, exist_ok=True)
        test_file = cache / ".doctor_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return True, str(cache)
    except Exception as e:
        return False, f"Cache dir not writable: {e}"


# 7. URL extraction smoke test
def check_url_extraction():
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from media_triage import extract_media_url
        test_cases = [
            ("посмотри https://youtube.com/watch?v=abc123 пожалуйста",
             "https://youtube.com/watch?v=abc123"),
            ("https://youtu.be/xyz789 end", "https://youtu.be/xyz789"),
            ("podcast https://soundcloud.com/user/track here",
             "https://soundcloud.com/user/track"),
            ("no media here", None),
        ]
        for inp, expected in test_cases:
            got = extract_media_url(inp)
            if got != expected:
                return False, f"Expected {expected!r}, got {got!r} for: {inp[:40]!r}"
        return True, f"{len(test_cases)} test cases passed"
    except Exception as e:
        return False, f"Import or test failed: {e}"


def main() -> int:
    print("\n" + "=" * 60)
    print("  media-triage — doctor self-test")
    print("=" * 60 + "\n")

    run_check("Python version", check_python)
    run_check("yt-dlp binary", check_yt_dlp)
    run_check("ffmpeg binary", check_ffmpeg)
    run_check("z-ai CLI", check_z_ai)
    run_check("poler-toolkit ingest.py", check_poler)
    run_check("cache dir writable", check_cache_dir)
    run_check("URL extraction regex", check_url_extraction)

    print(f"\n{'=' * 60}")
    print(f"  Summary: {checks_passed} passed, {checks_warning} warnings, {checks_failed} failed")
    print(f"{'=' * 60}\n")

    return 0 if checks_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
