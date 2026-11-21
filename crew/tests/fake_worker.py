"""Fake worker CLI used by tests. Usage: fake_worker.py <agent> <cli args...>

Behaviour is selected by the FAKE_SCENARIO environment variable.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

agent = sys.argv[1]
args = sys.argv[2:]
scenario = os.environ.get("FAKE_SCENARIO", "done")

if "--version" in args:
    print(f"fake-{agent} 1.0")
    sys.exit(0)

RESULT_OK = "Work done.\n\n## RESULT\nSTATUS: success\nCHANGED_FILES: none\nVERIFICATION: n/a\nRISKS: none\nNEXT: none\n"

prompt = ""
if agent in ("codex", "claude"):
    prompt = sys.stdin.read()
else:
    for a in args:
        if a.startswith("--print="):
            prompt = a[len("--print="):]

text = RESULT_OK
exit_code = 0
stderr_lines = []

if scenario == "sleep":
    time.sleep(60)
elif scenario == "sleep_with_child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    print(json.dumps([os.getpid(), child.pid]), file=sys.stderr, flush=True)
    time.sleep(60)
elif scenario == "modify_sleep":
    Path("outside.txt").write_text("x", encoding="utf-8")
    time.sleep(60)
elif scenario == "read_modify" or scenario == "write_outside":
    Path("outside.txt").write_text("x", encoding="utf-8")
elif scenario == "write_inside":
    Path("src").mkdir(exist_ok=True)
    Path("src/a.txt").write_text("hello", encoding="utf-8")
elif scenario == "blocked":
    stderr_lines.append("patch rejected: writing outside of the project; rejected by user approval settings")
elif scenario == "no_result":
    text = "I did something but forgot the block."
elif scenario == "invalid_status":
    text = "x\n## RESULT\nSTATUS: maybe\n"
elif scenario == "quote_error":
    text = "The log says 'Access is denied' and 'patch rejected'.\n\n" + RESULT_OK
elif scenario == "usage_limit":
    stderr_lines.append("ERROR: You've hit your usage limit. Try again later.")
    exit_code = 1
    text = ""
elif scenario == "transient_once_slow":
    marker = Path(os.environ["FAKE_MARKER"])
    time.sleep(3)
    if not marker.exists():
        marker.write_text("1", encoding="utf-8")
        stderr_lines.append("stream error: 503 Service Unavailable")
        exit_code = 1
        text = ""
elif scenario == "transient_once":
    marker = Path(os.environ["FAKE_MARKER"])
    if not marker.exists():
        marker.write_text("1", encoding="utf-8")
        stderr_lines.append("stream error: 503 Service Unavailable")
        exit_code = 1
        text = ""
elif scenario == "env_dump":
    names = [k for k in ("GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AGENT_ROLE") if k in os.environ]
    text = "ENV: " + ",".join(names) + "\n\n" + RESULT_OK
elif scenario == "transient_write":
    Path("src").mkdir(exist_ok=True)
    Path("src/partial.txt").write_text("half", encoding="utf-8")
    stderr_lines.append("stream error: 503 Service Unavailable")
    exit_code = 1
    text = ""

for line in stderr_lines:
    print(line, file=sys.stderr)

if agent == "codex":
    print(json.dumps({"type": "thread.started", "thread_id": "fake-thread-1"}))
    if scenario == "measured":
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 80, "output_tokens": 20}}))
    if "-o" in args and text:
        Path(args[args.index("-o") + 1]).write_text(text, encoding="utf-8")
elif agent == "agy":
    if scenario == "agy_noout":
        print("jetski: no output produced — a tool required the \"command\" permission that headless mode cannot prompt for, so it was auto-denied.")
        sys.exit(0)
    print(json.dumps({"event": "init", "conversation_id": "conv-1", "init": {"model": "fake-model"}}))
    if scenario == "agy_subagent":
        print(json.dumps({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "subagent",
              "tool_name": "invoke_subagent", "subagent_info": {"subagents": [{"conversation_id": "sub-1"}]}}}))
    if text:
        print(json.dumps({"event": "step_update", "step_update": {"step_index": 2, "state": "ACTIVE",
              "step_type": "agent_response", "text_delta": text}}))
    print(json.dumps({"event": "result", "result": {"conversation_id": "conv-1", "status": "SUCCESS", "response": text}}))
elif agent == "claude":
    denials = [{"tool_name": "Write"}] if scenario == "claude_denied" else []
    print(json.dumps({"result": text, "is_error": exit_code != 0, "permission_denials": denials,
                      "subagent_stats": {"spawned": 0}, "session_id": "s-1", "total_cost_usd": 0.01,
                      "modelUsage": {"fake-claude": {}}}))

sys.exit(exit_code)
