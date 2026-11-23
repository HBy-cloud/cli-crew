"""Trace lifecycle, legacy task projection, and metadata filtering."""

import json
import os
import time
from contextlib import closing, contextmanager
from http.client import HTTPConnection
from threading import Thread
from unittest.mock import patch

from test_crew import Base, crew

from crew.cli import _display_status
from crew.procutil import create_time
from crew.trace import READ_LIMIT, ActivityRecorder, TraceFiles, record_claude_hook, trace_snapshot
from crew.terminal_session import TerminalSession
from crew.trace_commands import COMMAND_SUFFIX
from crew.trace_view import TraceFeed, create_server


class TraceTests(Base):
    def test_live_attempt_projects_child_without_exposing_contents(self):
        self.runs.ensure()
        self.runs.save_state({"task_id": "T-1", "plan": "P-plan", "status": "RUNNING", "agent": "agy",
                              "crew_pid": os.getpid(), "crew_create_time": create_time(os.getpid()),
                              "attempts": [], "live_attempt": {"n": 1, "started": "2026-11-22T12:00:00+09:00"},
                              "main_verification": "private verification"})
        source = self.runs.file("T-1.a1.events.jsonl")
        recorder = ActivityRecorder("agy", source, self.runs.file("T-1.a1.trace.jsonl"),
                                    self.runs.file("T-1.a1.trace-children.json"))
        line = json.dumps({"event": "step_update", "step_update": {"text_delta": "private text",
                           "subagent_info": {"subagents": [{"conversation_id": "child-1", "status": "RUNNING"}]}}})
        source.write_text(line[:30], encoding="utf-8")
        recorder.record()
        with source.open("a", encoding="utf-8") as stream:
            stream.write(line[30:] + "\n")
        recorder.record()
        result = trace_snapshot(self.runs, _display_status)
        task, = result["tasks"]
        self.assertEqual(task["plan"], "P-plan")
        attempt, = task["attempts"]
        child, = attempt["children"]
        self.assertEqual((child["id"], child["status"]), ("child-1", "Running"))
        self.assertNotIn("private", json.dumps(result))
        state = self.runs.load_state("T-1")
        state["crew_pid"] = None
        self.runs.save_state(state)
        stale, = trace_snapshot(self.runs, _display_status)["tasks"]
        self.assertEqual(stale["status"], "STALE?")
        self.assertEqual(stale["attempts"][0]["children"][0]["status"], "Final state unknown")

    def test_completed_worker_preserves_attempt_and_review_timeline(self):
        self.assertEqual(self.run_task("measured")[0], 0)
        self.assertEqual(crew("accept", "T-1", "--verified", "private rationale")[0], 0)
        task, = trace_snapshot(self.runs, _display_status)["tasks"]
        self.assertEqual((task["status"], task["verdict"]), ("DONE", "ACCEPTED"))
        self.assertTrue(any(event["label"] == "Main verdict · ACCEPTED" and event["at"] for event in task["events"]))
        attempt, = task["attempts"]
        self.assertIn("Turn completed", [event["label"] for event in attempt["events"]])
        self.assertNotIn("private rationale", json.dumps(task))

    def test_opt_in_claude_hook_keeps_lifecycle_not_payload(self):
        payload = {"session_id": "session-1", "agent_id": "agent-1", "hook_event_name": "SubagentStart",
                   "last_assistant_message": "private message", "transcript_path": "private transcript"}
        record_claude_hook(self.runs, payload, "P-plan")
        payload["hook_event_name"] = "SubagentStop"
        record_claude_hook(self.runs, payload)
        saved = self.runs.file("session-1.agent-1.native-trace.json")
        record = json.loads(saved.read_text(encoding="utf-8"))
        record["events"][0]["transcript"] = "private transcript"
        record["events"].append({"at": "private timestamp", "label": "private label"})
        saved.write_text(json.dumps(record), encoding="utf-8")
        session = trace_snapshot(self.runs, _display_status)["sessions"]["session-1"]
        self.assertEqual(session["plan"], "P-plan")
        self.assertEqual(session["children"][0]["status"], "Finished")
        self.assertEqual([event["label"] for event in session["events"]], ["Subagent started", "Subagent stopped"])
        self.assertNotIn("private", json.dumps(session))

    def test_terminal_burst_does_not_hide_late_events(self):
        self.runs.ensure()
        source = self.runs.file("T-1.a1.events.jsonl")
        destination = self.runs.file("T-1.a1.trace.jsonl")
        recorder = ActivityRecorder("codex", source, destination, self.runs.file("T-1.a1.trace-children.json"))
        source.write_bytes(b"x" * (READ_LIMIT * 3) + b'\n{"type":"turn.completed"}')
        recorder.record()
        self.assertFalse(destination.exists())
        recorder.record(finished=True)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["label"], "Turn completed")

    def test_view_reuses_unchanged_metadata_and_drops_removed_records(self):
        self.runs.ensure()
        self.runs.save_state({"task_id": "T-1", "status": "DONE"})
        self.runs.save_state({"task_id": "T-2", "status": "DONE"})
        files = TraceFiles(self.runs)
        from crew.trace import read_json

        with patch("crew.trace.read_json", wraps=read_json) as read:
            trace_snapshot(self.runs, _display_status, files=files)
            trace_snapshot(self.runs, _display_status, files=files)
            self.assertEqual(read.call_count, 2)
            self.runs.save_state({"task_id": "T-2", "status": "FAILED"})
            tasks = trace_snapshot(self.runs, _display_status, files=files)["tasks"]
            self.assertEqual(read.call_count, 3)
            self.assertEqual([task["status"] for task in tasks], ["DONE", "FAILED"])
        self.runs.state_path("T-1").rename(self.runs.file("removed.bak"))
        tasks = trace_snapshot(self.runs, _display_status, files=files)["tasks"]
        self.assertEqual([task["task_id"] for task in tasks], ["T-2"])
        self.assertNotIn("T-1.json", files.cache)

    def test_feed_replays_changes_and_resets_expired_or_foreign_cursor(self):
        self.runs.ensure()
        feed = TraceFeed(self.runs, _display_status)
        self.addCleanup(feed.close)
        first, = feed.after(None)
        feed.refresh()
        self.assertEqual(feed.latest(), first)
        self.runs.save_state({"task_id": "T-1", "status": "RUNNING"})
        feed.refresh()
        self.runs.save_state({"task_id": "T-1", "status": "DONE"})
        feed.refresh()
        second, third = feed.after(first.cursor)
        self.assertEqual(json.loads(second.data)["tasks"][0]["status"], "STALE?")
        self.assertEqual(json.loads(third.data)["tasks"][0]["status"], "DONE")
        with patch("crew.trace_view.REPLAY_LIMIT", 2):
            self.runs.save_state({"task_id": "T-1", "status": "FAILED"})
            feed.refresh()
        self.assertEqual(feed.after(first.cursor), (feed.latest(),))
        self.assertEqual(feed.after("other-server:1"), (feed.latest(),))

    def test_loopback_stream_shares_updates_and_honors_last_event_id(self):
        def event(response):
            while (line := response.readline()).startswith(b":") or line == b"\n":
                pass
            self.assertTrue(line.startswith(b"id: "), line)
            cursor = line[4:].strip().decode("ascii")
            data = response.readline()
            self.assertTrue(data.startswith(b"data: "), data)
            self.assertEqual(response.readline(), b"\n")
            return cursor, json.loads(data[6:])

        self.runs.ensure()
        with create_server(self.runs, _display_status, 0) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address

            def connect():
                return HTTPConnection(host, port, timeout=5)

            try:
                with closing(connect()) as one, closing(connect()) as two:
                    one.request("GET", "/api/events", headers={"Origin": "https://example.invalid"})
                    with one.getresponse() as forbidden:
                        self.assertEqual(forbidden.status, 403)
                    one.request("GET", "/api/events")
                    two.request("GET", "/api/events")
                    with one.getresponse() as first, two.getresponse() as second:
                        self.assertEqual(first.getheader("Content-Type"), "text/event-stream; charset=utf-8")
                        initial, _ = event(first)
                        self.assertEqual(event(second)[0], initial)
                        self.runs.save_state({"task_id": "T-1", "status": "DONE"})
                        finished, data = event(first)
                        self.assertEqual(data["tasks"][0]["status"], "DONE")
                        self.assertEqual(event(second), (finished, data))
                with closing(connect()) as resumed:
                    resumed.request("GET", "/api/events", headers={"Last-Event-ID": initial})
                    with resumed.getresponse() as response:
                        self.assertEqual(event(response), (finished, data))
            finally:
                server.shutdown()
                thread.join()


class TraceQueryTests(Base):
    @contextmanager
    def server(self):
        with create_server(self.runs, _display_status, 0) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield server.server_address
            finally:
                server.shutdown()
                thread.join()

    def test_viewer_serves_the_layout_and_browser_modules(self):
        with self.server() as address, closing(HTTPConnection(*address, timeout=8)) as client:
            for route, mime, marker in (("/", "text/html", 'role="tablist"'),
                                        ("/view.css", "text/css", ".tabs"),
                                        ("/view.js", "text/javascript", "./controls.js"),
                                        ("/controls.js", "text/javascript", "export function workerControls"),
                                        ("/panels.js", "text/javascript", "export function renderTimeline")):
                with self.subTest(route=route):
                    client.request("GET", route)
                    with client.getresponse() as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.getheader("Content-Type"), f"{mime}; charset=utf-8")
                        self.assertIn(marker, response.read().decode("utf-8"))

    def test_shared_terminal_stream_and_input_are_opt_in_and_same_origin(self):
        with self.server() as address, closing(HTTPConnection(*address, timeout=8)) as client:
            client.request("GET", "/terminal")
            with client.getresponse() as response:
                self.assertEqual(response.status, 404)

        terminal = TerminalSession(90, 28)
        terminal.publish("terminal output 한글", 90, 28)
        with create_server(self.runs, _display_status, 0, terminal=terminal) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                address = server.server_address
                with closing(HTTPConnection(*address, timeout=8)) as client:
                    client.request("GET", "/terminal")
                    with client.getresponse() as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(b"Shared Codex terminal", response.read())
                    client.request("GET", "/api/terminal/events")
                    with client.getresponse() as stream:
                        _, initial = self.event(stream)
                        self.assertTrue(initial["reset"])
                        cursor, frame = self.event(stream)
                        self.assertEqual(frame, {"data": "terminal output 한글", "cols": 90, "rows": 28,
                                                 "reset": False})
                        self.assertEqual(stream.readline(), b"event: ready\n")
                    terminal.publish("more output", 100, 30)
                    client.request("GET", "/api/terminal/events", headers={"Last-Event-ID": cursor})
                    with client.getresponse() as stream:
                        _, frame = self.event(stream)
                        self.assertEqual(frame, {"data": "more output", "cols": 100, "rows": 30,
                                                 "reset": False})
                with closing(HTTPConnection(*address, timeout=8)) as client:
                    origin = f"http://{address[0]}:{address[1]}"
                    for source, data, expected in (("https://example.org", "ignored", 403),
                                                   (origin, "\ud800", 400), (origin, "한글\r", 202)):
                        client.request("POST", "/api/terminal/input", json.dumps({"data": data}),
                                       {"Origin": source, "Content-Type": "application/json"})
                        with client.getresponse() as response:
                            self.assertEqual(response.status, expected)
                            response.read()
                    self.assertEqual(terminal.input.get_nowait(), "한글\r")
                    self.assertTrue(terminal.input.empty())
                    terminal.close()
                    client.request("GET", "/api/terminal/events")
                    with client.getresponse() as response:
                        self.assertEqual(response.status, 410)
                        response.read()
            finally:
                terminal.close()
                server.shutdown()
                thread.join()

    def test_board_stream_deduplicates_contracts_and_excludes_registered_tasks(self):
        path = self.contract(tid="T-planned")
        text = path.read_text(encoding="utf-8").replace("\n---\n", '\nplan: "board-plan"\nplan_step: "S1"\n---\n', 1)
        path.write_text(text, encoding="utf-8")
        path.with_name("alias.md").write_text(text, encoding="utf-8")
        self.contract(tid="T-cleaned")
        self.runs.append_history({"task_id": "T-cleaned", "status": "FAILED", "verdict": "ACCEPTED"})
        with self.server() as address, closing(HTTPConnection(*address, timeout=8)) as client:
            client.request("GET", "/api/events")
            with client.getresponse() as stream:
                _, before = self.event(stream)
                self.assertEqual(before["pending_contracts"], [
                    {"task_id": "T-planned", "contract": "T-planned", "plan": "board-plan"},
                ])
                self.runs.save_state({"task_id": "T-planned", "status": "FAILED", "verdict": "ACCEPTED",
                                      "plan": "board-plan", "attempts": [{"n": 2, "status": "FAILED"}]})
                _, after = self.event(stream)
                self.assertEqual(after["pending_contracts"], [])
                task, = after["tasks"]
                self.assertEqual((task["status"], task["verdict"], task["attempts"][-1]["n"]),
                                 ("FAILED", "ACCEPTED", 2))

    def test_shared_terminal_replay_preserves_vt_prefix_after_spooling(self):
        terminal = TerminalSession(80, 24)
        try:
            terminal.publish("\x1b[?1049h", 80, 24)
            for _ in range(80):
                terminal.publish("x" * 8000, 80, 24)
            reset, first = terminal.after(None, 0)
            self.assertTrue(reset)
            self.assertEqual(first[1].data, "\x1b[?1049h")
            reset, rest = terminal.after(first[-1].cursor, 0)
            self.assertFalse(reset)
            self.assertEqual(rest[-1].cursor, terminal.latest.cursor)
        finally:
            terminal.dispose()

    def post(self, address, command, task="", *, project=None, origin=True):
        return self.request(address, {"project": str(self.runs.root) if project is None else project,
                                      "command": command, "task_id": task}, origin=origin)

    def request(self, address, payload, *, origin=True):
        host, port = address
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = f"http://{host}:{port}"
        body = json.dumps(payload)
        with closing(HTTPConnection(host, port, timeout=8)) as client:
            client.request("POST", "/api/commands", body, headers)
            with client.getresponse() as response:
                data = response.read()
                return response.status, json.loads(data) if response.status != 403 else None

    def event(self, response):
        while (line := response.readline()).startswith(b":") or line == b"\n":
            pass
        self.assertTrue(line.startswith(b"id: "), line)
        data = response.readline()
        self.assertTrue(data.startswith(b"data: "), data)
        self.assertEqual(response.readline(), b"\n")
        self.latest_event = line[4:].strip().decode("ascii"), json.loads(data[6:])
        return self.latest_event

    def completed(self, stream, request_id):
        cursor, state = getattr(self, "latest_event", (None, {}))
        while True:
            for record in state.get("commands", []):
                if record["id"] == request_id and record["ended"]:
                    return cursor, record
            cursor, state = self.event(stream)

    def test_queries_validate_project_and_persist_real_cli_output(self):
        self.runs.ensure()
        self.runs.save_state({"task_id": "T-1", "status": "DONE", "agent": "codex", "mode": "write",
                              "attempts": [{"n": 1, "result": "T-1.a1.result.md"}]})
        self.runs.file("T-1.a1.result.md").write_text("## RESULT\nSTATUS: DONE\nfixture result\n", encoding="utf-8")
        # The project is the working directory, never an import source for the query CLI.
        (self.root / "crew.py").write_text("raise RuntimeError('workspace module imported')\n", encoding="utf-8")
        with self.server() as address, closing(HTTPConnection(*address, timeout=8)) as subscriber:
            self.assertEqual(self.post(address, "status", origin=False)[0], 403)
            self.assertEqual(self.post(address, "status", project="wrong-project")[0], 400)
            self.assertEqual(self.post(address, "run")[0], 400)
            self.assertEqual(self.post(address, "result", "--raw")[0], 400)
            self.assertEqual(self.post(address, "check", "missing")[0], 400)
            subscriber.request("GET", "/api/events")
            with subscriber.getresponse() as stream:
                _, state = self.event(stream)
                self.assertEqual(state["project"], str(self.root))
                self.assertEqual(state["commands"], [])
                for command, task, expected in (("status", "", "T-1 | codex"),
                                                ("check", "T-1", "task T-1: DONE"),
                                                ("result", "T-1", "fixture result")):
                    status, accepted = self.post(address, command, task)
                    self.assertEqual(status, 202, accepted)
                    cursor, record = self.completed(stream, accepted["id"])
                    self.assertEqual((record["exit_code"], record["attempt"]), (0, 1 if task else None), record)
                    self.assertIn(expected, "".join(chunk["text"] for chunk in record["output"]))
        with self.server() as address, closing(HTTPConnection(*address, timeout=8)) as resumed:
            resumed.request("GET", "/api/events", headers={"Last-Event-ID": cursor})
            with resumed.getresponse() as stream:
                _, state = self.event(stream)
                self.assertEqual([row["command"] for row in state["commands"]], ["result", "check", "status"])
                self.assertEqual(state["commands"][0], record)

    def test_streams_stdout_and_stderr_while_query_is_running(self):
        # Keep the child open until the client has seen its first output update.
        entry = ("import pathlib, sys, time; print('progress'); signal = pathlib.Path('finish-query'); "
                 "deadline = time.monotonic() + 6; "
                 "exec('while not signal.exists() and time.monotonic() < deadline: time.sleep(0.05)'); "
                 "print('diagnostic', file=sys.stderr); sys.exit(7)")
        with patch("crew.trace_commands.CLI_ENTRY", entry):
            with self.server() as address, closing(HTTPConnection(*address, timeout=8)) as subscriber:
                subscriber.request("GET", "/api/events")
                with subscriber.getresponse() as stream:
                    _, snapshot = self.event(stream)
                    self.assertEqual(snapshot["commands"], [])
                    status, accepted = self.post(address, "status")
                    self.assertEqual(status, 202, accepted)
                    while True:
                        _, snapshot = self.event(stream)
                        record = snapshot["commands"][0]
                        if record["output"]:
                            break
                    self.assertIsNone(record["ended"])
                    self.assertTrue(all(row["channel"] == "stdout" for row in record["output"]))
                    self.assertEqual("".join(row["text"] for row in record["output"]).strip(), "progress")
                    self.assertEqual(self.post(address, "status")[0], 409)
                    (self.root / "finish-query").touch()
                    _, record = self.completed(stream, accepted["id"])
                    self.assertEqual(record["exit_code"], 7)
                    stderr = "".join(row["text"] for row in record["output"] if row["channel"] == "stderr")
                    self.assertEqual(stderr.strip(), "diagnostic")

    def test_rejects_state_result_references_outside_the_run(self):
        self.runs.ensure()
        self.runs.save_state({"task_id": "T-1", "attempts": [{"n": 1, "result": "../other.md"}]})
        with self.server() as address:
            self.assertEqual(self.post(address, "result", "T-1")[0], 400)
        self.assertEqual(list(self.runs.dir.glob(f"*{COMMAND_SUFFIX}")), [])

    def test_check_links_live_retry_and_result_links_saved_attempt(self):
        self.runs.ensure()
        self.runs.save_state({"task_id": "T-1", "status": "RUNNING", "mode": "write", "agent": "codex",
                              "attempts": [{"n": 1, "result": "T-1.a1.result.md"}], "live_attempt": {"n": 2}})
        self.runs.file("T-1.a1.result.md").write_text("## RESULT\nSTATUS: DONE\nAttempt one output\n", encoding="utf-8")
        with self.server() as address, closing(HTTPConnection(*address, timeout=8)) as subscriber:
            subscriber.request("GET", "/api/events")
            with subscriber.getresponse() as stream:
                for command, attempt in (("check", 2), ("result", 1)):
                    status, accepted = self.post(address, command, "T-1")
                    self.assertEqual(status, 202, accepted)
                    _, record = self.completed(stream, accepted["id"])
                    self.assertEqual((record["exit_code"], record["attempt"]), (0, attempt))

    def operation(self, address, command, **target):
        return self.request(address, {"project": str(self.root), "command": command, **target})

    def test_worker_run_requires_a_fresh_preview_and_preserves_the_cli_guard(self):
        path = self.contract(tid="T-run")
        os.environ["FAKE_SCENARIO"] = "done"
        with self.server() as address, closing(HTTPConnection(*address, timeout=8)) as subscriber:
            self.assertEqual(self.operation(address, "run", preview_id="unissued")[0], 400)
            self.assertEqual(self.operation(address, "run-preview", contract="../external")[0], 400)
            subscriber.request("GET", "/api/events")
            with subscriber.getresponse() as stream:
                _, initial = self.event(stream)
                self.assertIn("T-run", initial["contracts"])
                status, preview = self.operation(address, "run-preview", contract="T-run")
                self.assertEqual(status, 200, preview)
                self.assertIn("codex", preview["description"])
                token = preview["preview_id"]
                self.assertEqual(self.operation(address, "cancel", preview_id=token)[0], 400)
                path.write_text(path.read_text(encoding="utf-8") + "\nChanged after preview.\n", encoding="utf-8")
                status, accepted = self.operation(address, "run", preview_id=token)
                self.assertEqual(status, 202, accepted)
                _, record = self.completed(stream, accepted["id"])
                self.assertEqual(record["exit_code"], 2, record)
                self.assertIn("changed after preview", "".join(row["text"] for row in record["output"]))
                self.assertIsNone(self.runs.load_state("T-run"))
                self.assertEqual(self.operation(address, "run", preview_id=token)[0], 400)
                self.contract(tid="T-cost", effort="high")
                self.assertEqual(self.operation(address, "run-preview", contract="T-cost")[0], 400)
                status, renewed = self.operation(address, "run-preview", contract="T-run")
                self.assertEqual(status, 200, renewed)
                status, accepted = self.operation(address, "run", preview_id=renewed["preview_id"])
                self.assertEqual(status, 202, accepted)
                _, record = self.completed(stream, accepted["id"])
                self.assertEqual(record["exit_code"], 0, record)
                self.assertEqual(self.runs.load_state("T-run")["status"], "DONE")

    def test_worker_run_and_confirmed_cancellation_share_project_timeline(self):
        self.contract(tid="T-browser-run")
        os.environ["FAKE_SCENARIO"] = "sleep"
        with self.server() as address, closing(HTTPConnection(*address, timeout=12)) as subscriber:
            subscriber.request("GET", "/api/events")
            with subscriber.getresponse() as stream:
                status, preview = self.operation(address, "run-preview", contract="T-browser-run")
                self.assertEqual(status, 200, preview)
                status, launch = self.operation(address, "run", preview_id=preview["preview_id"])
                self.assertEqual(status, 202, launch)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    status, preview = self.operation(address, "cancel-preview", task_id="T-browser-run")
                    if status == 200:
                        break
                    time.sleep(0.1)
                self.assertEqual(status, 200, preview)
                state = self.runs.load_state("T-browser-run")
                self.assertIn(f"PID {state['worker_pid']}", preview["description"])
                self.assertEqual(state["status"], "RUNNING")
                status, queried = self.post(address, "status")
                self.assertEqual(status, 202, queried)
                self.assertEqual(self.completed(stream, queried["id"])[1]["exit_code"], 0)
                status, cancelled = self.operation(address, "cancel", preview_id=preview["preview_id"])
                self.assertEqual(status, 202, cancelled)
                self.assertEqual(self.operation(address, "cancel", preview_id=preview["preview_id"])[0], 400)
                _, stopped = self.completed(stream, cancelled["id"])
                self.assertEqual((stopped["exit_code"], stopped["attempt"]), (0, 1), stopped)
                _, finished = self.completed(stream, launch["id"])
                self.assertEqual(finished["exit_code"], 130, finished)
                self.assertEqual(self.runs.load_state("T-browser-run")["status"], "CANCELLED")

class PendingContractTests(Base):
    """pending_contracts edge cases for the Kanban Not run column."""

    def commands(self):
        from crew.trace_commands import TraceCommands
        return TraceCommands(self.runs)

    def test_active_lock_and_corrupt_contract_are_not_pending(self):
        self.contract(tid="T-open")
        self.contract(tid="T-active")
        self.runs.active.mkdir(parents=True, exist_ok=True)
        self.runs.active_path("T-active").write_text(json.dumps({"task_id": "T-active"}), encoding="utf-8")
        (self.runs.contracts / "T-broken.md").write_text("no frontmatter", encoding="utf-8")
        commands = self.commands()
        pending = commands.pending_contracts(commands.contracts(), set())
        self.assertEqual([row["task_id"] for row in pending], ["T-open"])

    def test_linked_history_fails_closed(self):
        self.contract(tid="T-open")
        commands = self.commands()
        real = type(self.runs.history).resolve

        def resolve(path, *args, **kwargs):
            resolved = real(path, *args, **kwargs)
            return resolved.with_name("elsewhere.jsonl") if path.name == "history.jsonl" else resolved
        with patch.object(type(self.runs.history), "resolve", resolve):
            self.assertEqual(commands.pending_contracts(commands.contracts(), set()), [])
        self.assertEqual([row["task_id"] for row in commands.pending_contracts(commands.contracts(), set())],
                         ["T-open"])
