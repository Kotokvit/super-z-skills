#!/usr/bin/env python3
"""media-triage runner — delegates to scripts/media_triage.py."""
import subprocess
import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parent / "media_triage.py"


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else None
    cmd = [sys.executable, str(MAIN)]
    if query:
        cmd.append(query)
        cmd.append("--json")
    else:
        cmd.append("-")
        cmd.append("--json")
    if not query or query == "-":
        proc = subprocess.run(cmd, stdin=sys.stdin)
    else:
        proc = subprocess.run(cmd)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
