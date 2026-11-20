"""Worker built-in subagent detection and worker session file discovery (SPEC 7.3)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from crew.common import is_within, real


def codex_sessions_root() -> Path:
    base = os.environ.get("CODEX_HOME")
    return Path(base) / "sessions" if base else Path.home() / ".codex" / "sessions"


def agy_home() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli"


def _first_line(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return json.loads(f.readline())
    except (OSError, json.JSONDecodeError):
        return None


def codex_meta(path: Path) -> tuple[str | None, str | None]:
    """(id, parent_thread_id) from a rollout's session_meta line."""
    first = _first_line(path)
    if not first or first.get("type") != "session_meta":
        return None, None
    pl = first.get("payload") or {}
    parent = pl.get("parent_thread_id")
    src = pl.get("source")
    if not parent and isinstance(src, dict):
        parent = ((src.get("subagent") or {}).get("thread_spawn") or {}).get("parent_thread_id")
    return pl.get("id"), parent


def codex_model_from_rollout(path: Path) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"turn_context"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = (o.get("payload") or {}).get("model")
                if m:
                    return m
    except OSError:
        return None
    return None


def codex_rollouts(thread_id: str, since_epoch: float) -> dict[str, Any]:
    """Find the worker rollout and all descendant rollouts modified since `since_epoch`."""
    root = codex_sessions_root()
    result = {"main": None, "children": [], "model": None}
    if not root.exists() or not thread_id:
        return result
    metas: dict[Path, tuple[str | None, str | None]] = {}
    for p in root.rglob("rollout-*.jsonl"):
        try:
            if p.stat().st_mtime < since_epoch - 5:
                continue
        except OSError:
            continue
        metas[p] = codex_meta(p)
    wanted = {thread_id}
    changed = True
    while changed:
        changed = False
        for p, (pid, parent) in metas.items():
            if pid == thread_id and result["main"] is None:
                result["main"] = str(p)
            if parent in wanted and pid and pid not in wanted:
                wanted.add(pid)
                result["children"].append({"thread_id": pid, "parent": parent, "path": str(p)})
                changed = True
    if result["main"]:
        result["model"] = codex_model_from_rollout(Path(result["main"]))
    return result


def agy_conversation_paths(conv_id: str) -> list[str]:
    home = agy_home()
    out = []
    for p in (home / "conversations" / f"{conv_id}.db", home / "brain" / conv_id):
        if p.exists():
            out.append(str(p))
    return out


def path_size(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        total = 0
        for dirpath, _, files in os.walk(p):
            for f in files:
                try:
                    total += (Path(dirpath) / f).stat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def verify_codex_rollout_for_delete(path: str, thread_ids: set[str]) -> str | None:
    """Return None if safe to delete, else a reason (SPEC 4.10 re-verification)."""
    p = Path(path)
    if p.is_symlink():
        return "is a symlink"
    if not p.exists():
        return "missing"
    if not p.is_file():
        return "not a regular file"
    try:
        if os.stat(p, follow_symlinks=False).st_file_attributes & 0x400:  # type: ignore[attr-defined]
            return "is a reparse point"
    except (AttributeError, OSError):
        pass
    if not is_within(real(p), real(codex_sessions_root())):
        return "outside ~/.codex/sessions"
    if not (p.name.startswith("rollout-") and p.name.endswith(".jsonl")):
        return "unexpected file name"
    pid, parent = codex_meta(p)
    if pid in thread_ids or parent in thread_ids:
        return None
    return "session id does not match recorded thread ids"
