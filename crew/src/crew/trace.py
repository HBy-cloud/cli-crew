"""Project-local trace metadata for workers and explicitly connected main agents."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from crew.common import TASK_ID_RE, atomic_write_json, now, read_json
from crew.state import Runs

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
READ_LIMIT = 256 * 1024
TIMELINE_LIMIT = 120
CODEX_EVENTS = {
    "thread.started": "Session started", "turn.started": "Turn started",
    "turn.completed": "Turn completed", "turn.failed": "Turn failed", "error": "Worker reported an error",
    "item.started": "Activity started", "item.completed": "Activity completed", "item.updated": "Activity updated",
}
AGY_EVENTS = {"init": "Session started", "step_update": "Step updated", "result": "Worker reported a result"}
COPILOT_EVENTS = {
    "session.start": "Session started", "assistant.message": "Response activity",
    "tool.execution_start": "Tool started", "tool.execution_complete": "Tool completed",
    "result": "Worker reported a result", "session.error": "Worker reported an error",
}
CHILD_STATES = {
    "pending": "Waiting", "running": "Running", "in_progress": "Running", "completed": "Finished",
    "finished": "Finished", "errored": "Failed", "failed": "Failed", "shutdown": "Stopped",
}
WORKER_LABELS = set(CODEX_EVENTS.values()) | set(AGY_EVENTS.values()) | set(COPILOT_EVENTS.values()) | {"Subagent observed"}
NATIVE_LABELS = {"Subagent started", "Subagent stopped"}
OBSERVED_STATES = {*CHILD_STATES.values(), "Observed"}


def identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def trace_timestamp() -> str:
    return now().isoformat(timespec="milliseconds")


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _objects(value: Any):
    if isinstance(value, list):
        yield from (item for item in value if isinstance(item, dict))


def activity(agent: str, event: dict) -> list[tuple[str, str | None, str | None]]:
    """Return only known activity labels, child IDs, and observed child states."""
    if agent == "agy":
        kind = event.get("event")
        step = _object(event.get("step_update"))
        children = []
        for child in _objects(_object(step.get("subagent_info")).get("subagents")):
            child_id = identifier(child.get("conversation_id"))
            if child_id:
                status = identifier(child.get("status") or child.get("state")) or ""
                children.append(("Subagent observed", child_id, CHILD_STATES.get(status.lower(), "Observed")))
        if children:
            return children
        label = AGY_EVENTS.get(identifier(kind))
    elif agent == "codex":
        kind = event.get("type")
        item = _object(event.get("item"))
        children = []
        if item.get("type") == "collab_agent_tool_call":
            for child_id, detail in _object(item.get("agents_states")).items():
                if identifier(child_id):
                    status = identifier(_object(detail).get("status")) or ""
                    children.append(("Subagent observed", child_id, CHILD_STATES.get(status.lower(), "Observed")))
            receivers = item.get("receiver_thread_ids")
            if isinstance(receivers, list) and item.get("tool") == "spawn_agent":
                known = {child[1] for child in children}
                children.extend(("Subagent observed", child_id, "Observed") for child_id in receivers
                                if identifier(child_id) and child_id not in known)
        if children:
            return children
        label = CODEX_EVENTS.get(identifier(kind))
    elif agent == "copilot":
        label = COPILOT_EVENTS.get(identifier(event.get("type")))
    else:
        label = "Worker reported a result" if event.get("type") == "result" else None
    return [(label, None, None)] if label else []


class ActivityRecorder:
    """Tail a worker's JSONL in bounded reads without changing its launch or wait behavior."""

    def __init__(self, agent: str, source: Path, destination: Path, children_path: Path):
        self.agent = agent
        self.source = source
        self.destination = destination
        self.offset = 0
        self.partial = b""
        self.discard_line = False
        self.children_path = children_path
        self.children: dict[str, dict] = {}

    def record(self, *, finished: bool = False) -> None:
        # Telemetry is best effort. A sharing violation or full disk must not fail the worker run.
        try:
            if self.source.is_symlink() or self.destination.is_symlink() or self.children_path.is_symlink():
                return
            with self.source.open("rb") as stream:
                stream.seek(self.offset)
                end = self.source.stat().st_size if finished else self.offset + READ_LIMIT
                while self.offset < end:
                    chunk = stream.read(min(READ_LIMIT, end - self.offset))
                    if not chunk:
                        break
                    self.offset += len(chunk)
                    self._record_chunk(chunk)
                if finished:
                    self._record_chunk(b"\n")
        except OSError:
            return

    def _record_chunk(self, chunk: bytes) -> None:
        if self.discard_line:
            _, endline, chunk = chunk.partition(b"\n")
            if not endline:
                return
            self.discard_line = False
        lines = (self.partial + chunk).split(b"\n")
        self.partial = lines.pop()
        if len(self.partial) > READ_LIMIT:
            self.partial = b""
            self.discard_line = True
        events = {}
        at = trace_timestamp()
        changed_children = False
        for line in lines:
            if not line or len(line) > READ_LIMIT:
                continue
            try:
                item = _object(json.loads(line))
            except (ValueError, RecursionError):
                continue
            for label, child, status in activity(self.agent, item):
                if child:
                    if self.children.get(child, {}).get("status") == status:
                        continue
                    self.children[child] = {"id": child, "status": status, "observed": at}
                    changed_children = True
                events[(label, child, status)] = None
        if events:
            with self.destination.open("a", encoding="utf-8", newline="\n") as stream:
                for label, child, status in events:
                    stream.write(json.dumps({"at": at, "label": label, "child": child, "status": status}) + "\n")
        if changed_children:
            atomic_write_json(self.children_path, {"children": list(self.children.values())})


def _local_file(runs: Runs, name: str) -> Path | None:
    if Path(name).name != name:
        return None
    path = runs.file(name)
    try:
        return path if path.resolve() == path and path.is_file() else None
    except (OSError, RuntimeError):
        return None


def _json(runs: Runs, name: str) -> dict:
    path = _local_file(runs, name)
    try:
        return _object(read_json(path)) if path else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _timestamp(value: Any) -> str | None:
    if isinstance(value, str) and len(value) <= 40:
        try:
            if datetime.fromisoformat(value).tzinfo is not None:
                return value
        except ValueError:
            pass
    return None


def _choice(value: Any, choices: set[str]) -> str | None:
    return value if isinstance(value, str) and value in choices else None


def _stored_event(row: dict, labels: set[str]) -> dict | None:
    at = _timestamp(row.get("at"))
    label = _choice(row.get("label"), labels)
    if not at or not label:
        return None
    return {"at": at, "label": label, "child": identifier(row.get("child")),
            "status": _choice(row.get("status"), OBSERVED_STATES)}


def _timeline(path: Path) -> list[dict]:
    with path.open("rb") as stream:
        offset = max(0, path.stat().st_size - READ_LIMIT)
        stream.seek(offset)
        if offset:
            stream.readline(READ_LIMIT)
        data = stream.read(READ_LIMIT)
    events = []
    for line in data.splitlines()[-TIMELINE_LIMIT:]:
        try:
            row = _object(json.loads(line))
        except (ValueError, RecursionError):
            continue
        event = _stored_event(row, WORKER_LABELS)
        if event:
            events.append(event)
    return events


class TraceFiles:
    """Reuse parsed metadata while its file identity and stat fields stay the same."""

    def __init__(self, runs: Runs):
        self.runs = runs
        self.cache = {}
        self.seen: set[str] = set()

    def _read(self, name, parse, empty):
        path = _local_file(self.runs, name)
        if path is None:
            return empty
        try:
            info = path.stat()
            version = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            cached = self.cache.get(name)
            if cached is None or cached[0] != version:
                cached = (version, parse(path))
                self.cache[name] = cached
        except (OSError, ValueError, RecursionError):
            return empty
        self.seen.add(name)
        return cached[1]

    def json(self, name: str) -> dict:
        return _object(self._read(name, read_json, {}))

    def timeline(self, name: str) -> list[dict]:
        return self._read(name, _timeline, [])

    def prune(self) -> None:
        self.cache = {name: self.cache[name] for name in self.seen}
        self.seen.clear()


def _event(at: str | None, label: str) -> dict:
    return {"at": at, "label": label}


def _attempt(files: TraceFiles, tid: str, attempt: dict, *, stale: bool) -> dict:
    n = attempt["n"]
    child_events = files.timeline(f"{tid}.a{n}.trace.jsonl")
    events = [_event(attempt.get("started"), f"Attempt {n} started"), *child_events]
    children = {}
    for event in child_events:
        child = event.get("child")
        if identifier(child):
            children[child] = {"id": child, "status": event["status"], "observed": event["at"]}
    observed = files.json(f"{tid}.a{n}.trace-children.json")
    for child in _objects(observed.get("children")):
        child_id = identifier(child.get("id"))
        if child_id:
            children.setdefault(child_id, {"id": child_id, "status": _choice(child.get("status"), OBSERVED_STATES),
                                           "observed": _timestamp(child.get("observed"))})
    evidence = files.json(f"{tid}.a{n}.subagents.json")
    for child in _objects(evidence.get("children")):
        child_id = identifier(child.get("thread_id") or child.get("conversation_id"))
        if child_id:
            children.setdefault(child_id, {"id": child_id, "status": "Observed", "observed": None})
    if attempt.get("ended"):
        events.append(_event(attempt["ended"], f"Attempt {n} ended · {attempt.get('status', 'Unknown')}"))
    if attempt.get("ended") or stale:
        for child in children.values():
            if child["status"] in ("Running", "Waiting"):
                child["status"] = "Final state unknown"
    used = evidence.get("used")
    if not isinstance(used, bool):
        used = attempt.get("subagent_used")
    return {"n": n, "events": events, "children": list(children.values()),
            "subagent_used": used if isinstance(used, bool) else None}


def trace_snapshot(runs: Runs, display_status, *, files: TraceFiles | None = None) -> dict:
    files = files or TraceFiles(runs)
    tasks = []
    sessions: dict[str, dict] = {}
    if runs.dir.resolve() != runs.dir:
        files.prune()
        return {"tasks": [], "sessions": {}}
    for path in sorted(runs.dir.glob("*.json")):
        if path.name.endswith(".native-trace.json"):
            record = files.json(path.name)
            if identifier(record.get("session")) and identifier(record.get("id")):
                session = sessions.setdefault(record["session"], {"plan": None, "children": []})
                session["plan"] = session["plan"] or identifier(record.get("plan"))
                session["children"].append({"id": record["id"],
                    "status": _choice(record.get("status"), {"Running", "Finished"}),
                    "observed": _timestamp(record.get("observed"))})
                for entry in _objects(record.get("events")):
                    event = _stored_event(entry, NATIVE_LABELS)
                    if event:
                        session.setdefault("events", []).append({**event, "child": record["id"], "status": None})
            continue
        if not re.fullmatch(TASK_ID_RE, path.stem):
            continue
        state = files.json(path.name)
        if state.get("task_id") != path.stem:
            continue
        task = {key: state.get(key) for key in ("task_id", "plan", "plan_step", "agent", "mode", "started",
                                               "ended", "review_verdict", "verdict", "model_actual",
                                               "model_requested", "model_resolved")}
        task["status"] = display_status(state)
        task["events"] = [_event(task["started"], "Worker reserved")]
        if task["ended"]:
            task["events"].append(_event(task["ended"], f"Worker ended · {task['status']}"))
        if task["verdict"]:
            task["events"].append(_event(state.get("judged_at"), f"Main verdict · {task['verdict']}"))
        attempts = {a["n"]: a for a in _objects(state.get("attempts")) if type(a.get("n")) is int and a["n"] > 0}
        live = _object(state.get("live_attempt"))
        if type(live.get("n")) is int and live["n"] > 0:
            attempts.setdefault(live["n"], {**live, "ended": task["ended"], "status": task["status"]})
        stale = task["status"] in ("STALE", "STALE?")
        task["attempts"] = [_attempt(files, path.stem, attempts[n], stale=stale) for n in sorted(attempts)]
        tasks.append(task)
    files.prune()
    tasks.sort(key=lambda task: str(task["started"] or ""), reverse=True)
    return {"tasks": tasks, "sessions": sessions}


def record_claude_hook(runs: Runs, payload: dict, plan: str | None = None) -> None:
    """Store an opt-in Claude main session hook without retaining its input or output."""
    session = identifier(payload.get("session_id"))
    child = identifier(payload.get("agent_id"))
    event = payload.get("hook_event_name")
    if not session or not child or event not in ("SubagentStart", "SubagentStop"):
        return
    # Claude IDs use alphanumerics or hyphens. A narrower alphabet also rules out Windows ADS and path aliases.
    if not all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (session, child)):
        return
    if runs.dir.resolve() != runs.dir:
        return
    runs.ensure()
    name = f"{session}.{child}.native-trace.json"
    destination = runs.file(name)
    if destination.resolve() != destination:
        return
    at = trace_timestamp()
    previous = _json(runs, name)
    starting = event == "SubagentStart"
    events = [_event(row["at"], row["label"]) for item in _objects(previous.get("events"))
              if (row := _stored_event(item, NATIVE_LABELS))][-TIMELINE_LIMIT + 1:]
    events.append(_event(at, "Subagent started" if starting else "Subagent stopped"))
    atomic_write_json(destination, {
        "session": session, "id": child, "observed": at, "status": "Running" if starting else "Finished",
        "plan": plan if plan and re.fullmatch(TASK_ID_RE, plan) else previous.get("plan"),
        "started": at if starting else previous.get("started"), "ended": None if starting else at,
        "events": events,
    })
