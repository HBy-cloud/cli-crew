"""Regression cases from the four-model review."""

import json
import os
import subprocess
import sys
import time
from unittest.mock import Mock, patch

from test_crew import Base, HERE, crew
from crew import citations, classify, procutil, runner, snapshot


class PanelTests(Base):
    def agy_events(self, tid, events):
        script = "import sys; print('fake-agy' if '--version' in sys.argv else " + repr(
            "\n".join(json.dumps(e) for e in events)) + ")"
        os.environ["CREW_BIN_AGY"] = json.dumps([sys.executable, "-c", script])
        return self.run_task(agent="agy", tid=tid)

    def test_terminal_error_and_recovered_intermediate_error(self):
        footer = "## RESULT\nSTATUS: success\nNEXT: review the ## RESULT section"
        terminal = {"event": "result", "result": {"status": "ERROR", "response": footer}}
        code, state = self.agy_events("error", [terminal])
        self.assertEqual((code, state["status"], state["worker_status"]), (10, "FAILED", "success"))
        terminal["result"]["status"] = "SUCCESS"
        step = {"event": "step_update", "step_update": {"state": "ERROR", "error": "access is denied"}}
        code, state = self.agy_events("recovered", [step, terminal])
        self.assertEqual((code, state["status"]), (0, "DONE"))
        self.assertIsNone(classify.parse_result_block("## RESULT\nSTATUS: success | partial | failed | blocked")["status"])

    def test_raw_fallback_citations_and_legacy_results(self):
        body = "Checked README.md:1 and missing.py:99. See `README.md:5`."
        footer = "## RESULT\nSTATUS: success\nNEXT: see above"
        _, state = self.agy_events("review", [{"event": "result", "result": {"status": "SUCCESS",
                                                                            "response": body + "\n" + footer}}])
        self.assertEqual(crew("result", "review")[1].strip(), footer)
        self.assertIn(body, crew("result", "review", "--raw")[1])
        flagged = json.loads(crew("check", "review", "--json")[1])["unverified_citations"]
        self.assertEqual(flagged, ["missing.py:99", "README.md:5"])
        self.assertEqual(state["status"], "DONE")
        self.runs.file(state["attempts"][0]["events"]).unlink()
        self.assertEqual(crew("result", "review", "--raw")[1].strip(), footer)

    def test_intermediate_footer_cannot_complete_final_step(self):
        def step(n, text):
            return {"event": "step_update", "step_update": {"step_type": "agent_response",
                    "step_index": n, "text_delta": text}}
        earlier = "Earlier thought\n## RESULT\nSTATUS: success"
        later = "Still investigating"
        _, state = self.agy_events("steps", [step(1, earlier), step(2, later),
            {"event": "result", "result": {"status": "SUCCESS", "response": earlier + "\n" + later}}])
        self.assertEqual(state["status"], "PARTIAL")
        self.assertIn(earlier, crew("result", "steps", "--raw")[1])
        self.assertNotIn(earlier, crew("result", "steps")[1])

    def test_citations_skip_secret_paths_and_check_local_markdown_links(self):
        link = (self.root / "file with spaces.md").as_uri()
        (self.root / "file with spaces.md").write_text("first\n", encoding="utf-8")
        text = f"[source]({link}#L1) [bad]({link}#L2) [web](https://example.com/a.py:1) `.env:42`"
        self.assertEqual(citations.unverified(self.root, text), [link + "#L2"])

    def test_snapshot_mode_change_in_finalization_and_recheck(self):
        with patch.object(snapshot, "_git_dirty", side_effect=[{}, None]):
            code, state = self.run_task()
        self.assertEqual((code, state["status"], state["boundary_violations"]), (10, "DONE_UNVERIFIED", []))
        self.assertIn("mode changed", state["boundary_check"])
        self.assertEqual(json.loads(crew("check", "T-1", "--recheck", "--json")[1])["boundary_violations"], "UNKNOWN")

    def test_compare_live_archived_and_unmeasured_cost(self):
        self.run_task("measured", tid="one")
        self.run_task(tid="two", agent="claude")
        self.assertEqual(crew("accept", "one", "--verified", "checked files")[0], 0)
        def compare():
            code, out, _ = crew("compare", "one", "two", "--json")
            self.assertEqual(code, 0)
            return json.loads(out)
        one, two = compare()
        self.assertEqual((one["tokens"], one["cost_usd"], one["main_verification"]), (100, None, "checked files"))
        self.assertEqual(two["token_scope"], "input+output+cache creation+cache reads")
        self.assertEqual(crew("clean", "--runs", "--older-than", "0", "--yes")[0], 0)
        self.assertFalse(self.runs.state_path("one").exists())
        self.assertEqual(compare()[0]["main_verification"], "checked files")
        self.assertEqual(crew("compare", "one", "absent")[0], 2)

    def test_cancel_refuses_unidentified_or_changed_target(self):
        state = {"task_id": "orphan", "status": "ORPHANED", "crew_pid": 12, "worker_pid": 34,
                 "worker_create_time": None}
        self.runs.ensure()
        with patch.object(procutil, "stop_verified_tree") as stop:
            self.runs.save_state(state)
            self.assertEqual(crew("cancel", "orphan", "--yes")[0], 3)
            state["worker_create_time"] = 42
            self.runs.save_state(state)
            with patch.object(procutil, "same_process", return_value=True):
                self.assertEqual(crew("cancel", "orphan", "--target", "34:41", "--yes")[0], 3)
            stop.assert_not_called()

    def test_cancel_running_controller_records_attempt(self):
        contract = self.contract()
        env = dict(os.environ, FAKE_SCENARIO="sleep", PYTHONPATH=str(HERE.parent / "src"))
        with subprocess.Popen([sys.executable, "-m", "crew", "run", "--contract", str(contract)],
                              cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as controller:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = self.runs.load_state("T-1") or {}
                if state.get("worker_pid"):
                    break
                time.sleep(0.05)
            code, preview, _ = crew("cancel", "T-1")
            self.assertEqual(code, 0, preview)
            identity = preview.split("--target ")[1].split()[0]
            self.assertEqual(crew("cancel", "T-1", "--target", identity, "--yes")[0], 0)
            controller.communicate(timeout=15)
            self.assertEqual(controller.returncode, 130)
        self.assertEqual(self.runs.load_state("T-1")["attempts"][0]["status"], "CANCELLED")
        self.assertFalse(self.runs.list_active())

    def test_unconfirmed_kill_keeps_live_run_under_controller(self):
        marker = self.root / "example.cancel"
        marker.write_text("123:45", encoding="utf-8")
        process = Mock(pid=123)
        process.poll.side_effect = [None, 0]
        with patch.object(procutil, "stop_verified_tree", return_value=False) as stop:
            self.assertEqual(runner._wait(process, 10, marker, 45), (0, False))
        stop.assert_called_once_with(123, 45)
        self.assertFalse(marker.exists())

    def test_orphan_cancel_only_stops_verified_worker(self):
        self.runs.ensure()
        worker = procutil.spawn([sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(self.root),
                                env=dict(os.environ), stdin_path=None, stdout_path=str(self.root / "out"),
                                stderr_path=str(self.root / "err"))
        try:
            state = {"task_id": "orphan", "status": "ORPHANED", "mode": "read", "crew_pid": 999999,
                     "worker_pid": worker.pid, "worker_create_time": procutil.create_time(worker.pid)}
            self.runs.save_state(state)
            code, preview, _ = crew("cancel", "orphan")
            self.assertEqual(code, 0)
            self.assertIsNone(worker.poll())
            kill = procutil.kill_tree
            with patch.object(procutil, "kill_tree", wraps=kill) as calls:
                identity = preview.split("--target ")[1].split()[0]
                self.assertEqual(crew("cancel", "orphan", "--target", identity, "--yes")[0], 0)
                calls.assert_called_once_with(worker.pid)
            worker.wait(timeout=5)
            self.assertEqual(self.runs.load_state("orphan")["status"], "CANCELLED")
        finally:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
