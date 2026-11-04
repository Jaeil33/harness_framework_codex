#!/usr/bin/env python3
"""Run a phase with isolated Codex sessions and independently verified results."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

from harness_common import (ROOT, HarnessError, configure_stdio, inside, load_config, read_json,
                            resolve_command, run_process, validate_commands,
                            verify_commands, write_json)

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STATUSES = {"pending", "running", "completed", "error", "blocked"}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextlib.contextmanager
def execution_lock(root):
    path = root / ".harness-run.lock"
    try:
        stream = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise HarnessError(f"Another run owns {path}. If it crashed, verify its PID is no longer running before removing the lock.") from exc
    try:
        with stream:
            json.dump({"pid": os.getpid(), "started_at": stamp()}, stream)
        yield
    finally:
        path.unlink(missing_ok=True)


class StepExecutor:
    def __init__(self, phase_dir, *, root=ROOT, auto_push=False, resume=False,
                 model=None, codex=None, timeout=None, max_attempts=None):
        self.root = Path(root).resolve()
        if not SLUG.fullmatch(phase_dir):
            raise HarnessError("Phase must be a directory slug, for example 0-mvp")
        self.phase_name = phase_dir
        self.phase_dir = inside(self.root, f"phases/{phase_dir}")
        self.index_file = self.phase_dir / "index.json"
        self.top_file = self.root / "phases/index.json"
        self.schema_file = self.root / "schemas/step-result.schema.json"
        self.config = load_config(self.root)
        self.index = read_json(self.index_file)
        self.auto_push, self.resume, self.model = auto_push, resume, model
        self.codex_executable = codex or "codex"
        self.codex_command = None
        self.timeout = timeout if timeout is not None else self.config["codex_timeout_seconds"]
        self.max_attempts = max_attempts if max_attempts is not None else self.config["max_attempts"]
        if self.timeout < 1 or self.max_attempts < 1:
            raise HarnessError("Timeout and max-attempts must be positive")
        self._validate_index()

    def _validate_index(self):
        idx = self.index
        if not isinstance(idx, dict) or idx.get("phase") != self.phase_name:
            raise HarnessError("index.json phase must equal its directory name")
        if not isinstance(idx.get("project"), str) or not idx["project"].strip():
            raise HarnessError("index.json needs a project name")
        steps = idx.get("steps")
        if not isinstance(steps, list) or not steps:
            raise HarnessError("index.json needs at least one step")
        unfinished = False
        for number, step in enumerate(steps):
            if not isinstance(step, dict) or type(step.get("step")) is not int or step["step"] != number:
                raise HarnessError("Step numbers must be unique, ordered and start at zero")
            if not isinstance(step.get("name"), str) or not SLUG.fullmatch(step["name"]):
                raise HarnessError(f"Step {number}: name must be a kebab-case slug")
            if not isinstance(step.get("status"), str) or step["status"] not in STATUSES:
                raise HarnessError(f"Step {number}: invalid status")
            if type(step.get("attempts", 0)) is not int or step.get("attempts", 0) < 0:
                raise HarnessError(f"Step {number}: attempts must be a nonnegative integer")
            if step["status"] == "completed" and unfinished:
                raise HarnessError("A completed step follows an unfinished step; repair the index before running")
            unfinished |= step["status"] != "completed"
            validate_commands(step.get("checks"), f"Step {number} checks")
            if not (self.phase_dir / f"step{number}.md").is_file():
                raise HarnessError(f"Missing step{number}.md")
        if not self.schema_file.is_file():
            raise HarnessError(f"Missing {self.schema_file}")
        if self.top_file.exists():
            top = read_json(self.top_file)
            if not isinstance(top, dict) or not isinstance(top.get("phases"), list):
                raise HarnessError("phases/index.json needs a phases array")
            if any(not isinstance(p, dict) or not isinstance(p.get("dir"), str) for p in top["phases"]):
                raise HarnessError("Invalid top-level phase entry")
            if len({p["dir"] for p in top["phases"]}) != len(top["phases"]):
                raise HarnessError("Duplicate phase entries")

    def _save(self):
        write_json(self.index_file, self.index)

    def _update_top_index(self, status):
        top = read_json(self.top_file) if self.top_file.exists() else {"phases": []}
        entry = next((p for p in top["phases"] if p["dir"] == self.phase_name), None)
        if entry is None:
            entry = {"dir": self.phase_name}
            top["phases"].append(entry)
        entry["status"] = status
        for key in ("completed_at", "failed_at", "blocked_at"):
            entry.pop(key, None)
        field = {"completed": "completed_at", "error": "failed_at", "blocked": "blocked_at"}.get(status)
        if field:
            entry[field] = stamp()
        write_json(self.top_file, top)

    def _git_result(self, *args):
        return run_process(["git", *args], cwd=self.root, timeout=120)

    def _git(self, *args):
        result = self._git_result(*args)
        if result.returncode:
            raise HarnessError(f"git {' '.join(args)} failed:\n{result.stderr or result.stdout}")
        return result.stdout.strip()

    def _require_clean_worktree(self):
        self._git("rev-parse", "--verify", "HEAD")
        if self._git("status", "--porcelain"):
            raise HarnessError("Working tree is dirty. Review and commit your changes before execution/resume; the harness will not include existing edits automatically.")

    def _checkout_branch(self):
        branch = f"feat-{self.phase_name}"
        if self._git("branch", "--show-current") == branch:
            return
        existing = self._git("branch", "--list", branch)
        self._git("checkout", branch) if existing else self._git("checkout", "-b", branch)

    def _commit_staged(self, message):
        result = self._git_result("diff", "--cached", "--quiet")
        if result.returncode == 1:
            self._git("commit", "-m", message)
        elif result.returncode != 0:
            raise HarnessError(f"Cannot inspect staged changes: {result.stderr}")

    def _commit_step(self, step):
        self._git("add", "-A", "--", ".")
        self._git("reset", "-q", "HEAD", "--",
                  self.index_file.relative_to(self.root).as_posix(),
                  self.top_file.relative_to(self.root).as_posix())
        self._commit_staged(f"feat({self.phase_name}): step {step['step']} - {step['name']}")
        self._commit_metadata(f"chore({self.phase_name}): step {step['step']} verified")

    def _commit_metadata(self, message):
        self._git("add", "--", self.index_file.relative_to(self.root).as_posix(),
                  self.top_file.relative_to(self.root).as_posix())
        self._commit_staged(message)

    def _check_blockers(self):
        for step in self.index["steps"]:
            if step["status"] in ("error", "blocked", "running"):
                if not self.resume:
                    raise HarnessError(f"Step {step['step']} is {step['status']}. Resolve the cause, review/commit the working tree and use --resume.",
                                       2 if step["status"] == "blocked" else 1)
                step["status"] = "pending"
                for key in ("error_message", "blocked_reason", "failed_at", "blocked_at", "completed_at"):
                    step.pop(key, None)

    def _load_guardrails(self):
        return "\n\n---\n\n".join(
            f"## {name}\n\n{inside(self.root, name).read_text(encoding='utf-8-sig')}"
            for name in self.config["context_files"]
        )

    def _build_preamble(self, guardrails, previous_error=""):
        context = "\n".join(
            f"- Step {s['step']} ({s['name']}): {s.get('summary', '')}"
            for s in self.index["steps"] if s["status"] == "completed"
        )
        return (
            f"당신은 {self.index['project']} 프로젝트의 Codex 개발자입니다.\n\n"
            f"{guardrails}\n\n## 이전 Step 산출물\n{context}\n\n"
            "## 실행 계약\n"
            "- 아래 Step의 작업만 수행하고 관련 기존 코드를 먼저 읽는다.\n"
            "- 테스트를 작성하고 AC를 실행한다. 기존 테스트를 약화시키지 않는다.\n"
            "- Git 커밋, 브랜치 변경, push는 하지 않는다. 부모 실행기가 처리한다.\n"
            "- phases/**/index.json, Step 명세, harness.json, 결과 스키마는 수정하지 않는다.\n"
            "- 다른 실행기나 Codex 작업을 재귀적으로 시작하지 않는다.\n"
            "- 완료 상태는 부모가 검증 후 기록한다. 지정된 JSON 스키마로 결과만 반환한다.\n"
            "- status=completed에는 산출물을 summary에, error/blocked에는 원인을 reason에 쓴다.\n"
            "- 인증, 권한, 외부 설정이 필요하면 status=blocked로 즉시 종료한다.\n"
            f"\n## 이전 시도 오류\n{previous_error[-12000:] or '없음'}\n\n"
        )

    @staticmethod
    def _validate_result(result):
        if not isinstance(result, dict) or set(result) != {"status", "summary", "reason"}:
            raise HarnessError("Final result must contain status, summary and reason")
        if result["status"] not in ("completed", "error", "blocked") or any(
            not isinstance(result[k], str) for k in ("summary", "reason")
        ):
            raise HarnessError("Invalid final result fields")
        required = "summary" if result["status"] == "completed" else "reason"
        if not result[required].strip():
            raise HarnessError(f"Final result needs a nonempty {required}")
        return result

    def _invoke_codex(self, step, prompt, attempt):
        prefix = self.phase_dir / f"step{step['step']}-attempt{attempt}"
        result_file = Path(f"{prefix}-result.json")
        if result_file.exists() or Path(f"{prefix}-output.json").exists():
            raise HarnessError(f"Attempt artifacts already exist: {prefix}")
        command = [*self.codex_command, "exec", "--sandbox", "workspace-write",
                   "--json", "--output-schema", str(self.schema_file),
                   "--output-last-message", str(result_file)]
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")
        result = run_process(command, cwd=self.root, timeout=self.timeout, input_text=prompt)
        output = {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
                  "timed_out": result.timed_out, "elapsed_seconds": result.elapsed_seconds,
                  "events": [], "result": None, "error": ""}
        try:
            if result.returncode:
                raise HarnessError(result.stderr or f"Codex exited with code {result.returncode}")
            for line in result.stdout.splitlines():
                if line.strip():
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise HarnessError("Invalid Codex JSONL event")
                    output["events"].append(event)
            failures = [e for e in output["events"] if e.get("type") in ("error", "turn.failed")]
            if failures:
                raise HarnessError(json.dumps(failures, ensure_ascii=False))
            if not any(e.get("type") == "turn.completed" for e in output["events"]):
                raise HarnessError("Codex did not emit turn.completed")
            output["result"] = self._validate_result(read_json(result_file))
        except (HarnessError, ValueError) as exc:
            output["error"] = str(exc)
        write_json(Path(f"{prefix}-output.json"), output)
        return output

    def _verify(self, step):
        commands = []
        for command in [*step["checks"], *self.config["verification_commands"]]:
            if command not in commands:
                commands.append(command)
        return verify_commands(commands, root=self.root, timeout=self.config["check_timeout_seconds"])

    def _next_attempt(self, step):
        values = [int(m.group(1)) for p in self.phase_dir.glob(f"step{step['step']}-attempt*-*.json")
                  if (m := re.search(r"-attempt(\d+)-", p.name))]
        return max([step.get("attempts", 0), *values]) + 1

    def _contract_snapshot(self):
        files = [self.index_file, self.top_file, self.schema_file, self.root / "harness.json"]
        files.extend(self.phase_dir / f"step{s['step']}.md" for s in self.index["steps"])
        return {p: p.read_bytes() if p.exists() else None for p in files}

    def _check_contract(self, snapshot, head, branch):
        for path, original in snapshot.items():
            current = path.read_bytes() if path.exists() else None
            if current != original:
                raise HarnessError(f"Worker/check modified protected contract: {path.relative_to(self.root)}")
        if self._git("rev-parse", "HEAD") != head or self._git("branch", "--show-current") != branch:
            raise HarnessError("Worker/check changed Git HEAD or branch; inspect the repository before resuming")

    def _fail_step(self, step, reason, status="error"):
        step["status"] = status
        step.pop("completed_at", None)
        step["blocked_reason" if status == "blocked" else "error_message"] = reason
        step["blocked_at" if status == "blocked" else "failed_at"] = stamp()
        self.index["status"] = status
        self._save()
        self._update_top_index(status)

    def _execute_single_step(self, step, guardrails):
        previous_error = ""
        for retry in range(self.max_attempts):
            attempt = self._next_attempt(step)
            step.update(status="running", attempts=attempt)
            step.setdefault("started_at", stamp())
            self._save()
            snapshot = self._contract_snapshot()
            head, branch = self._git("rev-parse", "HEAD"), self._git("branch", "--show-current")
            print(f"Step {step['step']}: {step['name']} (attempt {attempt})", flush=True)
            prompt = self._build_preamble(guardrails, previous_error)
            prompt += (self.phase_dir / f"step{step['step']}.md").read_text(encoding="utf-8-sig")
            try:
                output = self._invoke_codex(step, prompt, attempt)
                self._check_contract(snapshot, head, branch)
            except (HarnessError, OSError) as exc:
                self._fail_step(step, str(exc))
                raise HarnessError(str(exc)) from exc
            error = output.get("error") or ""
            if output["exit_code"] or output.get("timed_out"):
                error = error or output.get("stderr") or f"Codex exited with code {output['exit_code']}"
            result = output.get("result")
            if not error:
                try:
                    self._validate_result(result)
                except HarnessError as exc:
                    error = str(exc)
            if not error and result["status"] == "blocked":
                self._fail_step(step, result["reason"], "blocked")
                raise HarnessError(result["reason"], 2)
            if not error and result["status"] == "error":
                error = result["reason"]
            if not error:
                verification = self._verify(step)
                write_json(self.phase_dir / f"step{step['step']}-attempt{attempt}-checks.json", verification)
                try:
                    self._check_contract(snapshot, head, branch)
                except HarnessError as exc:
                    self._fail_step(step, str(exc))
                    raise
                if not verification["ok"]:
                    error = verification["error"]
            if not error:
                step.update(status="completed", summary=result["summary"], completed_at=stamp())
                for key in ("error_message", "failed_at", "blocked_reason", "blocked_at"):
                    step.pop(key, None)
                self._save()
                try:
                    self._commit_step(step)
                except HarnessError as exc:
                    self._fail_step(step, str(exc))
                    raise
                print(f"Verified step {step['step']}.", flush=True)
                return
            previous_error = error
            if retry + 1 == self.max_attempts:
                self._fail_step(step, error)
                raise HarnessError(error)
            print(f"Retrying: {error[-1500:]}", flush=True)

    def _finalize(self):
        if any(s["status"] != "completed" for s in self.index["steps"]):
            raise HarnessError("Cannot finalize: not every step is completed")
        self.index.update(status="completed", completed_at=stamp())
        self._save()
        self._update_top_index("completed")
        self._commit_metadata(f"chore({self.phase_name}): mark phase completed")
        if self.auto_push:
            self._git("push", "-u", "origin", f"feat-{self.phase_name}")
        print(f"Phase {self.phase_name} completed.")

    def run(self, *, dry_run=False):
        guardrails = self._load_guardrails()
        if dry_run:
            print(f"Valid phase: {self.phase_name}; {len(self.index['steps'])} steps")
            for step in self.index["steps"]:
                print(f"  {step['step']} {step['name']}: {step['status']}; checks={step['checks']!r}")
            print(f"Global checks: {self.config['verification_commands']!r}")
            return
        self.codex_command = resolve_command([self.codex_executable])
        with execution_lock(self.root):
            self._check_blockers()
            self._require_clean_worktree()
            self._checkout_branch()
            self.index.setdefault("created_at", stamp())
            self.index["status"] = "running"
            self.index.pop("completed_at", None)
            self._save()
            self._update_top_index("running")
            try:
                for step in self.index["steps"]:
                    if step["status"] != "completed":
                        self._execute_single_step(step, guardrails)
                self._finalize()
            except KeyboardInterrupt:
                self.index["status"] = "error"
                self._save()
                self._update_top_index("error")
                raise
            except HarnessError:
                if self.index.get("status") not in ("error", "blocked"):
                    self.index["status"] = "error"
                    self.index.pop("completed_at", None)
                    self._save()
                    self._update_top_index("error")
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase_dir", help="Directory under phases/, for example 0-smoke")
    parser.add_argument("--dry-run", action="store_true", help="Validate files without model calls or Git changes")
    parser.add_argument("--resume", action="store_true", help="Retry unfinished steps after resolving their cause")
    parser.add_argument("--push", action="store_true", help="Push the completed phase branch")
    parser.add_argument("--model", help="Optional Codex model; defaults to your Codex configuration")
    parser.add_argument("--codex", help="Codex executable path (npm Windows shims are supported)")
    parser.add_argument("--timeout", type=int, help="Seconds per Codex invocation")
    parser.add_argument("--max-attempts", type=int, help="Total attempts per step, including the first")
    args = parser.parse_args(argv)
    try:
        StepExecutor(args.phase_dir, auto_push=args.push, resume=args.resume, model=args.model,
                     codex=args.codex, timeout=args.timeout, max_attempts=args.max_attempts).run(dry_run=args.dry_run)
        return 0
    except HarnessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Review the working tree before --resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    configure_stdio()
    raise SystemExit(main())
