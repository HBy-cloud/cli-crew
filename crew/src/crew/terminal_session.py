"""A foreground Windows console and a browser sharing one explicitly launched CLI."""

import codecs
import json
import os
import shutil
import sys
from contextlib import contextmanager
from queue import Empty, Full, Queue
from tempfile import SpooledTemporaryFile
from threading import Condition, Event, Thread
from typing import NamedTuple

from crew import procutil
from crew.common import CrewError, EXIT_USAGE

REPLAY_LIMIT = 512 * 1024
REPLAY_BATCH = 64
INPUT_LIMIT = 4096


class TerminalFrame(NamedTuple):
    cursor: int
    cols: int
    rows: int
    data: str


class TerminalSession:
    """Replay from the start so browser reconnects preserve VT parser and screen state."""

    def __init__(self, cols: int, rows: int):
        self.changed = Condition()
        self.closed = Event()
        self.input: Queue[str] = Queue(maxsize=64)
        self.output = SpooledTemporaryFile(max_size=REPLAY_LIMIT, mode="w+b")
        self.subscribers = 0
        self.latest = TerminalFrame(0, cols, rows, "")
        self.publish("", cols, rows)

    def publish(self, data: str, cols: int, rows: int) -> None:
        with self.changed:
            if self.closed.is_set():
                return
            self.output.seek(self.latest.cursor)
            self.output.write(json.dumps([cols, rows, data], ensure_ascii=False).encode("utf-8") + b"\n")
            self.latest = TerminalFrame(self.output.tell(), cols, rows, "")
            self.changed.notify_all()

    def after(self, cursor: int | None, timeout: float) -> tuple[bool, tuple[TerminalFrame, ...]]:
        with self.changed:
            self.changed.wait_for(lambda: cursor != self.latest.cursor or self.closed.is_set(), timeout)
            if self.output.closed:
                return False, ()
            reset = cursor is None or not 0 < cursor <= self.latest.cursor
            if not reset:
                self.output.seek(cursor - 1)
                reset = self.output.read(1) != b"\n"
            if reset:
                self.output.seek(0)
            frames = []
            for _ in range(REPLAY_BATCH):
                line = self.output.readline()
                if not line:
                    break
                cols, rows, data = json.loads(line)
                frames.append(TerminalFrame(self.output.tell(), cols, rows, data))
            return reset, tuple(frames)

    def send(self, data: object) -> None:
        if not isinstance(data, str) or not 0 < len(data.encode("utf-8")) <= INPUT_LIMIT:
            raise ValueError("Expected terminal input of up to 4096 UTF-8 bytes.")
        if self.closed.is_set():
            raise BlockingIOError("The CLI session has ended.")
        try:
            self.input.put_nowait(data)
        except Full as exc:
            raise BlockingIOError("Terminal input is busy. Try again.") from exc

    def close(self) -> None:
        with self.changed:
            self.closed.set()
            self.changed.notify_all()

    @contextmanager
    def subscribe(self):
        with self.changed:
            admitted = not self.closed.is_set()
            if admitted:
                self.subscribers += 1
        try:
            yield admitted
        finally:
            if admitted:
                with self.changed:
                    self.subscribers -= 1
                    self.changed.notify_all()

    def dispose(self) -> None:
        self.close()
        with self.changed:
            if not self.output.closed:
                self.changed.wait_for(lambda: not self.subscribers, timeout=2)
                self.output.close()


@contextmanager
def console_mode():
    """Use VT input/output while hosting the child; restore the parent on exit."""
    import ctypes
    from ctypes import wintypes
    from msvcrt import get_osfhandle

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_mode = kernel.GetConsoleMode
    get_mode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    set_mode = kernel.SetConsoleMode
    set_mode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    read = kernel.ReadConsoleW
    read.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    open_thread = kernel.OpenThread
    open_thread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_thread.restype = wintypes.HANDLE
    cancel = kernel.CancelSynchronousIo
    cancel.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    input_handle = get_osfhandle(sys.stdin.fileno())
    output_handle = get_osfhandle(sys.stdout.fileno())
    input_mode, output_mode = wintypes.DWORD(), wintypes.DWORD()
    if not get_mode(input_handle, ctypes.byref(input_mode)) or not get_mode(output_handle, ctypes.byref(output_mode)):
        raise CrewError(EXIT_USAGE, "Start the shared CLI from an interactive Windows terminal.")
    # Disable processed, line, echo and Quick Edit input. Enable extended and VT input.
    if not set_mode(input_handle, (input_mode.value & ~0x47) | 0x280):
        raise ctypes.WinError(ctypes.get_last_error())

    def read_keys(session):
        buffer = ctypes.create_string_buffer(512)
        count = wintypes.DWORD()
        decoder = codecs.getincrementaldecoder("utf-16-le")(errors="replace")
        while not session.closed.is_set() and read(input_handle, buffer, 256, ctypes.byref(count), None):
            data = decoder.decode(buffer.raw[:count.value * 2])
            if data:
                while not session.closed.is_set():
                    try:
                        session.input.put(data, timeout=0.1)
                        break
                    except Full:
                        continue

    try:
        if not set_mode(output_handle, output_mode.value | 0x0D):
            raise ctypes.WinError(ctypes.get_last_error())
        yield read_keys, open_thread, cancel, kernel.CloseHandle
    finally:
        set_mode(input_handle, input_mode.value)
        set_mode(output_handle, output_mode.value)


def run_terminal(session: TerminalSession, pty, pty_error, console) -> int:
    read_keys, open_thread, cancel, close_handle = console
    started = procutil.create_time(pty.pid)

    def write_keys():
        while not session.closed.is_set():
            try:
                data = session.input.get(timeout=0.1)
            except Empty:
                continue
            try:
                pty.write(data)
            except pty_error:
                if pty.isalive():
                    session.close()
                return

    keyboard = Thread(target=read_keys, args=(session,), name="crew-terminal-keyboard", daemon=True)
    writer = Thread(target=write_keys, name="crew-terminal-input", daemon=True)
    try:
        keyboard.start()
        writer.start()
        while not session.closed.is_set():
            cols, rows = os.get_terminal_size()
            if (cols, rows) != (session.latest.cols, session.latest.rows):
                pty.set_size(cols, rows)
                session.publish("", cols, rows)
            try:
                output = pty.read(blocking=False)
            except pty_error:
                if pty.isalive():
                    raise
                break
            if output:
                sys.stdout.buffer.write(output.encode("utf-8"))
                sys.stdout.buffer.flush()
                session.publish(output, cols, rows)
            elif not pty.isalive():
                break
            else:
                session.closed.wait(0.015)
        return pty.get_exitstatus() or (1 if pty.isalive() else 0)
    finally:
        session.close()
        handle = open_thread(0x0001, False, keyboard.native_id) if keyboard.is_alive() else None
        if handle:
            try:
                for _ in range(10):
                    if not keyboard.is_alive():
                        break
                    cancel(handle)
                    keyboard.join(timeout=0.05)
            finally:
                close_handle(handle)
        if pty.isalive() and started is not None:
            procutil.stop_verified_tree(pty.pid, started)
        if writer.ident is not None:
            writer.join(timeout=1)
        sys.stdout.buffer.write(b"\x1b[?1049l\x1b[?25h\x1b[0m")
        sys.stdout.buffer.flush()


def serve_interactive(runs, display_status, port: int) -> int:
    if os.name != "nt":
        raise CrewError(EXIT_USAGE, "Shared CLI sessions currently require Windows.")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise CrewError(EXIT_USAGE, "Start the shared CLI from an interactive Windows terminal.")
    try:
        from winpty import Backend, PTY, WinptyError
    except ImportError as exc:
        raise CrewError(EXIT_USAGE, "Install cli-crew with the terminal extra to enable shared CLI sessions.") from exc
    from crew.trace_view import create_server

    executable = shutil.which("codex.exe")
    if not executable:
        raise CrewError(EXIT_USAGE, "The native Codex executable (codex.exe) must be on PATH.")
    cols, rows = os.get_terminal_size()
    session = TerminalSession(cols, rows)
    try:
        with create_server(runs, display_status, port, terminal=session) as server, console_mode() as console:
            pty = PTY(cols, rows, backend=Backend.ConPTY)
            pty.spawn(executable, cwd=str(runs.root))
            thread = Thread(target=server.serve_forever, name="crew-terminal-server", daemon=True)
            thread.start()
            try:
                print(f"Crew Runs: http://127.0.0.1:{server.server_port}/terminal", flush=True)
                return run_terminal(session, pty, WinptyError, console)
            finally:
                session.close()
                server.shutdown()
                thread.join()
    except (WinptyError, OSError) as exc:
        raise CrewError(EXIT_USAGE, f"Shared CLI session failed: {exc}") from exc
    finally:
        session.dispose()
