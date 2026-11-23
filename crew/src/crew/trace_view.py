"""Loopback-only HTTP viewer for crew trace metadata."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from threading import Condition, Event, Thread
from typing import TYPE_CHECKING
from uuid import uuid4

from crew.common import CrewError, EXIT_USAGE
from crew.trace import TraceFiles, trace_snapshot
from crew.trace_commands import TraceCommands

if TYPE_CHECKING:
    from crew.terminal_session import TerminalSession

SCAN_INTERVAL = 0.5
HEARTBEAT_INTERVAL = 10
REPLAY_LIMIT = 64
REPLAY_BYTES = 8 * 1024 * 1024
ASSET_FILES = {
    "/": ("text/html", "index.html"),
    "/view.css": ("text/css", "view.css"),
    "/view.js": ("text/javascript", "view.js"),
    "/panels.js": ("text/javascript", "panels.js"),
    "/controls.js": ("text/javascript", "controls.js"),
}
TERMINAL_ASSETS = {
    "/terminal": ("text/html", "terminal.html"),
    "/terminal.js": ("text/javascript", "terminal.js"),
    "/vendor/xterm.js": ("text/javascript", "vendor/xterm.js"),
    "/vendor/xterm.css": ("text/css", "vendor/xterm.css"),
}

@dataclass(frozen=True)
class TraceFrame:
    cursor: str
    data: bytes


class TraceFeed:
    """One observer and a bounded replay buffer shared by the project's clients."""

    def __init__(self, runs, display_status, commands: TraceCommands | None = None, *, interactive: bool = False):
        self.runs = runs
        self.display_status = display_status
        self.files = TraceFiles(runs)
        self.commands = commands
        self.interactive = interactive
        self.changed = Condition()
        self.stopped = Event()
        self.epoch = uuid4().hex
        self.version = 0
        self.frames: deque[TraceFrame] = deque()
        self.retained_bytes = 0
        self.snapshot = None
        self.refresh()
        self.thread = Thread(target=self.watch, name="crew-trace-watch", daemon=True)

    def refresh(self) -> None:
        snapshot = trace_snapshot(self.runs, self.display_status, files=self.files)
        if self.commands is not None:
            snapshot["project"] = str(self.runs.root)
            snapshot["commands"] = self.commands.history()
            snapshot["contracts"] = self.commands.contracts()
            snapshot["pending_contracts"] = self.commands.pending_contracts(
                snapshot["contracts"], {task["task_id"] for task in snapshot["tasks"]})
        if self.interactive:
            snapshot["interactive"] = True
        if snapshot == self.snapshot:
            return
        payload = json.dumps(snapshot, ensure_ascii=True).encode("utf-8")
        with self.changed:
            self.snapshot = snapshot
            self.version += 1
            self.frames.append(TraceFrame(f"{self.epoch}:{self.version}", payload))
            self.retained_bytes += len(payload)
            while len(self.frames) > 1 and (len(self.frames) > REPLAY_LIMIT or self.retained_bytes > REPLAY_BYTES):
                self.retained_bytes -= len(self.frames.popleft().data)
            self.changed.notify_all()

    def watch(self) -> None:
        while not self.stopped.wait(SCAN_INTERVAL):
            try:
                self.refresh()
            except OSError:
                # Keep the last good view during a transient project directory error.
                continue

    def latest(self) -> TraceFrame:
        with self.changed:
            return self.frames[-1]

    def after(self, cursor: str | None) -> tuple[TraceFrame, ...] | None:
        with self.changed:
            self.changed.wait_for(lambda: self.stopped.is_set() or self.frames[-1].cursor != cursor,
                                  timeout=HEARTBEAT_INTERVAL)
            if self.stopped.is_set():
                return None
            for index, frame in enumerate(self.frames):
                if frame.cursor == cursor:
                    return tuple(self.frames)[index + 1:]
            return (self.frames[-1],)

    def close(self) -> None:
        self.stopped.set()
        with self.changed:
            self.changed.notify_all()
        if self.thread.is_alive():
            self.thread.join(timeout=1)


def create_server(runs, display_status, port: int, *, terminal: TerminalSession | None = None) -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise CrewError(EXIT_USAGE, "trace port must be between 0 and 65535")

    commands = TraceCommands(runs)
    feed = TraceFeed(runs, display_status, commands, interactive=terminal is not None)
    viewer = files("crew").joinpath("viewer")
    assets = {route: (mime, viewer.joinpath(name).read_bytes()) for route, (mime, name) in ASSET_FILES.items()}
    if terminal is not None:
        assets.update({route: (mime, viewer.joinpath(name).read_bytes())
                       for route, (mime, name) in TERMINAL_ASSETS.items()})

    class TraceHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host or self.headers.get("Origin") != f"http://{host}":
                self.send_error(403)
                return
            terminal_input = self.path == "/api/terminal/input" and terminal is not None
            if self.path != "/api/commands" and not terminal_input:
                self.send_error(404)
                return
            self.connection.settimeout(5)
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 0 < length <= 16384 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected a JSON query request.")
                request = json.loads(self.rfile.read(length))
                if terminal_input:
                    if not isinstance(request, dict) or set(request) != {"data"}:
                        raise ValueError("Expected terminal input.")
                    terminal.send(request["data"])
                    response, status = {}, 202
                elif isinstance(request, dict) and request.get("command") in ("run-preview", "cancel-preview"):
                    response, status = commands.preview(request), 200
                else:
                    response, status = {"id": commands.submit(request)}, 202
            except (ValueError, RecursionError) as exc:
                response, status = {"error": str(exc)}, 400
            except BlockingIOError as exc:
                response, status = {"error": str(exc)}, 409
            except (OSError, CrewError):
                response, status = {"error": "Could not open the project command history."}, 500
            data = json.dumps(response).encode("utf-8")
            self.send_headers("application/json", len(data), status=status)
            self.wfile.write(data)

        def do_GET(self):
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host or self.headers.get("Origin") not in (None, f"http://{host}"):
                self.send_error(403)
                return
            if self.path == "/api/events":
                self.stream_events()
                return
            if self.path == "/api/terminal/events" and terminal is not None:
                with terminal.subscribe() as admitted:
                    if admitted:
                        self.stream_terminal()
                    else:
                        self.send_error(410, "The CLI session has ended")
                return
            if self.path == "/api/trace":
                mime = "application/json"
                data = feed.latest().data
            elif self.path in assets:
                mime, data = assets[self.path]
            else:
                self.send_error(404)
                return
            self.send_headers(mime, len(data), terminal_page=self.path == "/terminal")
            self.wfile.write(data)

        def send_headers(self, mime: str, length: int | None = None, *, status: int = 200,
                         terminal_page: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            styles = "'self' 'unsafe-inline'" if terminal_page else "'self'"
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'self'; style-src {styles}; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()

        def stream_events(self) -> None:
            cursor = self.headers.get("Last-Event-ID")
            self.connection.settimeout(HEARTBEAT_INTERVAL)
            try:
                self.send_headers("text/event-stream")
                self.wfile.write(b": connected\n\n")
                while (frames := feed.after(cursor)) is not None:
                    if not frames:
                        self.wfile.write(b": heartbeat\n\n")
                    for frame in frames:
                        self.wfile.write(b"id: " + frame.cursor.encode("ascii") + b"\ndata: " + frame.data + b"\n\n")
                        cursor = frame.cursor
            except OSError:
                # A closed tab or slow socket only ends this connection.
                return

        def stream_terminal(self) -> None:
            last_event = self.headers.get("Last-Event-ID", "")
            cursor = int(last_event) if last_event.isascii() and last_event.isdecimal() and len(last_event) <= 16 else None
            self.connection.settimeout(HEARTBEAT_INTERVAL)
            try:
                self.send_headers("text/event-stream")
                self.wfile.write(b": connected\n\n")
                replay_end = terminal.latest.cursor
                ready = False
                while True:
                    reset, frames = terminal.after(cursor, HEARTBEAT_INTERVAL if ready else 0)
                    for frame in frames:
                        payload = json.dumps({"cols": frame.cols, "rows": frame.rows, "data": frame.data,
                                              "reset": reset})
                        self.wfile.write(f"id: {frame.cursor}\ndata: {payload}\n\n".encode("ascii"))
                        reset = False
                        cursor = frame.cursor
                    if terminal.closed.is_set() and (not frames or cursor == terminal.latest.cursor):
                        self.wfile.write(b"event: ended\ndata: {}\n\n")
                        return
                    if not ready and cursor is not None and cursor >= replay_end:
                        self.wfile.write(b"event: ready\ndata: {}\n\n")
                        ready = True
                    if not frames:
                        self.wfile.write(b": heartbeat\n\n")
            except OSError:
                return

    class TraceServer(ThreadingHTTPServer):
        def server_close(self):
            if terminal is not None:
                terminal.dispose()
            commands.close()
            feed.close()
            super().server_close()

    try:
        server = TraceServer(("127.0.0.1", port), TraceHandler)
    except OSError as exc:
        raise CrewError(EXIT_USAGE, f"cannot start the loopback trace viewer: {exc}") from exc
    feed.thread.start()
    return server
