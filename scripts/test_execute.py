"""Behavioral tests: no model calls or third-party packages."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import execute as ex
from harness_common import HarnessError, ProcessResult, read_json, write_json, run_process


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-harness-test-")
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.temp.cleanup)
        (self.root / "agent.md").write_text("# 규칙\n한글과 UTF-8", encoding="utf-8")
        (self.root / "schemas").mkdir()
        write_json(self.root / "schemas/step-result.schema.json", {})
        self.phase = self.root / "phases/0-demo"
        self.phase.mkdir(parents=True)
        self.step = {"step": 0, "name": "demo", "status": "pending",
                     "checks": [["python", "-c", "print('ok')"]]}
        self.index = {"project": "테스트", "phase": "0-demo", "steps": [self.step]}
        write_json(self.phase / "index.json", self.index)
        write_json(self.root / "phases/index.json", {"phases": [{"dir": "0-demo", "status": "pending"}]})
        (self.phase / "step0.md").write_text("# 작업\n기능을 구현한다.", encoding="utf-8")
        write_json(self.root / "harness.json", {
            "context_files": ["agent.md"], "verification_commands": [["python", "-c", "print('global')"]],
            "max_attempts": 3, "codex_timeout_seconds": 30, "check_timeout_seconds": 10,
        })

    def executor(self, **kwargs):
        obj = ex.StepExecutor("0-demo", root=self.root, **kwargs)
        obj._commit_step = Mock()
        obj._git = Mock(return_value="head")
        return obj

    def success(self, **changes):
        result = {"exit_code": 0, "timed_out": False, "stderr": "", "error": "",
                  "result": {"status": "completed", "summary": "구현 완료", "reason": ""}}
        result.update(changes)
        return result


class ExecutionTests(ProjectTest):
    def test_utf8_context_and_json(self):
        self.assertIn("한글", self.executor()._load_guardrails())
        self.assertEqual(read_json(self.phase / "index.json")["project"], "테스트")

    def test_nonzero_exit_is_not_success(self):
        obj = self.executor(max_attempts=1)
        obj._invoke_codex = Mock(return_value=self.success(exit_code=1, stderr="auth failed"))
        obj._verify = Mock(return_value={"ok": True, "checks": []})
        with self.assertRaises(HarnessError):
            obj._execute_single_step(obj.index["steps"][0], "")
        self.assertEqual(obj.index["steps"][0]["status"], "error")
        obj._verify.assert_not_called()
        obj._commit_step.assert_not_called()

    def test_checks_must_pass_before_completion(self):
        obj = self.executor(max_attempts=1)
        obj._invoke_codex = Mock(return_value=self.success())
        obj._verify = Mock(return_value={"ok": False, "error": "test failed", "checks": []})
        with self.assertRaisesRegex(HarnessError, "test failed"):
            obj._execute_single_step(obj.index["steps"][0], "")
        obj._commit_step.assert_not_called()

    def test_retry_receives_real_error(self):
        obj = self.executor()
        obj._invoke_codex = Mock(side_effect=[
            self.success(exit_code=1, stderr="specific authentication error"), self.success()])
        obj._verify = Mock(return_value={"ok": True, "checks": []})
        obj._execute_single_step(obj.index["steps"][0], "")
        self.assertIn("specific authentication error", obj._invoke_codex.call_args_list[1].args[1])
        self.assertEqual(obj.index["steps"][0]["status"], "completed")
        obj._commit_step.assert_called_once()

    def test_timeout_retries_and_records_error(self):
        obj = self.executor(max_attempts=2)
        obj._invoke_codex = Mock(return_value=self.success(
            exit_code=124, timed_out=True, error="Codex timed out"))
        with self.assertRaisesRegex(HarnessError, "timed out"):
            obj._execute_single_step(obj.index["steps"][0], "")
        self.assertEqual(obj._invoke_codex.call_count, 2)
        self.assertEqual(read_json(self.phase / "index.json")["steps"][0]["status"], "error")

    def test_blocked_stops_without_retry_or_commit(self):
        obj = self.executor()
        obj._invoke_codex = Mock(return_value=self.success(
            result={"status": "blocked", "summary": "", "reason": "API credential needed"}))
        with self.assertRaises(HarnessError) as caught:
            obj._execute_single_step(obj.index["steps"][0], "")
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertEqual(obj._invoke_codex.call_count, 1)
        obj._commit_step.assert_not_called()
        self.assertEqual(read_json(self.root / "phases/index.json")["phases"][0]["status"], "blocked")

    def test_commit_failure_is_not_completed(self):
        obj = self.executor()
        obj._invoke_codex = Mock(return_value=self.success())
        obj._verify = Mock(return_value={"ok": True, "checks": []})
        obj._commit_step.side_effect = HarnessError("commit failed")
        with self.assertRaisesRegex(HarnessError, "commit failed"):
            obj._execute_single_step(obj.index["steps"][0], "")
        self.assertEqual(read_json(self.phase / "index.json")["steps"][0]["status"], "error")

    def test_worker_cannot_mark_its_own_completion(self):
        obj = self.executor(max_attempts=1)
        def tamper(*args):
            data = read_json(self.phase / "index.json")
            data["steps"][0]["status"] = "completed"
            write_json(self.phase / "index.json", data)
            return self.success()
        obj._invoke_codex = Mock(side_effect=tamper)
        with self.assertRaisesRegex(HarnessError, "index"):
            obj._execute_single_step(obj.index["steps"][0], "")
        obj._commit_step.assert_not_called()

    def test_finalize_rejects_unfinished_steps(self):
        obj = self.executor()
        for status in ("pending", "running", "error", "blocked"):
            with self.subTest(status=status):
                obj.index["steps"][0]["status"] = status
                with self.assertRaises(HarnessError):
                    obj._finalize()

    def test_all_blockers_are_checked(self):
        obj = self.executor()
        obj.index["steps"] = [{"step": 0, "status": "error"}, {"step": 1, "status": "completed"}]
        with self.assertRaises(HarnessError):
            obj._check_blockers()

    def test_resume_resets_stale_status_fields(self):
        self.index["steps"][0].update(status="blocked", blocked_reason="key", blocked_at="old")
        write_json(self.phase / "index.json", self.index)
        obj = self.executor(resume=True)
        obj._check_blockers()
        self.assertEqual(obj.index["steps"][0]["status"], "pending")
        self.assertNotIn("blocked_reason", obj.index["steps"][0])

    def test_validation_rejects_missing_checks(self):
        del self.index["steps"][0]["checks"]
        write_json(self.phase / "index.json", self.index)
        with self.assertRaisesRegex(HarnessError, "checks"):
            self.executor()

    def test_validation_rejects_path_escape(self):
        with self.assertRaises(HarnessError):
            ex.StepExecutor("../outside", root=self.root)

    def test_validation_rejects_duplicate_numbers(self):
        self.index["steps"].append(copy.deepcopy(self.step))
        write_json(self.phase / "index.json", self.index)
        with self.assertRaises(HarnessError):
            self.executor()

    def test_validation_rejects_malformed_status_and_attempts(self):
        for field, value in (("status", []), ("attempts", "one"), ("attempts", -1)):
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(self.index)
                data["steps"][0][field] = value
                write_json(self.phase / "index.json", data)
                with self.assertRaises(HarnessError):
                    self.executor()

    def test_atomic_write_preserves_old_index_on_replace_failure(self):
        original = (self.phase / "index.json").read_bytes()
        with patch("harness_common.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                write_json(self.phase / "index.json", {"new": True})
        self.assertEqual((self.phase / "index.json").read_bytes(), original)
        self.assertFalse(list(self.phase.glob("*.tmp")))

    def test_dry_run_does_not_write_or_launch(self):
        obj = self.executor()
        before = (self.phase / "index.json").read_bytes()
        with patch.object(obj, "_invoke_codex") as invoke:
            obj.run(dry_run=True)
        invoke.assert_not_called()
        obj._git.assert_not_called()
        self.assertEqual((self.phase / "index.json").read_bytes(), before)

    def test_invocation_uses_stdin_and_preserves_attempts(self):
        obj = self.executor()
        obj.codex_command = ["fake-codex"]
        events = '\n'.join([json.dumps({"type": "thread.started"}), json.dumps({"type": "turn.completed"})])
        def launch(cmd, **kwargs):
            destination = Path(cmd[cmd.index("--output-last-message") + 1])
            write_json(destination, {"status": "completed", "summary": "결과", "reason": ""})
            self.assertEqual(kwargs["input_text"], "prompt with $() and quotes")
            self.assertNotIn(kwargs["input_text"], cmd)
            self.assertIn("--sandbox", cmd)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
            return ProcessResult(0, events, "")
        with patch.object(ex, "run_process", side_effect=launch):
            first = obj._invoke_codex(self.step, "prompt with $() and quotes", 1)
            second = obj._invoke_codex(self.step, "prompt with $() and quotes", 2)
        self.assertEqual(first["result"]["summary"], "결과")
        self.assertFalse(second["error"])
        self.assertEqual(len(list(self.phase.glob("step0-attempt*-output.json"))), 2)

    def test_jsonl_failure_overrides_valid_final_result(self):
        obj = self.executor()
        obj.codex_command = ["fake-codex"]
        def launch(cmd, **kwargs):
            write_json(Path(cmd[cmd.index("--output-last-message") + 1]),
                       {"status": "completed", "summary": "done", "reason": ""})
            return ProcessResult(0, '{"type":"turn.failed","error":{"message":"rate limit"}}', "")
        with patch.object(ex, "run_process", side_effect=launch):
            result = obj._invoke_codex(self.step, "prompt", 1)
        self.assertIn("rate limit", result["error"])

    def test_missing_result_is_not_success(self):
        obj = self.executor()
        obj.codex_command = ["fake-codex"]
        with patch.object(ex, "run_process", return_value=ProcessResult(0, '{"type":"turn.completed"}', "")):
            result = obj._invoke_codex(self.step, "prompt", 1)
        self.assertTrue(result["error"])


class ProcessTests(unittest.TestCase):
    def test_real_utf8_subprocess(self):
        result = run_process([sys.executable, "-c", "print(chr(0xd55c))"], cwd=Path.cwd(), timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "한")

    def test_real_timeout(self):
        result = run_process([sys.executable, "-c", "import time; time.sleep(10)"],
                             cwd=Path.cwd(), timeout=0.15)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 124)


if __name__ == "__main__":
    unittest.main()
