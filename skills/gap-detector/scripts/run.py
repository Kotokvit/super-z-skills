#!/usr/bin/env python3
"""gap-detector runner — delegates to scripts/gap_detector.py."""
import subprocess
import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parent / "gap_detector.py"


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else None
    cmd = [sys.executable, str(MAIN)]
    if query:
        cmd.append(query)
    else:
        cmd.append("-")
        # Read stdin and pass through
    if not query or query == "-":
        # Pipe stdin to subprocess
        proc = subprocess.run(cmd, stdin=sys.stdin)
    else:
        proc = subprocess.run(cmd)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
