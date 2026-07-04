#!/usr/bin/env python3
"""Send the precise query to LLM via z-ai-web-dev-sdk CLI."""
import json
import subprocess
from pathlib import Path

QUERY = Path("/home/z/my-project/work/r3_research/precise_query.txt").read_text()
print(f"Query length: {len(QUERY)} chars")

# Use LLM chat
result = subprocess.run(
    ["/home/z/my-project/node_modules/.bin/z-ai", "chat",
     "--system", "You are a senior PyTorch + HuggingFace transformers engineer. Reply ONLY with concrete code, no theory, no preamble. Use Russian comments where helpful.",
     "-p", QUERY,
     "-o", "/home/z/my-project/work/r3_research/llm_answer.json"],
    capture_output=True, text=True, timeout=180
)
print("STDOUT:", result.stdout[-500:] if result.stdout else "")
print("STDERR:", result.stderr[-500:] if result.stderr else "")
print("RC:", result.returncode)

# Inspect output
out = Path("/home/z/my-project/work/r3_research/llm_answer.json")
if out.exists():
    data = json.loads(out.read_text())
    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
    print(f"\n--- LLM answer ({len(content)} chars) ---")
    print(content[:3000])
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
