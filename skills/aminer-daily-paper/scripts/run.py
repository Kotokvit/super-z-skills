#!/usr/bin/env python3
"""aminer-daily-paper runner — thin wrapper over _shared/llm_wrapper.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from llm_wrapper import run_skill

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None, help="User query")
    ap.add_argument("--backend", default=None,
                    choices=["zai_cli", "sandbox", "mock"],
                    help="LLM backend: zai_cli (default), sandbox (internal agents), mock (placeholder)")
    args = ap.parse_args()
    result = run_skill("aminer-daily-paper", user_query=args.query, backend=args.backend)
    sys.exit(0 if result.get("status") == "success" else 1)
