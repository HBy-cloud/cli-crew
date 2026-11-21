"""`.agent-runs/` layout, task registry, history, and the reservation critical section."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterator

from crew import procutil
from crew.common import (
    EXIT_BUSY, EXIT_DUP, EXIT_GUARD, CrewError, atomic_write_json, is_within, now_iso, read_json,
    resolve_nearest,
)

RESERVE_WAIT_S = 10.0
RESERVE_STALE_S = 30.0

ACTIVE_STATES = {"RUNNING", "ORPHANED"}


class Runs:
    """Paths inside <project>/.agent-runs/."""

    def __init__(self, root: Path):
        self.root = root
        self.dir = root / ".agent-runs"
        self.contracts = self.dir / "contracts"
        self.locks = self.dir / "locks"
        self.active = self.locks / "active"
        self.reserve_lock = self.locks / "reserve.lock"
        self.history = self.dir / "history.jsonl"
        self.plans = self.dir / "plans"

    def contained(self, path: Path) -> Path:
        """Refuse paths under .agent-runs that a link or junction resolves outside the project (SPEC 4.0, 8)."""
        if not is_within(resolve_nearest(path), self.root):
            raise CrewError(EXIT_GUARD, f"{path} resolves outside the project ({resolve_nearest(path)}); "
                                        "remove the link or junction")
        return path

    def plan_path(self, plan_id: str) -> Path:
        return self.contained(self.plans / f"{plan_id}.md")

    def plan_files(self) -> list[Path]:
        if not self.contained(self.plans).is_dir():
            return []
        return sorted(self.contained(p) for p in self.plans.glob("*.md"))

    def ensure(self) -> None:
        for d in (self.dir, self.contracts, self.active):
            self.contained(d)
        self.contracts.mkdir(parents=True, exist_ok=True)
        self.active.mkdir(parents=True, exist_ok=True)
        for d in (self.dir, self.contracts, self.active):
            self.contained(d)
        gi = self.dir / ".gitignore"
        if not gi.exists():
            gi.write_text("*\n", encoding="utf-8")

    def state_path(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.json"

    def file(self, name: str) -> Path:
        return self.dir / name

    def active_path(self, task_id: str) -> Path:
        return self.active / f"{task_id}.json"

    # ------------------------------------------------------------ state files

    def load_state(self, task_id: str) -> dict[str, Any] | None:
        p = self.state_path(task_id)
        if not p.exists():
            return None
        return read_json(p)

    def save_state(self, state: dict[str, Any]) -> None:
        atomic_write_json(self.state_path(state["task_id"]), state)

    def iter_states(self) -> Iterator[dict[str, Any]]:
        if not self.dir.exists():
            return
        for p in sorted(self.dir.glob("*.json")):
            try:
                data = read_json(p)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("task_id") == p.stem:
                yield data

    def iter_history(self) -> Iterator[dict[str, Any]]:
        if not self.history.exists():
            return
        with open(self.history, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def append_history(self, entry: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.history, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def registry(self) -> dict[str, dict[str, Any]]:
        """task_id -> {verdict, retry_of, status, source} from history and state files."""
        reg: dict[str, dict[str, Any]] = {}
        for h in self.iter_history():
            tid = h.get("task_id")
            if tid:
                reg[tid] = {"verdict": h.get("verdict"), "retry_of": h.get("retry_of"),
                            "status": h.get("status"), "source": "history"}
        for s in self.iter_states():
            reg[s["task_id"]] = {"verdict": s.get("verdict"), "retry_of": s.get("retry_of"),
                                 "status": s.get("status"), "source": "state"}
        if self.active.exists():
            for p in self.active.glob("*.json"):
                reg.setdefault(p.stem, {"verdict": None, "retry_of": None,
                                        "status": "RUNNING", "source": "active"})
        return reg

    def list_active(self) -> list[dict[str, Any]]:
        out = []
        if not self.active.exists():
            return out
        for p in sorted(self.active.glob("*.json")):
            try:
                out.append(read_json(p))
            except (OSError, json.JSONDecodeError):
                out.append({"task_id": p.stem, "mode": "write", "crew_pid": None,
                            "worker_pid": None, "corrupt": True})
        return out


def rejected_chain_count(reg: dict[str, dict[str, Any]], retry_of: str | None) -> int:
    """Number of REJECTED verdicts along the retry_of chain (cycle-safe)."""
    count = 0
    seen: set[str] = set()
    cur = retry_of
    while cur and cur not in seen:
        seen.add(cur)
        info = reg.get(cur)
        if not info:
            break
        if info.get("verdict") == "REJECTED":
            count += 1
        cur = info.get("retry_of")
    return count


def retry_of_problem(reg: dict[str, dict[str, Any]], task_id: str, retry_of: str | None) -> str | None:
    if not retry_of:
        return None
    if retry_of == task_id:
        return "retry_of refers to the task itself"
    info = reg.get(retry_of)
    if not info:
        return f"retry_of '{retry_of}' is not a known task"
    if info.get("verdict") != "REJECTED":
        return f"retry_of '{retry_of}' was not REJECTED (verdict={info.get('verdict')})"
    seen = {task_id}
    cur: str | None = retry_of
    while cur:
        if cur in seen:
            return "retry_of chain contains a cycle"
        seen.add(cur)
        cur = (reg.get(cur) or {}).get("retry_of")
    return None


# ------------------------------------------------------------ reservation

class ReserveLock:
    """Exclusive critical section via O_CREAT|O_EXCL on locks/reserve.lock."""

    def __init__(self, runs: Runs, wait_s: float = RESERVE_WAIT_S):
        self.runs = runs
        self.wait_s = wait_s
        self.held = False

    def __enter__(self) -> "ReserveLock":
        self.runs.ensure()
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                fd = os.open(self.runs.reserve_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"crew_pid": os.getpid(), "created": time.time()}, f)
                self.held = True
                return self
            except (FileExistsError, PermissionError):
                # PermissionError: Windows "delete pending" while the holder is releasing
                self._maybe_reclaim()
                if time.monotonic() >= deadline:
                    raise CrewError(EXIT_BUSY, "could not acquire reservation lock (another crew run is reserving)")
                time.sleep(0.1)

    def _maybe_reclaim(self) -> None:
        p = self.runs.reserve_lock
        try:
            info = read_json(p)
            pid = info.get("crew_pid")
            created = float(info.get("created", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            try:
                created = p.stat().st_mtime
            except OSError:
                return
            pid = None
        if not procutil.pid_alive(pid) and time.time() - created > RESERVE_STALE_S:
            try:
                p.unlink()
            except OSError:
                pass

    def __exit__(self, *exc) -> None:
        if self.held:
            _unlink_with_retry(self.runs.reserve_lock)
            self.held = False


def _unlink_with_retry(path: Path, timeout_s: float = 5.0) -> None:
    """Unlink, retrying while another process briefly has the file open (Windows sharing violation)."""
    deadline = time.monotonic() + timeout_s
    delay = 0.01
    while True:
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)


def finalize_stale(runs: Runs, entry: dict[str, Any], status: str) -> None:
    tid = entry["task_id"]
    st = runs.load_state(tid)
    if st is not None:
        st["status"] = status
        if status == "STALE":
            st["ended"] = st.get("ended") or now_iso()
            st["message"] = (st.get("message") or "") + " crew process disappeared; marked STALE"
        else:
            st["message"] = (st.get("message") or "") + " crew process disappeared but worker alive; ORPHANED"
        runs.save_state(st)


def cleanup_stale(runs: Runs) -> list[str]:
    """Inside the critical section: reclaim dead reservations, mark orphans. Returns notes."""
    notes = []
    for entry in runs.list_active():
        tid = entry.get("task_id")
        if not tid or procutil.same_process(entry.get("crew_pid"), entry.get("crew_create_time")):
            continue
        wpid = entry.get("worker_pid")
        if wpid and procutil.same_process(wpid, entry.get("worker_create_time")):
            finalize_stale(runs, entry, "ORPHANED")
            notes.append(f"{tid}: ORPHANED (worker pid {wpid} still alive; use crew cancel)")
            continue
        finalize_stale(runs, entry, "STALE")
        try:
            runs.active_path(tid).unlink()
        except OSError:
            pass
        notes.append(f"{tid}: STALE reservation reclaimed")
    return notes


def check_concurrency(active: list[dict[str, Any]], mode: str, limit: int) -> str | None:
    if mode == "write" and active:
        return f"write task must run alone; active: {', '.join(a.get('task_id', '?') for a in active)}"
    if any(a.get("mode") == "write" for a in active):
        return "a write task is running"
    if len(active) >= limit:
        return f"concurrency limit {limit} reached"
    return None


def reserve(runs: Runs, task_id: str, mode: str, limit: int, initial_state: dict[str, Any]) -> list[str]:
    """Atomically reserve a slot and create the RUNNING state. Returns stale notes."""
    with ReserveLock(runs):
        created: list[Path] = []
        try:
            notes = cleanup_stale(runs)
            if task_id in runs.registry() or runs.state_path(task_id).exists():
                raise CrewError(EXIT_DUP, f"task_id '{task_id}' already used; choose a new id")
            problem = check_concurrency(runs.list_active(), mode, limit)
            if problem:
                raise CrewError(EXIT_BUSY, problem)
            ap = runs.active_path(task_id)
            atomic_write_json(ap, {"task_id": task_id, "mode": mode, "crew_pid": os.getpid(),
                                   "crew_create_time": initial_state.get("crew_create_time"),
                                   "worker_pid": None, "worker_create_time": None,
                                   "started": initial_state["started"]})
            created.append(ap)
            runs.save_state(initial_state)
            created.append(runs.state_path(task_id))
            return notes
        except BaseException:
            for p in created:
                try:
                    p.unlink()
                except OSError:
                    pass
            raise


def update_active_worker(runs: Runs, task_id: str, worker_pid: int, worker_ct: float | None) -> None:
    ap = runs.active_path(task_id)
    try:
        data = read_json(ap)
    except (OSError, json.JSONDecodeError):
        return
    data["worker_pid"] = worker_pid
    data["worker_create_time"] = worker_ct
    atomic_write_json(ap, data)


def release(runs: Runs, task_id: str) -> None:
    _unlink_with_retry(runs.active_path(task_id))
