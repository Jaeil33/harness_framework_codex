import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import hooks
import setup_codex
from harness_common import HarnessError, ProcessResult, read_json, run_process, write_json
from test_execute import ProjectTest


class HookTests(ProjectTest):
    def payload(self):
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git status"}}

    def test_guard_receives_stdin_json(self):
        with patch.object(hooks, "find_dcg", return_value=Path("guard.exe")), patch.object(
            hooks, "run_process", return_value=ProcessResult(0, "", "")
        ) as run:
            self.assertEqual(hooks.pre_tool(self.payload(), self.root), {})
        self.assertEqual(json.loads(run.call_args.kwargs["input_text"])["tool_input"]["command"], "git status")
        self.assertEqual(run.call_args.args[0], ["guard.exe"])

    def test_guard_json_denial_is_preserved(self):
        denial = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "unsafe"}}
        with patch.object(hooks, "find_dcg", return_value=Path("guard.exe")), patch.object(
            hooks, "run_process", return_value=ProcessResult(0, json.dumps(denial), "")
        ):
            self.assertEqual(hooks.pre_tool(self.payload(), self.root), denial)

    def test_missing_guard_and_runtime_errors_deny(self):
        with patch.object(hooks, "find_dcg", side_effect=HarnessError("dcg missing")):
            result = hooks.pre_tool(self.payload(), self.root)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
        with patch.object(hooks, "find_dcg", return_value=Path("guard.exe")), patch.object(
            hooks, "run_process", return_value=ProcessResult(1, "", "configuration invalid")
        ):
            result = hooks.pre_tool(self.payload(), self.root)
        self.assertIn("configuration invalid", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_malformed_guard_output_denies(self):
        with patch.object(hooks, "find_dcg", return_value=Path("guard.exe")), patch.object(
            hooks, "run_process", return_value=ProcessResult(0, "broken json", "")
        ):
            result = hooks.pre_tool(self.payload(), self.root)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_failed_stop_check_requests_continuation(self):
        with patch.object(hooks, "verify_commands", return_value={"ok": False, "error": "test failure"}):
            result = hooks.stop({}, self.root)
        self.assertEqual(result["decision"], "block")
        self.assertIn("test failure", result["reason"])

    def test_repeated_stop_failure_terminates_with_reason(self):
        with patch.object(hooks, "verify_commands", return_value={"ok": False, "error": "still failing"}):
            result = hooks.stop({"stop_hook_active": True}, self.root)
        self.assertFalse(result["continue"])
        self.assertIn("still failing", result["stopReason"])

    def test_blocked_worker_can_stop_without_unrelated_checks(self):
        payload = {"last_assistant_message": json.dumps({"status": "blocked", "summary": "", "reason": "need credentials"})}
        with patch.object(hooks, "verify_commands") as verify:
            self.assertEqual(hooks.stop(payload, self.root), {})
        verify.assert_not_called()

    def test_successful_stop_check_allows_stop(self):
        with patch.object(hooks, "verify_commands", return_value={"ok": True}):
            self.assertEqual(hooks.stop({}, self.root), {})


class SetupTests(ProjectTest):
    def setUp(self):
        super().setUp()
        (self.root / "scripts").mkdir()
        (self.root / "scripts/hooks.py").write_text("print('{}')\n", encoding="utf-8")
        self.guard = self.root / "guard.exe"
        self.guard.write_bytes(b"fixture")
        for name in setup_codex.SKILLS:
            folder = self.root / "skills" / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test skill\n---\nBody\n", encoding="utf-8")

    def test_check_is_read_only(self):
        plan = setup_codex.install(self.root, dcg=self.guard, check=True)
        self.assertFalse((self.root / ".codex").exists())
        self.assertFalse((self.root / ".agents").exists())
        self.assertFalse(plan["installed"])

    def test_install_preserves_other_hooks_and_is_idempotent(self):
        unrelated = {"hooks": [{"type": "command", "command": "echo custom"}]}
        destination = self.root / ".codex/hooks.json"
        write_json(destination, {"hooks": {"Stop": [unrelated]}})
        setup_codex.install(self.root, dcg=self.guard)
        first = read_json(destination)
        setup_codex.install(self.root, dcg=self.guard)
        self.assertEqual(first, read_json(destination))
        self.assertIn(unrelated, first["hooks"]["Stop"])
        self.assertEqual(len(first["hooks"]["Stop"]), 2)

    def test_changed_existing_skill_is_preserved(self):
        destination = self.root / ".agents/skills/harness/SKILL.md"
        destination.parent.mkdir(parents=True)
        destination.write_text("user content", encoding="utf-8")
        with self.assertRaisesRegex(HarnessError, "Existing skill"):
            setup_codex.install(self.root, dcg=self.guard)
        self.assertEqual(destination.read_text(encoding="utf-8"), "user content")
        self.assertFalse((self.root / ".codex/hooks.json").exists())

    def test_invalid_existing_hooks_are_not_overwritten(self):
        destination = self.root / ".codex/hooks.json"
        write_json(destination, {"hooks": {"Stop": [{"hooks": ["invalid"]}]}})
        original = destination.read_bytes()
        with self.assertRaises(HarnessError):
            setup_codex.install(self.root, dcg=self.guard)
        self.assertEqual(destination.read_bytes(), original)

    def test_windows_hook_command_runs_from_subdirectory(self):
        if sys.platform != "win32":
            self.skipTest("Windows command format")
        config = setup_codex.build_hooks(self.root, sys.executable, self.guard)
        command = config["hooks"]["Stop"][0]["hooks"][0]["commandWindows"]
        result = run_process(["cmd.exe", "/d", "/s", "/c", command], cwd=self.phase,
                             timeout=15, input_text="{}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})


if __name__ == "__main__":
    unittest.main()
