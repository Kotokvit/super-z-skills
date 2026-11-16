#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

def main():
    try:
        # Read Antigravity hook input from stdin
        input_raw = sys.stdin.read()
        
        # Write to debug log
        debug_log = Path("/home/vitalij/super-z-skills/.context/hook_debug.log")
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(f"Input JSON: {input_raw}\n")
            
        input_data = json.loads(input_raw)
    except Exception as e:
        # Return empty steps on parse error
        print(json.dumps({"injectSteps": []}))
        return 0

    user_message = input_data.get("userMessage") or ""
    invocation_num = input_data.get("invocationNum", 1)

    # We only inject context on the first step of the response loop (before tool usage/replies)
    # and only if there is a message to analyze.
    if invocation_num == 1 and user_message.strip():
        project_root = Path("/home/vitalij/super-z-skills")
        venv_python = project_root / ".venv" / "bin" / "python3"
        python_exec = str(venv_python) if venv_python.exists() else sys.executable
        super_z_script = project_root / "bin" / "super-z.py"

        # Start daemon if not running (matching self_context.sh behavior)
        daemon_pid_file = project_root / ".context" / "watcher_daemon.pid"
        daemon_running = False
        if daemon_pid_file.exists():
            try:
                pid = int(daemon_pid_file.read_text().strip())
                os.kill(pid, 0)
                daemon_running = True
            except Exception:
                pass
        
        if not daemon_running:
            subprocess.run([python_exec, str(super_z_script), "--daemon", "start"], capture_output=True)

        # Enqueue the user message and wait for the brief
        cmd = [python_exec, str(super_z_script), "--self-context", user_message]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        brief_output = result.stdout.strip()
        if brief_output and "(no new brief entries" not in brief_output:
            # Wrap the brief in an ephemeral system message to inject it into context
            inject_payload = {
                "injectSteps": [
                  {
                    "ephemeralMessage": f"=== SUPER-Z BACKGROUND SKILLS CONTEXT ===\n{brief_output}\n=== END CONTEXT ==="
                  }
                ]
            }
            print(json.dumps(inject_payload, ensure_ascii=False, indent=2))
            return 0

    # Default fallback: do not inject anything
    print(json.dumps({"injectSteps": []}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
