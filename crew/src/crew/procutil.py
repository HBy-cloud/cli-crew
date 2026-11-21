"""Process helpers: liveness, creation time, process-tree kill, group spawn."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _STILL_ACTIVE = 259

    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if IS_WINDOWS:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not _k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            _k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii") as info:
                return info.read().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
        except (OSError, IndexError):
            pass
    return True


def create_time(pid: int | None) -> float | None:
    """Process creation time as a float (platform units), or None."""
    if not pid or pid <= 0:
        return None
    if IS_WINDOWS:
        h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                return None
            return float((c.dwHighDateTime << 32) | c.dwLowDateTime)
        finally:
            _k32.CloseHandle(h)
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as f:
            return float(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def same_process(pid: int | None, recorded_create_time: float | None) -> bool:
    """True if `pid` is alive and (when known) has the recorded creation time."""
    if not pid_alive(pid):
        return False
    if recorded_create_time is None:
        return True
    return create_time(pid) == recorded_create_time


def kill_tree(pid: int | None) -> None:
    if not pid:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def stop_verified_tree(pid: int, started: float | None) -> bool:
    """Stop the recorded worker tree. Pin the Windows PID until termination is confirmed."""
    with ExitStack() as stack:
        if IS_WINDOWS:
            handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
            if not handle:
                return False
            stack.callback(_k32.CloseHandle, handle)
        if started is None or not same_process(pid, started):
            return False
        kill_tree(pid)
        if IS_WINDOWS:
            return _k32.WaitForSingleObject(handle, 5000) == 0
        deadline = time.monotonic() + 5
        while same_process(pid, started):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True


def spawn(args: list[str], *, cwd: str, env: dict[str, str], stdin_path: Path | None,
          stdout_path: str, stderr_path: str) -> subprocess.Popen:
    """Start a worker in its own process group with files for stdout/stderr."""
    kwargs: dict = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with ExitStack() as files:
        stdin = files.enter_context(stdin_path.open("rb")) if stdin_path else subprocess.DEVNULL
        out = files.enter_context(open(stdout_path, "wb"))
        err = files.enter_context(open(stderr_path, "wb"))
        return subprocess.Popen(args, cwd=cwd, env=env, shell=False,
                                stdin=stdin, stdout=out, stderr=err, **kwargs)


