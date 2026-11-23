"""Project-local query execution and records for the trace viewer."""

from __future__ import annotations

import codecs
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, time_ns
from uuid import uuid4

from crew import contract, runner, safety
from crew.common import EXIT_GUARD, TASK_ID_RE, CrewError, atomic_write_json, load_config, read_json
from crew.procutil import create_time, same_process
from crew.state import Runs
from crew.trace import TraceFiles, trace_timestamp

COMMAND_SUFFIX = ".viewer-command"
COMMAND_HISTORY_LIMIT = 20
OUTPUT_LIMIT = 32 * 1024
QUERY_TIMEOUT = 30
QUERY_COMMANDS = ("status", "check", "result")
OPERATIONS = ("run", "cancel")
PREVIEW_LIFETIME = 120
PREVIEW_LIMIT = 32
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CLI_ENTRY = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
             "from crew.trace_commands import query_main; sys.exit(query_main(sys.argv[1:]))")
RUN_ENTRY = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
             "from crew.trace_commands import run_main; sys.exit(run_main(sys.argv[1:]))")


def _project(argv: list[str]) -> Path | None:
    project = Path(argv[0])
    if Path.cwd().resolve() == project and (project / ".agent-runs").resolve() == project / ".agent-runs":
        return project
    return None


def query_main(argv: list[str]) -> int:
    from crew.cli import main

    project = _project(argv)
    if project is None:
        print("crew: the viewer project has moved or is linked elsewhere", file=sys.stderr)
        return EXIT_GUARD
    return main(argv[1:], viewer_project=project)


def _record_path(runs: Runs, request_id: str) -> Path:
    if not re.fullmatch(r"\d{20}-[0-9a-f]{32}", request_id):
        raise ValueError("Invalid command record ID.")
    path = runs.file(request_id + COMMAND_SUFFIX)
    if path.resolve() != path:
        raise OSError("Command history path is linked elsewhere.")
    return path


class OutputRecord:
    def __init__(self, runs: Runs, record: dict):
        self.path = _record_path(runs, record["id"])
        self.record = record
        self.lock = Lock()
        self.retained = 0

    def save(self) -> None:
        if self.path.resolve() != self.path:
            raise OSError("Command history path is linked elsewhere.")
        atomic_write_json(self.path, self.record)

    def append(self, channel: str, text: str) -> int:
        if text:
            with self.lock:
                remaining = OUTPUT_LIMIT - self.retained
                truncated = len(text) > remaining and not self.record["truncated"]
                if remaining or truncated:
                    self.record["truncated"] |= truncated
                    if remaining:
                        chunk = text[:remaining]
                        self.record["output"].append({"channel": channel, "text": chunk})
                        self.retained += len(chunk)
                    try:
                        self.save()
                    except OSError:
                        self.record["error"] = "Could not save command output."
        return len(text)


class RecordStream:
    def __init__(self, output: OutputRecord, channel: str):
        self.output = output
        self.channel = channel

    def write(self, text: str) -> int:
        return self.output.append(self.channel, text)

    def flush(self) -> None:
        pass


def run_main(argv: list[str]) -> int:
    """Own the run record in the controller so it survives viewer shutdown."""
    from crew.cli import main

    project = _project(argv)
    if project is None:
        return EXIT_GUARD
    request_id, digest, *args = argv[1:]
    runs = Runs(project)
    record = read_json(_record_path(runs, request_id))
    output = OutputRecord(runs, record)
    record.update(owner_pid=os.getpid(), owner_started=create_time(os.getpid()))
    output.save()
    try:
        with redirect_stdout(RecordStream(output, "stdout")), redirect_stderr(RecordStream(output, "stderr")):
            record["exit_code"] = main(args, viewer_project=project, viewer_contract_digest=digest)
        return record["exit_code"]
    except Exception:
        record.update(exit_code=EXIT_GUARD, error="The run controller failed unexpectedly.")
        return EXIT_GUARD
    finally:
        record["ended"] = trace_timestamp()
        output.save()


@dataclass(frozen=True)
class Preview:
    command: str
    task_id: str
    args: tuple[str, ...]
    expires: float
    attempt: int | None = None
    digest: str | None = None


class TraceCommands:
    """Run project commands with separate query, run, and cancellation slots."""

    def __init__(self, runs):
        self.runs = runs
        self.lock = Lock()
        self.closed = False
        self.workers: dict[str, Thread] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.previews: dict[str, Preview] = {}
        self.files = TraceFiles(runs)
        self.owner_started = create_time(os.getpid())
        self._history_stamp = None
        self._registered_ids: set[str] = set()
        self._contract_cache: dict[str, tuple[tuple[int, int, int], dict]] = {}

    def history(self) -> list[dict]:
        records = []
        if self.runs.dir.resolve() == self.runs.dir:
            paths = sorted(self.runs.dir.glob(f"*{COMMAND_SUFFIX}"), reverse=True)
            for path in paths[:COMMAND_HISTORY_LIMIT]:
                record = self.files.json(path.name)
                if record.get("id") and record.get("command") in (*QUERY_COMMANDS, *OPERATIONS):
                    owner = record.get("owner_pid")
                    if not record.get("ended") and type(owner) is int and not same_process(owner, record.get("owner_started")):
                        record = {**record, "interrupted": True, "error": "The viewer stopped before saving the result."}
                    records.append(record)
        self.files.prune()
        return records

    def contracts(self) -> list[str]:
        folder = self.runs.contracts
        if folder.resolve() != folder:
            return []
        return sorted(path.stem for path in folder.glob("*.md") if re.fullmatch(TASK_ID_RE, path.stem)
                      and path.resolve() == path and path.is_file())

    def pending_contracts(self, names: list[str], task_ids: set[str]) -> list[dict]:
        self._contract_cache = {name: self._contract_cache[name] for name in names if name in self._contract_cache}
        if not names:
            return []
        history = self.runs.history
        if history.resolve() != history:
            return []  # a linked registry cannot be trusted to exclude cleaned tasks; fail closed
        stamp = None
        try:
            info = history.stat()
            stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        except FileNotFoundError:
            pass
        if stamp != self._history_stamp:
            try:
                self._registered_ids = {
                    row["task_id"] for row in self.runs.iter_history()
                    if isinstance(row, dict) and isinstance(row.get("task_id"), str)
                } if stamp is not None else set()
            except (UnicodeError, RecursionError):
                return []
            self._history_stamp = stamp

        pending = {}
        for name in names:
            path = self.runs.contracts / f"{name}.md"
            try:
                if path.resolve() != path:
                    continue
                info = path.stat()
                stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
                cached = self._contract_cache.get(name)
                if not cached or cached[0] != stamp:
                    fields = contract.load(path).fields
                    cached = self._contract_cache[name] = (stamp, {key: fields.get(key) for key in ("task_id", "plan")})
            except (contract.ContractError, OSError, ValueError, RuntimeError):
                continue
            task_id, plan = cached[1]["task_id"], cached[1]["plan"]
            if not isinstance(task_id, str) or not re.fullmatch(TASK_ID_RE, task_id):
                continue
            if (task_id in task_ids or task_id in self._registered_ids
                    or self.runs.state_path(task_id).exists() or self.runs.active_path(task_id).exists()):
                continue
            pending.setdefault(task_id, {"task_id": task_id, "contract": name,
                                         "plan": plan if isinstance(plan, str) and re.fullmatch(TASK_ID_RE, plan) else None})
        return list(pending.values())

    def _check_project(self, project: object) -> None:
        if project != str(self.runs.root):
            raise ValueError("The selected project does not match this viewer.")
        if self.runs.root.resolve() != self.runs.root or self.runs.dir.resolve() != self.runs.dir:
            raise ValueError("The viewer project has moved or is linked elsewhere.")

    def preview(self, payload: dict) -> dict:
        command = payload.get("command")
        key = "contract" if command == "run-preview" else "task_id"
        if set(payload) != {"project", "command", key}:
            raise ValueError("Invalid preview request.")
        self._check_project(payload["project"])
        target = payload[key]
        if not isinstance(target, str) or not re.fullmatch(TASK_ID_RE, target):
            raise ValueError("Enter a valid contract or task ID.")
        expires = monotonic() + PREVIEW_LIFETIME
        if command == "run-preview":
            path = self.runs.contracts / (target + ".md")
            if path.resolve() != path or not path.is_file():
                raise ValueError("Contract not found in this project's contracts folder.")
            digest = sha256(path.read_bytes()).hexdigest()
            try:
                loaded = contract.load(path, expected_digest=digest)
            except contract.ContractError as exc:
                raise ValueError(str(exc)) from exc
            try:
                cfg = load_config()
            except CrewError as exc:
                raise ValueError(exc.message) from exc
            settings = contract.merge(loaded, cfg)
            checked = contract.validate(loaded, settings, self.runs.root, self.runs, cfg)
            if not checked.ok or not settings.get("agent"):
                raise ValueError("; ".join(checked.errors) or "The contract must specify a worker CLI.")
            scope = safety.instruction_dirs(self.runs.root, self.runs.root, settings.get("write_paths") or [],
                                            checked.add_dirs_abs)
            scan = safety.scan_instructions(scope)
            if scan["restrictions"] or scan["privacy"] or runner.cost_gate(settings, cfg, False):
                raise ValueError("This contract requires review or approval in the CLI. Run crew contract check first.")
            task_id = settings["task_id"]
            preview = Preview("run", task_id, ("run", "--contract", str(path)), expires, digest=digest)
            description = f"Run {task_id} using {settings['agent']} ({settings['mode']})? The worker provider receives the contract context."
        else:
            task_id = target
            attempt = self._attempt("cancel", task_id)
            try:
                _, pid, started = runner.cancel_target(self.runs, task_id)
            except CrewError as exc:
                raise ValueError(exc.message) from exc
            identity = f"{pid}:{started}"
            preview = Preview("cancel", task_id, ("cancel", task_id, "--target", identity, "--yes"), expires, attempt)
            description = f"Cancel {task_id} by stopping worker PID {pid} (creation time {started}) and its child processes?"
        with self.lock:
            self.previews = {token: saved for token, saved in self.previews.items() if saved.expires > monotonic()}
            if len(self.previews) >= PREVIEW_LIMIT:
                self.previews.pop(next(iter(self.previews)))
            token = uuid4().hex
            self.previews[token] = preview
        return {"preview_id": token, "description": description}

    def submit(self, payload: object) -> str:
        if isinstance(payload, dict) and payload.get("command") in OPERATIONS:
            return self._confirm(payload)
        if not isinstance(payload, dict) or set(payload) != {"project", "command", "task_id"}:
            raise ValueError("Invalid query request.")
        self._check_project(payload["project"])
        command, task_id = payload["command"], payload["task_id"]
        if not isinstance(command, str) or command not in QUERY_COMMANDS:
            raise ValueError("Choose status, check, or result.")
        if command == "status":
            if task_id != "":
                raise ValueError("Status takes no task ID.")
        elif not isinstance(task_id, str) or not re.fullmatch(TASK_ID_RE, task_id):
            raise ValueError("Enter a valid task ID.")

        with self.lock:
            if command == "status" and any(path.resolve() != path for path in self.runs.dir.glob("*.json")):
                raise ValueError("The run folder contains linked metadata.")
            attempt = self._attempt(command, task_id) if task_id else None
            args = (command, task_id) if task_id else (command,)
            return self._start("query", command, task_id, attempt, args)

    def _confirm(self, payload: dict) -> str:
        if set(payload) != {"project", "command", "preview_id"} or not isinstance(payload["preview_id"], str):
            raise ValueError("Preview this operation before confirming it.")
        self._check_project(payload["project"])
        with self.lock:
            token = payload["preview_id"]
            preview = self.previews.get(token)
            if preview is None or preview.expires <= monotonic() or preview.command != payload["command"]:
                raise ValueError("The preview expired or does not match. Preview the operation again.")
            request_id = self._start(preview.command, preview.command, preview.task_id, preview.attempt,
                                     preview.args, digest=preview.digest)
            self.previews.pop(token)
            return request_id

    def _start(self, lane: str, command: str, task_id: str, attempt: int | None, args: tuple[str, ...],
               *, digest: str | None = None) -> str:
        if self.closed or lane in self.workers:
            raise BlockingIOError(f"A {lane} command is already running or the viewer is closing.")
        request_id = f"{time_ns():020d}-{uuid4().hex}"
        record = {"id": request_id, "command": command, "task_id": task_id, "attempt": attempt,
                  "started": trace_timestamp(), "ended": None, "exit_code": None,
                  "output": [], "truncated": False, "error": None, "owner_pid": os.getpid(),
                  "owner_started": self.owner_started}
        self.runs.ensure()
        output = OutputRecord(self.runs, record)
        output.save()
        self.workers[lane] = Thread(target=self._run, args=(lane, output, args, digest),
                                    name=f"crew-trace-{lane}", daemon=True)
        self.workers[lane].start()
        return request_id

    def _attempt(self, command: str, task_id: str) -> int | None:
        state_path = self.runs.state_path(task_id)
        if state_path.resolve() != state_path:
            raise ValueError("The task state is linked elsewhere.")
        try:
            state = self.runs.load_state(task_id)
        except CrewError as exc:
            raise ValueError(exc.message) from exc
        if not isinstance(state, dict) or state.get("task_id") != task_id:
            raise ValueError("Task not found in this project.")
        attempts = state.get("attempts") or []
        if not isinstance(attempts, list) or attempts and not isinstance(attempts[-1], dict):
            raise ValueError("Invalid task attempts.")
        last = attempts[-1] if attempts else {}
        number = last.get("n")
        if command in ("check", "result"):
            for key, suffix in (("result", "result.md"), ("events", "events.jsonl"), ("log", "log")):
                name = last.get(key)
                if name:
                    if type(number) is not int or number < 1:
                        raise ValueError("Invalid task attempt number.")
                    path = self.runs.file(f"{task_id}.a{number}.{suffix}")
                    if name != path.name or path.resolve() != path:
                        raise ValueError("A task result file is outside the expected location.")
        # Check describes the current run. Result reads the last saved attempt's output.
        current = (state.get("live_attempt") or last) if command != "result" else last
        number = current.get("n") if isinstance(current, dict) else None
        return number if type(number) is int and number > 0 else None

    def _run(self, lane: str, output: OutputRecord, args: tuple[str, ...], digest: str | None) -> None:
        record = output.record
        detached = lane == "run"
        completed_in_child = False

        def capture(pipe, channel: str) -> None:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            with pipe:
                while True:
                    raw = pipe.read1(4096)
                    output.append(channel, decoder.decode(raw, final=not raw))
                    if not raw:
                        return

        try:
            with self.lock:
                if self.closed:
                    record["error"] = "Viewer closed before the command started."
                    return
                entry = RUN_ENTRY if detached else CLI_ENTRY
                child_args = (record["id"], digest, *args) if detached else args
                stream = subprocess.DEVNULL if detached else subprocess.PIPE
                flags = subprocess.CREATE_NEW_PROCESS_GROUP if detached and os.name == "nt" else 0
                process = subprocess.Popen(
                    [sys.executable, "-I", "-u", "-X", "utf8", "-c", entry,
                     str(PACKAGE_ROOT), str(self.runs.root), *child_args],
                    cwd=self.runs.root, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                    creationflags=flags, start_new_session=detached and os.name != "nt",
                )
                self.processes[lane] = process
            if detached:
                record["exit_code"] = process.wait()
                current = read_json(_record_path(self.runs, record["id"]))
                completed_in_child = bool(current.get("ended"))
                if not completed_in_child:
                    record.update(current, exit_code=process.returncode, error="The run controller exited unexpectedly.")
                return
            readers = [Thread(target=capture, args=(pipe, channel), daemon=True)
                       for pipe, channel in ((process.stdout, "stdout"), (process.stderr, "stderr"))]
            for reader in readers:
                reader.start()
            try:
                record["exit_code"] = process.wait(timeout=QUERY_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                record["exit_code"] = process.wait()
                record["error"] = "Command timed out."
            for reader in readers:
                reader.join()
            if self.closed:
                record["error"] = "Viewer closed while the command was running."
        except (OSError, ValueError):
            record["error"] = "Could not execute the local command."
        finally:
            with self.lock:
                if not completed_in_child:
                    record["ended"] = trace_timestamp()
                    try:
                        output.save()
                    except OSError:
                        pass
                self.processes.pop(lane, None)
                self.workers.pop(lane, None)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for lane, process in self.processes.items():
                if lane != "run" and process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
            workers = [worker for lane, worker in self.workers.items() if lane != "run"]
        for worker in workers:
            worker.join(timeout=2)
