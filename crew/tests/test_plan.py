"""Main-agent plans (SPEC 4.13): template, validation, contract links, show, handoff."""

import json
import os
from pathlib import Path

from test_crew import Base, crew

PLAN = """---
plan_id: {pid}
status: {status}
---
## Goal
Fix the CSV export bug and get an independent review.

## Steps
| step | status | executor | depends_on | task | verify |
| --- | --- | --- | --- | --- | --- |
{rows}

## Approvals
- none
"""

ROWS = """| S1 | done | main-subagent | - | Locate the exporter code | Paths cited exist |
| S2 | todo | crew:codex/default/low | S1 | Independent review of the fix | Findings checked against source |
| S3 | todo | main | S2 | Apply fixes and run tests | Test suite passes |"""


class PlanHelpers:
    """Shared helpers; mixed into test classes so their test methods are not inherited twice."""

    def write_plan(self, pid="P1", status="active", rows=ROWS) -> Path:
        self.runs.ensure()
        path = self.runs.plan_path(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PLAN.format(pid=pid, status=status, rows=rows), encoding="utf-8")
        return path

    def linked_contract(self, tid="T-1", plan="P1", step="S2", agent="codex") -> Path:
        p = self.contract(tid=tid, agent=agent)
        text = p.read_text(encoding="utf-8").replace(
            "ephemeral: false\n---", f"ephemeral: false\nplan: {plan}\nplan_step: {step}\n---", 1)
        p.write_text(text, encoding="utf-8")
        return p

    def plan_errors(self, pid="P1") -> list[str]:
        code, out, _ = crew("plan", "check", pid, "--json")
        data = json.loads(out)
        self.assertEqual(code, 0 if data["ok"] else 2)
        return data["errors"]


class PlanTests(PlanHelpers, Base):
    def test_new_duplicate_and_template_needs_filling(self):
        code, out, _ = crew("plan", "new", "P1")
        self.assertEqual(code, 0)
        self.assertTrue(Path(out.strip()).is_file())
        self.assertEqual(crew("plan", "new", "P1")[0], 5)
        errors = self.plan_errors()
        self.assertTrue(any("Goal" in e for e in errors))
        self.assertTrue(any("task is empty or still TODO" in e for e in errors))
        self.assertEqual(crew("plan", "new", "../escape")[0], 2)

    def test_valid_plan_and_structural_errors(self):
        self.write_plan()
        self.assertEqual(self.plan_errors(), [])
        bad = (ROWS.replace("crew:codex/default/low", "codex")
               .replace("| S3 | todo | main | S2 |", "| S1 | later | main | S9 |"))
        self.write_plan(rows=bad)
        errors = " | ".join(self.plan_errors())
        for expected in ("executor must be", "duplicate step id: S1", "status must be one of",
                         "depends on unknown step 'S9'"):
            self.assertIn(expected, errors)
        cyc = ROWS.replace("| S1 | done | main-subagent | - |", "| S1 | done | main-subagent | S3 |")
        self.write_plan(rows=cyc)
        self.assertTrue(any(e.startswith("dependency cycle") for e in self.plan_errors()))
        self.write_plan(pid="P2")
        self.assertEqual(self.plan_errors("P2"), [])
        self.runs.plan_path("P2").write_text(PLAN.format(pid="OTHER", status="active", rows=ROWS), encoding="utf-8")
        self.assertTrue(any("does not match the file name" in e for e in self.plan_errors("P2")))

    def test_contract_links(self):
        self.write_plan()
        code, out, _ = crew("contract", "check", str(self.linked_contract()), "--json")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["warnings"], [])
        code, out, _ = crew("contract", "check", str(self.linked_contract(tid="T-2", agent="claude")), "--json")
        self.assertEqual(code, 0)
        self.assertIn("names codex but the contract uses claude", " ".join(json.loads(out)["warnings"]))
        code, out, _ = crew("contract", "check", str(self.linked_contract(tid="T-3", step="S9")))
        self.assertEqual(code, 2)
        self.assertIn("has no step 'S9'", out)
        p = self.contract(tid="T-4")
        p.write_text(p.read_text(encoding="utf-8").replace("ephemeral: false\n---", "ephemeral: false\nplan: P1\n---"),
                     encoding="utf-8")
        self.assertEqual(crew("contract", "check", str(p))[0], 2)
        self.write_plan(rows=ROWS.replace("Locate the exporter code", "TODO"))
        code, out, _ = crew("contract", "check", str(self.linked_contract(tid="T-5")))
        self.assertEqual(code, 2)
        self.assertIn("is invalid; run: crew plan check P1", out)

    def test_contract_new_plan_options(self):
        self.assertEqual(crew("contract", "new", "T-7", "--plan", "P1")[0], 2)
        code, out, _ = crew("contract", "new", "T-8", "--plan", "P1", "--step", "S2")
        self.assertEqual(code, 0)
        text = Path(out.strip()).read_text(encoding="utf-8")
        self.assertIn('plan: "P1"\nplan_step: "S2"\n', text)

    def test_show_links_runs_ready_steps_and_notes(self):
        self.write_plan()
        os.environ["FAKE_SCENARIO"] = "done"
        code, _, err = crew("run", "--contract", str(self.linked_contract()))
        self.assertEqual(code, 0, err)
        self.assertEqual(crew("accept", "T-1", "--verified", "checked findings")[0], 0)
        view = json.loads(crew("plan", "show", "P1", "--json")[1])
        s2 = next(s for s in view["steps"] if s["step"] == "S2")
        self.assertEqual([(r["task_id"], r["verdict"]) for r in s2["runs"]], [("T-1", "ACCEPTED")])
        self.assertEqual(view["ready"], ["S2"])
        self.assertIn("S2: has an accepted run but is marked todo", view["notes"])
        self.write_plan(rows=ROWS.replace("| S2 | todo |", "| S2 | done |"))
        view = json.loads(crew("plan", "show", "P1", "--json")[1])
        self.assertEqual((view["ready"], view["notes"]), (["S3"], []))
        self.write_plan(rows=ROWS.replace("| S2 | todo | crew:codex/default/low |", "| S2 | done | crew:claude |")
                        .replace("| S3 | todo | main |", "| S3 | done | crew:agy |"))
        notes = json.loads(crew("plan", "show", "P1", "--json")[1])["notes"]
        self.assertIn("S2: executor names claude but linked runs used another agent: T-1", notes)
        self.assertIn("S3: marked done without an accepted crew run", notes)
        self.write_plan(rows=ROWS.replace("| S2 | todo | crew:codex/default/low |", "| S2 | done | crew:claude |"))
        listing = json.loads(crew("plan", "show", "--json")[1])
        self.assertEqual(listing, [{"plan_id": "P1", "status": "active", "steps": 3, "closed": 2}])

    def test_handoff_lists_active_and_linked_plans(self):
        self.write_plan()
        self.write_plan(pid="P-old", status="done")
        os.environ["FAKE_SCENARIO"] = "done"
        crew("run", "--contract", str(self.linked_contract()))
        code, out, _ = crew("handoff", "--agent", "claude", "--task", "plan", "--status", "In progress", "--lang", "en")
        self.assertEqual(code, 0)
        text = Path(out.strip()).read_text(encoding="utf-8")
        self.assertIn("## Plans (crew)\n- P1 (active): S1 done main-subagent; S2 todo crew:codex/default/low [T-1 DONE/unjudged]", text)
        self.assertNotIn("P-old", text)


class ReviewFindingTests(PlanHelpers, Base):
    """Regression cases from the 2026-11-21 codex review (rev-uncommitted-1)."""

    def test_secrets_are_redacted_in_check_and_show(self):
        secret = "sk-" + "a" * 25
        rows = ROWS.replace("Locate the exporter code", "use " + secret) + f"\n| S4 | todo | main | - | broken {secret} |"
        self.write_plan(rows=rows)
        for args in (("plan", "check", "P1"), ("plan", "check", "P1", "--json"),
                     ("plan", "show", "P1"), ("plan", "show", "P1", "--json")):
            out = crew(*args)[1]
            self.assertNotIn(secret, out, args)
        self.assertIn("[REDACTED]", crew("plan", "show", "P1")[1])

    def test_numeric_looking_ids_survive_templates(self):
        self.assertEqual(crew("plan", "new", "007")[0], 0)
        errors = self.plan_errors("007")
        self.assertFalse(any("plan_id" in e for e in errors), errors)
        code, out, _ = crew("contract", "new", "008", "--plan", "P1", "--step", "007")
        self.assertEqual(code, 0)
        self.assertIn('task_id: "008"', Path(out.strip()).read_text(encoding="utf-8"))
        self.write_plan(rows="| 007 | todo | crew:codex | - | Review | Findings checked |")
        p = Path(out.strip())
        p.write_text(p.read_text(encoding="utf-8").replace("TODO: one or two sentences describing the goal.", "Goal.")
                     .replace("TODO: conditions that mean the task is complete.", "Done."), encoding="utf-8")
        code, out, _ = crew("contract", "check", str(p), "--json")
        self.assertEqual(code, 0, out)

    def test_unreadable_plan_does_not_break_handoff_or_check(self):
        self.write_plan()
        self.runs.plan_path("BAD").write_bytes(b"\xff\xfe not utf-8")
        code, out, _ = crew("handoff", "--agent", "t", "--task", "x", "--status", "Done", "--lang", "en")
        self.assertEqual(code, 0)
        self.assertIn("P1 (active)", Path(out.strip()).read_text(encoding="utf-8"))
        code, _, err = crew("plan", "check", "BAD")
        self.assertEqual(code, 2)
        self.assertIn("cannot read plan", err)
        self.assertIn("unreadable", crew("plan", "show")[1])

    def test_step_named_step_is_a_row(self):
        self.write_plan(rows=ROWS + "\n| STEP | todo | main | S3 | Final check | Suite passes |")
        self.assertEqual(self.plan_errors(), [])
        self.assertIn("STEP", [s["step"] for s in json.loads(crew("plan", "show", "P1", "--json")[1])["steps"]])

    def test_plan_new_leaves_no_temp_file(self):
        crew("plan", "new", "P9")
        self.assertEqual([p.name for p in self.runs.plans.iterdir() if p.name.startswith(".")], [])
        self.assertEqual(crew("plan", "new", "P9")[0], 5)


def make_dir_link(link: Path, target: Path) -> None:
    """Directory junction on Windows (no admin rights needed), symlink elsewhere."""
    if os.name == "nt":
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


class ContainmentTests(Base):
    """`.agent-runs` subfolders that resolve outside the project are refused (review finding 2)."""

    def setUp(self):
        super().setUp()
        self.outside = self.root.parent / (self.root.name + "-outside")
        self.outside.mkdir()
        self.runs.ensure()

    def tearDown(self):
        for p in (self.runs.plans, self.runs.contracts):
            if p.is_dir() and os.path.realpath(p) != os.path.abspath(p):
                os.rmdir(p) if os.name == "nt" else os.unlink(p)
        super().tearDown()
        import shutil
        shutil.rmtree(self.outside, ignore_errors=True)

    def test_plans_junction_is_refused(self):
        make_dir_link(self.runs.plans, self.outside)
        code, _, err = crew("plan", "new", "P1")
        self.assertEqual(code, 3)
        self.assertIn("resolves outside the project", err)
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertEqual(crew("plan", "show")[0], 3)
        p = self.contract()
        p.write_text(p.read_text(encoding="utf-8").replace("ephemeral: false\n---",
                     "ephemeral: false\nplan: P1\nplan_step: S1\n---"), encoding="utf-8")
        code, out, _ = crew("contract", "check", str(p))
        self.assertEqual(code, 2)
        self.assertIn("resolves outside the project", out)
        code, out, _ = crew("handoff", "--agent", "t", "--task", "x", "--status", "Done", "--lang", "en")
        self.assertEqual(code, 0)
        self.assertIn("plans skipped", Path(out.strip()).read_text(encoding="utf-8"))

    def test_contracts_junction_is_refused(self):
        os.rmdir(self.runs.contracts)
        make_dir_link(self.runs.contracts, self.outside)
        code, _, err = crew("contract", "new", "T-9")
        self.assertEqual(code, 3)
        self.assertEqual(list(self.outside.iterdir()), [])
