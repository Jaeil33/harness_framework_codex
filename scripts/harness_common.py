"""Shared UTF-8 I/O, command execution and verification for the Codex harness."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent


def configure_stdio():
    """The CLI and hook protocol are UTF-8 even on Windows with a legacy locale."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


class HarnessError(Exception):
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"Cannot read JSON {path}: {exc}") from exc


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise HarnessError(f"Path must remain inside the project: {relative}")
    return path


def validate_commands(commands, label):
    if not isinstance(commands, list) or not commands:
        raise HarnessError(f"{label}: expected a nonempty list of argument arrays")
    for command in commands:
        if not isinstance(command, list) or not command or any(
            not isinstance(arg, str) or not arg or "\0" in arg for arg in command
        ):
            raise HarnessError(f"{label}: commands must be nonempty string argument arrays")


def load_config(root):
    config = read_json(Path(root) / "harness.json")
    if not isinstance(config, dict):
        raise HarnessError("harness.json must contain an object")
    validate_commands(config.get("verification_commands"), "verification_commands")
    for field in ("max_attempts", "codex_timeout_seconds", "check_timeout_seconds"):
        value = config.get(field)
        if type(value) is not int or value < 1:
            raise HarnessError(f"{field} must be a positive integer")
    files = config.get("context_files")
    if not isinstance(files, list) or not files or any(not isinstance(p, str) for p in files):
        raise HarnessError("context_files must be a nonempty list of project-relative paths")
    for name in files:
        if not inside(root, name).is_file():
            raise HarnessError(f"Missing context file: {name}")
    return config


def resolve_command(argv):
    """Resolve npm's Windows shims without passing a prompt through cmd.exe."""
    argv = list(argv)
    if argv[0] in ("python", "python3"):
        return [sys.executable, *argv[1:]]
    found = shutil.which(argv[0])
    if found is None and Path(argv[0]).is_file():
        found = str(Path(argv[0]).resolve())
    if found is None:
        raise HarnessError(f"Executable not found: {argv[0]}")
    executable = Path(found)
    if os.name == "nt" and executable.suffix.lower() in (".cmd", ".bat", ".ps1"):
        scripts = {
            "codex": "node_modules/@openai/codex/bin/codex.js",
            "npm": "node_modules/npm/bin/npm-cli.js",
            "npx": "node_modules/npm/bin/npx-cli.js",
        }
        relative = scripts.get(executable.stem.lower())
        node = shutil.which("node")
        if relative and node and (executable.parent / relative).is_file():
            return [node, str(executable.parent / relative), *argv[1:]]
        raise HarnessError(f"Use a native executable or an explicit interpreter for {executable}")
    return [str(executable), *argv[1:]]


@dataclass
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    elapsed_seconds: float = 0.0


def _stop_process_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run_process(command, *, cwd, timeout, input_text=None, env=None):
    child_env = os.environ.copy()
    child_env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    if env:
        child_env.update(env)
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    started = time.monotonic()
    try:
        process = subprocess.Popen(command, cwd=cwd, env=child_env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", **options)
    except OSError as exc:
        return ProcessResult(127, stderr=str(exc), elapsed_seconds=time.monotonic() - started)
    timed_out = False
    try:
        stdout, stderr = process.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_process_tree(process)
        stdout, stderr = process.communicate()
        stderr += f"\nCommand timed out after {timeout}s"
    except BaseException:
        _stop_process_tree(process)
        process.communicate()
        raise
    return ProcessResult(124 if timed_out else process.returncode, stdout, stderr,
                         timed_out, time.monotonic() - started)


def verify_commands(commands, *, root, timeout):
    records = []
    for argv in commands:
        try:
            result = run_process(resolve_command(argv), cwd=root, timeout=timeout)
        except HarnessError as exc:
            result = ProcessResult(127, stderr=str(exc))
        records.append({"argv": argv, **asdict(result)})
        if result.returncode != 0:
            return {"ok": False, "checks": records,
                    "error": f"Check failed: {argv!r}\n{result.stderr}\n{result.stdout}"[-12000:]}
    return {"ok": True, "checks": records, "error": ""}
