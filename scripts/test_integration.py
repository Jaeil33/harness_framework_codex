"""Exercise real subprocess, UTF-8, JSONL and Git boundaries without a model service."""
import contextlib
import io
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

import execute as ex
from harness_common import ROOT, HarnessError, read_json, run_process, write_json
from test_execute import ProjectTest

FAKE_CODEX = '''import json
from pathlib import Path
import sys
args = sys.argv[1:]
assert args[0] == "exec"
assert args[-1] == "-"
assert "--sandbox" in args and args[args.index("--sandbox") + 1] == "workspace-write"
prompt = sys.stdin.read()
assert "UTF-8" in prompt
Path("artifact.txt").write_text("한글 완료\\n", encoding="utf-8")
destination = Path(args[args.index("--output-last-message") + 1])
destination.write_text(json.dumps({"status":"completed", "summary":"한글 파일 생성", "reason":""}, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"type":"thread.started", "thread_id":"test"}))
print(json.dumps({"type":"turn.completed", "usage":{"input_tokens":1, "output_tokens":1}}))
if Path("fail-process").exists():
    print("simulated process failure", file=sys.stderr)
    sys.exit(9)
'''


class IntegrationTests(ProjectTest):
    def setUp(self):
        super().setUp()
        if not shutil.which("git"):
            self.skipTest("Git not installed")
        (self.root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
        (self.root / "schemas/step-result.schema.json").write_bytes((ROOT / "schemas/step-result.schema.json").read_bytes())
        self.fake = self.root / "fake_codex.py"
        self.fake.write_text(FAKE_CODEX, encoding="utf-8")
        self.index["steps"][0]["checks"] = [["python", "-c", "from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8') == '한글 완료\\n'"]]
        write_json(self.phase / "index.json", self.index)
        self.git("init", "-q")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        self.git("add", "-A")
        self.git("commit", "-qm", "test: initial fixture")

    def git(self, *args):
        result = run_process(["git", *args], cwd=self.root, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def run_phase(self, **kwargs):
        obj = ex.StepExecutor("0-demo", root=self.root, **kwargs)
        with patch.object(ex, "resolve_command", return_value=[sys.executable, str(self.fake)]), contextlib.redirect_stdout(io.StringIO()):
            obj.run()
        return obj

    def test_real_process_and_git_complete_phase(self):
        self.run_phase()
        self.assertEqual(self.git("branch", "--show-current"), "feat-0-demo")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(read_json(self.phase / "index.json")["status"], "completed")
        self.assertEqual(read_json(self.root / "phases/index.json")["phases"][0]["status"], "completed")
        output = read_json(self.phase / "step0-attempt1-output.json")
        self.assertEqual(output["result"]["summary"], "한글 파일 생성")
        self.assertEqual(output["events"][-1]["type"], "turn.completed")
        commits = self.git("log", "--format=%s")
        self.assertIn("feat(0-demo): step 0", commits)
        self.assertIn("chore(0-demo): step 0 verified", commits)
        tracked = self.git("ls-files")
        self.assertNotIn("attempt1-output.json", tracked)
        self.assertNotIn(".harness-run.lock", tracked)

    def test_dirty_tree_is_preserved_without_model_launch(self):
        (self.root / "user-work.txt").write_text("existing work", encoding="utf-8")
        original_head = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(HarnessError, "dirty"):
            self.run_phase()
        self.assertFalse((self.root / "artifact.txt").exists())
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual((self.root / "user-work.txt").read_text(), "existing work")
        self.assertFalse((self.root / ".harness-run.lock").exists())

    def test_failed_process_never_commits_success_and_can_resume(self):
        (self.root / "fail-process").write_text("fixture", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "test: failure mode")
        original_head = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(HarnessError, "simulated process failure"):
            self.run_phase(max_attempts=1)
        self.assertEqual(self.git("rev-parse", "HEAD"), original_head)
        self.assertEqual(read_json(self.phase / "index.json")["steps"][0]["status"], "error")
        self.assertFalse((self.root / ".harness-run.lock").exists())
        (self.root / "fail-process").unlink()
        self.git("add", "-A")
        self.git("commit", "-qm", "test: reviewed recovery checkpoint")
        self.run_phase(resume=True)
        self.assertEqual(read_json(self.phase / "index.json")["status"], "completed")
        self.assertTrue((self.phase / "step0-attempt1-output.json").exists())
        self.assertTrue((self.phase / "step0-attempt2-output.json").exists())


if __name__ == "__main__":
    unittest.main()
