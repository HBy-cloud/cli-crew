"""Before/after project snapshots and boundary diffing (SPEC 7.2)."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from crew.common import SNAPSHOT_EXCLUDE_DIRS, is_secret_name


def _hash(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _sig(path: Path) -> dict[str, Any]:
    secret = is_secret_name(path.name)
    try:
        st = path.stat()
    except OSError:
        return {"exists": False, "secret": secret}
    if not path.is_file():
        return {"exists": True, "dir": True, "secret": secret}
    sig = {"exists": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "secret": secret}
    if not secret:
        sig["sha256"] = _hash(path)
    return sig


def _excluded(rel_parts: tuple[str, ...]) -> bool:
    return any(part in SNAPSHOT_EXCLUDE_DIRS for part in rel_parts[:-1]) or (
        len(rel_parts) == 1 and rel_parts[0] in SNAPSHOT_EXCLUDE_DIRS)


def _walk(root: Path, only_secret: bool) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SNAPSHOT_EXCLUDE_DIRS]
        for name in filenames:
            if only_secret and not is_secret_name(name):
                continue
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            out[rel] = _sig(p)
    return out


def git_toplevel(root: Path) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                           capture_output=True, check=False)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.decode("utf-8", "replace").strip()
    return Path(out) if out else None


def _git_dirty(root: Path) -> dict[str, str] | None:
    """Dirty/untracked files under `root`, keyed by path relative to `root`.

    `git status --porcelain` reports paths relative to the repository top level even when run in a
    subdirectory, so paths are translated and filtered to `root` (a sub-project of a larger repo).
    """
    top = git_toplevel(root)
    if top is None:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "-uall", "--", "."],
            capture_output=True, check=False,
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    prefix = Path(os.path.relpath(root, top)).as_posix()
    prefix = "" if prefix == "." else prefix + "/"

    def local(p: str) -> str | None:
        if not prefix:
            return p
        if p.lower().startswith(prefix.lower()):
            return p[len(prefix):]
        return None

    entries: dict[str, str] = {}
    parts = r.stdout.decode("utf-8", errors="replace").split("\0")
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        lp = local(path)
        if lp:
            entries[lp] = xy
        if xy[0] in "RC" or xy[1] in "RC":
            if i < len(parts) and parts[i]:
                orig = local(parts[i])  # original path of rename/copy
                if orig:
                    entries[orig] = xy
            i += 1
    return entries


def is_git_repo(root: Path) -> bool:
    return git_toplevel(root) is not None


def take(root: Path) -> dict[str, Any]:
    dirty = _git_dirty(root)
    if dirty is not None:
        files: dict[str, dict[str, Any]] = {}
        for rel, xy in dirty.items():
            parts = tuple(Path(rel).parts)
            if _excluded(parts):
                continue
            sig = _sig(root / rel)
            sig["xy"] = xy
            files[rel] = sig
        for rel, sig in _walk(root, only_secret=True).items():
            files.setdefault(rel, sig)
        return {"mode": "git", "files": files}
    return {"mode": "walk", "files": _walk(root, only_secret=False)}


def _differs(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("exists") != b.get("exists"):
        return True
    if not a.get("exists"):
        return False
    if a.get("secret") or b.get("secret"):
        return (a.get("size"), a.get("mtime_ns")) != (b.get("size"), b.get("mtime_ns"))
    return a.get("sha256") != b.get("sha256") or a.get("size") != b.get("size")


def diff(before: dict[str, Any], after: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Files changed between snapshots. Pre-existing dirty files count only if they changed."""
    b, a = before.get("files", {}), after.get("files", {})
    changes: list[dict[str, Any]] = []
    for rel in sorted(set(a) | set(b)):
        sa, sb = a.get(rel), b.get(rel)
        if sa is not None and sb is not None:
            if not _differs(sb, sa):
                continue
            kind = "deleted" if not sa.get("exists") else "modified"
        elif sa is not None:  # newly dirty / new file
            if not sa.get("exists"):
                kind = "deleted"
            elif sa.get("xy") == "??" or before.get("mode") == "walk":
                kind = "added"
            else:
                kind = "modified"
        else:  # was dirty before, clean or gone now
            current = _sig(root / rel)
            if before.get("mode") == "walk":
                kind = "deleted"
            elif current.get("exists"):
                kind = "modified"  # reverted to committed content
            else:
                kind = "deleted"
        changes.append({"path": rel, "kind": kind, "secret": bool((sa or sb or {}).get("secret"))})
    return changes


def violations(changes: list[dict[str, Any]], mode: str, write_paths_abs: list[Path], root: Path) -> list[dict[str, Any]]:
    from crew.common import is_within

    if mode == "read":
        return list(changes)
    out = []
    for ch in changes:
        target = root / ch["path"]
        if not any(is_within(target, wp) for wp in write_paths_abs):
            out.append(ch)
    return out
