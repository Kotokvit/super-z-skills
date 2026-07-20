#!/usr/bin/env python3
"""video-generation runner — dispatches to z-ai video subcommand."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

Z_AI = shutil.which("z-ai") or "/usr/local/bin/z-ai"
SKILL_DIR = Path(__file__).resolve().parent.parent


def main():
    if not query or query == "-":
        query = sys.stdin.read().strip()
    if not query:
        print(json.dumps({"skill": "video-generation", "status": "error",
                          "error": "no query provided"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    cmd = [Z_AI, "video", "--prompt", query]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print(json.dumps({"skill": "video-generation", "status": "error",
                          "error": "timeout (300s)"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    if r.returncode != 0:
        # Fallback to LLM wrapper
        sys.path.insert(0, str(SKILL_DIR.parent / "_shared"))
        try:
            from llm_wrapper import run_skill
            result = run_skill("video-generation", user_query=query)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0 if result.get("status") == "success" else 1)
        except Exception as e:
            print(json.dumps({"skill": "video-generation", "status": "error",
                              "error": f"z-ai failed and fallback failed: {e}"},
                             ensure_ascii=False, indent=2))
            sys.exit(1)

    out = r.stdout
    start = out.find("{")
    if start >= 0:
        try:
            env = json.loads(out[start:])
            # Video generation usually returns URL or async task ID
            url = env.get("url") or env.get("data", {}).get("url") if isinstance(env.get("data"), dict) else None
            task_id = env.get("id") or env.get("task_id")
            content = url or task_id or json.dumps(env, ensure_ascii=False)
            brief = {
                "skill": "video-generation",
                "status": "success",
                "confidence": 0.85,
                "brief": f"Video generated: {content[:200]}" if content else "(empty response)",
                "claims": [{"text": content, "source": "video-generation:z-ai-video",
                            "confidence": 0.85}],
                "metadata": {"method": "z-ai-video", "raw_env": env.get("id", "")},
            }
            print(json.dumps(brief, ensure_ascii=False, indent=2))
            sys.exit(0)
        except json.JSONDecodeError:
            pass

    brief = {
        "skill": "video-generation",
        "status": "success",
        "confidence": 0.5,
        "brief": (out or "(empty)")[:300],
        "claims": [{"text": out, "source": "video-generation:z-ai-video",
                     "confidence": 0.5}],
        "metadata": {"method": "z-ai-video", "raw": True},
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None, help="User query")
    ap.add_argument("--backend", default=None,
                    choices=["zai_cli", "sandbox", "mock"],
                    help="LLM backend: zai_cli (default), sandbox (internal agents), mock (placeholder)")
    args = ap.parse_args()
    result = run_skill("video-generation", user_query=args.query, backend=args.backend)
    sys.exit(0 if result.get("status") == "success" else 1)
