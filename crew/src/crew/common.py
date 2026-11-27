"""Shared helpers: exit codes, errors, time, paths, atomic files, config."""

from __future__ import annotations

import fnmatch
import json
import os
import tempfile
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Exit codes (SPEC 4.0)
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GUARD = 3
EXIT_BUSY = 4
EXIT_DUP = 5
EXIT_NOT_DONE = 10
EXIT_VIOLATION = 11
EXIT_CANCEL = 130

KST = timezone(timedelta(hours=9))

TASK_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"

AGENTS = ("codex", "agy", "claude", "copilot", "cursor")

EFFORTS = {
    "codex": ("low", "medium", "high", "xhigh"),
    "agy": ("low", "medium", "high", "xhigh", "max"),
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "copilot": ("low",),
    "cursor": ("low",),  # the cursor model ID carries its effort
}
HIGH_EFFORTS = {"high", "xhigh", "max"}

# agy model IDs that carry their effort (claude-sonnet-5-5-low); agy rejects a different --effort.
MODEL_EFFORT_SUFFIX = r"-(low|medium|high|xhigh|max)$"


def approval_match(model: str | None, approval: list[str]) -> str | None:
    """The approval_models entry that `model` starts with (case-insensitive), so effort variants match too."""
    if not model:
        return None
    return next((a for a in approval if isinstance(a, str) and a and model.lower().startswith(a.lower())), None)


SNAPSHOT_EXCLUDE_DIRS = {
    ".git", ".agent-runs", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
}

SECRET_NAME_PATTERNS = (
    ".env*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*",
    "*credentials*", "*secret*", "*.kdbx",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "max_concurrent": 5,
    "default_timeout_min": 20,
    "default_effort": "low",
    "approval_models": [],
    "agy_prompt_mode": "file",  # "file" (V-2) or "inline"
    "env_passthrough": [],      # credential-like env var names to pass to workers anyway
}


class CrewError(Exception):
    """Error carrying a crew exit code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def now() -> datetime:
    return datetime.now(KST)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------- paths

def _ncase(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def real(p: str | os.PathLike) -> Path:
    return Path(os.path.realpath(os.fspath(p)))


def is_within(child: str | os.PathLike, parent: str | os.PathLike) -> bool:
    """Component-wise containment on real paths (case-insensitive on Windows)."""
    c = _ncase(os.fspath(real(child)))
    p = _ncase(os.fspath(real(parent)))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False


def resolve_nearest(path: str | os.PathLike) -> Path:
    """Resolve a possibly non-existent path via its nearest existing parent."""
    p = Path(os.path.abspath(os.fspath(path)))
    rest: list[str] = []
    cur = p
    while not cur.exists():
        if cur.parent == cur:
            break
        rest.append(cur.name)
        cur = cur.parent
    base = real(cur)
    for name in reversed(rest):
        base = base / name
    return base


def same_path(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    return _ncase(os.fspath(real(a))) == _ncase(os.fspath(real(b)))


def home_dir() -> Path:
    return real(Path.home())


def is_drive_root(p: Path) -> bool:
    return p.parent == p


def temp_dir() -> Path:
    return real(tempfile.gettempdir())


def unsuitable_root_reason(p: Path) -> str | None:
    p = real(p)
    if is_drive_root(p):
        return f"{p} is a drive/filesystem root"
    if same_path(p, home_dir()):
        return f"{p} is the home directory"
    if is_within(p, temp_dir()):
        return f"{p} is inside the OS temp directory"
    return None


PROJECT_MARKERS = (".agent-runs", "AGENTS.md", "CLAUDE.md", "GEMINI.md")


def find_project_root(start: str | os.PathLike | None = None) -> Path:
    """Project root for `start` (SPEC 4.0, revised after real-repo test).

    Walk up from `start` to the git root (inclusive). The first directory containing a project marker
    (.agent-runs, AGENTS.md, CLAUDE.md, GEMINI.md) wins; otherwise the git root; otherwise `start`.
    This supports one large git repository holding several projects. Raises CrewError(3) if unsuitable.
    """
    cur = real(start or os.getcwd())
    probe = cur
    marker_dir: Path | None = None
    git_root: Path | None = None
    while True:
        if marker_dir is None and any((probe / m).exists() for m in PROJECT_MARKERS):
            marker_dir = probe
        if (probe / ".git").exists():
            git_root = probe
            break
        if probe.parent == probe:
            break
        probe = probe.parent
    if git_root is None:
        root = marker_dir or cur
    else:
        root = marker_dir or git_root
    reason = unsuitable_root_reason(root)
    if reason:
        raise CrewError(EXIT_GUARD, f"unsuitable project root: {reason}")
    return root


def is_secret_name(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, pat) for pat in SECRET_NAME_PATTERNS)


# ---------------------------------------------------------------- files

REPLACE_RETRY_S = 5.0


def _replace_with_retry(tmp: str, path: Path) -> None:
    """os.replace, retrying while another process holds the target open (Windows sharing violation)."""
    import time

    deadline = time.monotonic() + REPLACE_RETRY_S
    delay = 0.01
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        # Worker text can carry lone surrogates from escaped JSON; never let them abort a run's records.
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace", newline="\n") as f:
            f.write(text)
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_text(path: Path) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------- config

def config_path() -> Path:
    override = os.environ.get("CREW_CONFIG")
    return Path(override) if override else Path.home() / ".crew" / "config.toml"


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    path = config_path()
    data = None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        pass  # no config: defaults
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        raise CrewError(EXIT_USAGE, f"invalid config file {path}: {e}") from e
    if data is not None:
        for key in DEFAULT_CONFIG:
            if key in data:
                cfg[key] = data[key]
        am = cfg["approval_models"]
        if not isinstance(am, list) or not all(isinstance(m, str) and m.strip() for m in am):
            raise CrewError(EXIT_USAGE, f"invalid config file {path}: approval_models must be a list of model id strings")
        mc = cfg["max_concurrent"]
        if not isinstance(mc, int) or isinstance(mc, bool) or mc < 1:
            raise CrewError(EXIT_USAGE, f"invalid config file {path}: max_concurrent must be an integer of 1 or more")
    return cfg
