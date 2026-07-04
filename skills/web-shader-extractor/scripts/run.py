#!/usr/bin/env python3
"""web-shader-extractor runner — thin wrapper over _shared/llm_wrapper.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from llm_wrapper import run_skill

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_skill("web-shader-extractor", user_query=query)
    sys.exit(0 if result.get("status") == "success" else 1)
