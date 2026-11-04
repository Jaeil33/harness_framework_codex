"""Codex command hooks. Input and output are JSON; no shell command is evaluated here."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from harness_common import ROOT, HarnessError, configure_stdio, load_config, run_process, verify_commands


def find_dcg(explicit=None):
    configured = explicit or os.environ.get("HARNESS_DCG")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise HarnessError("DCG path must be an existing absolute executable path")
        return path.resolve()
    candidates = []
    found = shutil.which("dcg")
    if found:
        candidates.append(Path(found))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Programs/dcg/dcg.exe")
    # User-installed Python and dcg often share AppData/Local/Programs.
    for parent in Path(sys.executable).parents:
        if parent.name.lower() == "programs":
            candidates.append(parent / "dcg/dcg.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise HarnessError("dcg not found. Install dcg or pass --dcg with its absolute executable path")


def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason[-6000:]}}


def pre_tool(payload, root=ROOT, dcg=None):
    if payload.get("tool_name") not in ("Bash", "PowerShell", "exec_command", "shell", "shell_command"):
        return {}
    try:
        tool_input = payload.get("tool_input", {})
        command = tool_input.get("command", tool_input.get("cmd"))
        if not isinstance(command, str) or not command.strip():
            return deny("Harness: cannot inspect the shell command in hook input")
        normalized = {**payload, "tool_name": "Bash", "tool_input": {**tool_input, "command": command}}
        executable = find_dcg(dcg)
        result = run_process([str(executable)], cwd=root, timeout=10,
                             input_text=json.dumps(normalized, ensure_ascii=False))
        if result.returncode:
            return deny(f"Harness: dcg denied or failed: {result.stderr or result.stdout}")
        if not result.stdout.strip():
            return {}
        decision = json.loads(result.stdout)
        if not isinstance(decision, dict):
            raise HarnessError("dcg returned a non-object response")
        if decision:
            specific = decision.get("hookSpecificOutput", {})
            if not isinstance(specific, dict) or specific.get("permissionDecision") not in ("allow", "deny", "ask"):
                raise HarnessError("dcg returned an unsupported response")
        return decision
    except (HarnessError, ValueError, TypeError, AttributeError) as exc:
        return deny(f"Harness: command guard unavailable: {exc}")


def stop(payload, root=ROOT):
    # A blocked worker must be able to report missing credentials/permissions.
    try:
        final = json.loads(payload.get("last_assistant_message") or "null")
    except (TypeError, ValueError):
        final = None
    if isinstance(final, dict) and final.get("status") == "blocked" and isinstance(final.get("reason"), str) and final["reason"].strip():
        return {}
    try:
        config = load_config(root)
        result = verify_commands(config["verification_commands"], root=root,
                                 timeout=config["check_timeout_seconds"])
        if result["ok"]:
            return {}
        reason = result["error"][-6000:]
    except (HarnessError, OSError) as exc:
        reason = str(exc)
    if payload.get("stop_hook_active"):
        return {"continue": False, "stopReason": f"Harness verification still failed: {reason}",
                "systemMessage": "Harness checks failed. Do not report this task as verified."}
    return {"decision": "block", "reason": f"Harness verification failed. Fix the failure and rerun checks:\n{reason}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event", choices=("pre-tool", "stop"))
    parser.add_argument("--dcg", help="Absolute dcg executable path")
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        result = pre_tool(payload, dcg=args.dcg) if args.event == "pre-tool" else stop(payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Harness hook failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    configure_stdio()
    raise SystemExit(main())
