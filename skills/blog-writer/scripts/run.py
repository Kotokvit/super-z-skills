#!/usr/bin/env python3
"""blog-writer runner — thin wrapper over _shared/llm_wrapper.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from llm_wrapper import run_skill

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else None
    backend = "mock"
    if len(sys.argv) > 2 and sys.argv[2] == "--backend":
        backend = sys.argv[3] if len(sys.argv) > 3 else backend
    result = run_skill("blog-writer", user_query=query, backend_name=backend)
    sys.exit(0 if result.get("status") == "success" else 1)
