"""Main-agent plans (SPEC 4.13): template, validation, contract links, show, handoff."""

import json
import os
from pathlib import Path

from test_crew import Base, crew
from crew import plan as pl  # noqa: E402  (test_crew puts src on sys.path)

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

    def test_template_has_revisions_section(self):
        code, out, _ = crew("plan", "new", "P9")
        self.assertEqual(code, 0)
        text = Path(out.strip()).read_text(encoding="utf-8")
        self.assertIn("## Revisions", text)
        self.assertIn("WHOLE task", text)
        self.assertEqual(crew("plan", "check", "P9", "--json")[0], 2)

    def test_dropped_step_needs_a_revision_entry(self):
        rows = ROWS.replace("| S3 | todo |", "| S3 | dropped |")
        self.write_plan(rows=rows)
        code, out, _ = crew("plan", "check", "P1", "--json")
        self.assertIn("Revisions table has no entry", " ".join(json.loads(out)["warnings"]))
        path = self.runs.plan_path("P1")
        extra = ["", "## Revisions", "| date | change | reason or evidence |", "| --- | --- | --- |",
                 "| 2026-11-21 | dropped S3 | the review found nothing to fix |", ""]
        path.write_text(path.read_text(encoding="utf-8") + "\n".join(extra), encoding="utf-8")
        code, out, _ = crew("plan", "check", "P1", "--json")
        self.assertNotIn("Revisions table has no entry", " ".join(json.loads(out)["warnings"]))

    def test_blocked_step_is_valid_and_not_ready_and_blocks_done(self):
        rows = ROWS.replace("| S2 | todo |", "| S2 | blocked |")
        self.write_plan(rows=rows)
        self.assertEqual(self.plan_errors(), [])
        code, out, _ = crew("plan", "show", "P1", "--json")
        self.assertNotIn("S2", json.dumps(json.loads(out).get("ready", [])))
        self.write_plan(status="done", rows=rows)
        code, out, _ = crew("plan", "check", "P1", "--json")
        self.assertIn("S2", " ".join(json.loads(out)["warnings"]))

    def test_stopped_plan_without_newer_handoff_gets_a_note(self):
        rows = ROWS.replace("| S2 | todo |", "| S2 | blocked |")
        path = self.write_plan(rows=rows)
        old = path.stat().st_mtime - 60
        os.utime(path, (old, old))
        code, out, _ = crew("plan", "check", "P1", "--json")
        self.assertIn("no handoff is newer", " ".join(json.loads(out)["warnings"]))
        handoff = self.root / "handoffs" / "2026-11-21" / "1200-claude-stopped.md"
        handoff.parent.mkdir(parents=True)
        handoff.write_text("# handoff\n", encoding="utf-8")
        code, out, _ = crew("plan", "check", "P1", "--json")
        self.assertNotIn("no handoff is newer", " ".join(json.loads(out)["warnings"]))
        self.write_plan(rows=ROWS)  # active plan with no blocked step: no note
        os.utime(path, (old, old))
        handoff.unlink()
        code, out, _ = crew("plan", "check", "P1", "--json")
        self.assertNotIn("no handoff is newer", " ".join(json.loads(out)["warnings"]))

    def test_run_note_when_no_active_cplan(self):
        os.environ["FAKE_SCENARIO"] = "done"
        code, out, _ = crew("run", "--contract", str(self.contract()))
        self.assertEqual(code, 0)
        self.assertIn("NOTE: no active cplan", out)
        self.write_plan()
        os.environ["FAKE_SCENARIO"] = "done"
        code, out, _ = crew("run", "--contract", str(self.linked_contract(tid="T-2")))
        self.assertEqual(code, 0)
        self.assertNotIn("NOTE: no active cplan", out)

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

    def test_reviewer_step_contract_needs_review_kind(self):
        path = self.write_plan()
        path.write_text(path.read_text(encoding="utf-8")
                        .replace("| task | verify |\n| --- | --- | --- | --- | --- | --- |",
                                 "| task | verify | reviews |\n| --- | --- | --- | --- | --- | --- | --- |")
                        .replace("checked against source |", "checked against source | S1 |")
                        .replace("Paths cited exist |", "Paths cited exist | - |")
                        .replace("Test suite passes |", "Test suite passes | - |"), encoding="utf-8")
        p = self.linked_contract()
        code, out, _ = crew("contract", "check", str(p))
        self.assertEqual(code, 0, out)
        self.assertIn("is a reviewer step (reviews S1); set task_kind: review", out)
        p.write_text(p.read_text(encoding="utf-8").replace("ephemeral: false\n", "ephemeral: false\ntask_kind: review\n", 1),
                     encoding="utf-8")
        self.assertNotIn("is a reviewer step", crew("contract", "check", str(p))[1])

    def test_plan_model_must_match_contract(self):
        self.write_plan()  # S2 is crew:codex/default/low: the model is not compared; effort is never compared
        p = self.linked_contract()
        p.write_text(p.read_text(encoding="utf-8").replace("effort: low", "effort: medium", 1), encoding="utf-8")
        self.assertNotIn("names", crew("contract", "check", str(p))[1])
        self.write_plan(rows=ROWS.replace("crew:codex/default/low", "crew:codex/gpt-5.5"))
        self.assertNotIn("names model", crew("contract", "check", str(p))[1])  # model: null is not compared
        p.write_text(p.read_text(encoding="utf-8").replace("model: null", "model: gpt-5.4", 1), encoding="utf-8")
        self.assertIn("names model gpt-5.5 but the contract uses gpt-5.4", crew("contract", "check", str(p))[1])
        p.write_text(p.read_text(encoding="utf-8").replace("model: gpt-5.4", "model: GPT-5.5", 1), encoding="utf-8")
        self.assertNotIn("names model", crew("contract", "check", str(p))[1])

    def test_observed_models_judge_review_independence(self):
        plan = pl.Plan(path=Path("x"), fields={}, sections={}, steps=[
            pl.Step(step="S1", status="done", executor="crew:codex", depends_on=[], task="a", verify="v", reviews=[]),
            pl.Step(step="S2", status="done", executor="crew:agy", depends_on=["S1"], task="r", verify="v",
                    reviews=["S1"])])

        def notes(author, reviewer, verdict="ACCEPTED"):
            links = {"S1": [{"task_id": "A", "agent": "codex", "verdict": "ACCEPTED", "model": author}],
                     "S2": [{"task_id": "B", "agent": "agy", "verdict": verdict, "model": reviewer}]}
            return [n for n in pl.step_notes(plan, links) if "review" in n]
        self.assertEqual(notes("gpt-6.2-sol", "gemini-4-neon-low"), [])
        self.assertIn("same model as S1 (gpt-6.2-sol)", notes("gpt-6.2-sol", "GPT-6.2-sol")[0])
        self.assertIn("Claude models", notes("claude-sonnet-5-5", "claude-opus-5-5")[0])
        self.assertIn("not recorded", notes("gpt-6.2-sol", None)[0])
        self.assertEqual(notes("gpt-6.2-sol", "gpt-6.2-sol", verdict="REJECTED"), [])
        self.assertEqual(notes("a", "x,b"), [])
        self.assertTrue(notes("a,b", "b"))

    def test_step_status_drift_notes(self):
        self.write_plan()
        os.environ["FAKE_SCENARIO"] = "done"
        self.assertEqual(crew("run", "--contract", str(self.linked_contract()))[0], 0)
        notes = json.loads(crew("plan", "show", "P1", "--json")[1])["notes"]
        self.assertIn("S2: has linked runs but is still marked todo; set it to doing", notes)
        self.write_plan(rows=ROWS.replace("| S2 | todo |", "| S2 | done |"))
        notes = json.loads(crew("plan", "show", "P1", "--json")[1])["notes"]
        self.assertIn("S2: marked done but runs are not judged yet: T-1", notes)

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
        self.assertEqual((view["ready"], [n for n in view["notes"] if "no reviewer step" not in n]), (["S3"], []))
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
    """Regression cases from the 2026-11-21 codex review."""

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


PLAN7 = """---
plan_id: P2
status: active
---
## Goal
Fix the bug and have it reviewed independently.

## Steps
| step | status | executor | depends_on | task | verify | reviews |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | done | crew:codex | - | Implement the fix | tests pass | - |
| R1 | todo | crew:codex | S1 | Review S1 | PASS verdict | S1 |
| R2 | todo | main | S1 | Review S1 | PASS verdict | S1 |
| S3 | todo | crew:claude | R1 | Write the report | file exists | - |

## Approvals
- none
"""


class ReviewerTests(PlanHelpers, Base):
    def warnings(self, pid="P2"):
        code, out, _ = crew("plan", "check", pid, "--json")
        return json.loads(out)["warnings"]

    def test_reviewer_rules(self):
        self.runs.ensure()
        path = self.runs.plan_path("P2")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PLAN7, encoding="utf-8")
        text = " | ".join(self.warnings())
        self.assertIn("step R1: the reviewer uses the same model (codex/default) as the author of S1", text)
        self.assertIn("step R2: the reviewer is main itself", text)
        self.assertIn("step S3 has no reviewer step", text)
        self.assertNotIn("step S1 has no reviewer step", text)
        path.write_text(PLAN7.replace("## Approvals", "## Revisions\n| date | change | reason or evidence |\n| --- | --- | --- |\n"
                                      "| 2026-11-21 | S3 review skipped | trivial report, the user reads it | \n\n## Approvals"),
                        encoding="utf-8")
        text = " | ".join(self.warnings())
        self.assertNotIn("step S3 has no reviewer step", text)
        self.assertIn("step S3: 'small' or 'simple' is not an accepted reason", text)
        self.assertIn("no other model or CLI available", text)
        path.write_text(PLAN7.replace("## Approvals", "## Revisions\n| date | change | reason or evidence |\n| --- | --- | --- |\n"
                                      "| 2026-11-21 | S3 검증 생략 | 단순 보고서이지만 다른 모델 없음 | \n\n## Approvals"),
                        encoding="utf-8")
        self.assertNotIn("not an accepted reason", " | ".join(self.warnings()))
        path.write_text(PLAN7.replace("## Approvals", "## Revisions\n| date | change | reason or evidence |\n| --- | --- | --- |\n"
                                      "| 2026-11-21 | S3 review skipped | token budget is used up, no budget for another review | \n\n## Approvals"),
                        encoding="utf-8")
        text = " | ".join(self.warnings())
        self.assertNotIn("step S3 has no reviewer step", text)
        self.assertNotIn("not an accepted reason", text)

    def test_independent_main_steps_need_parallel_or_a_reason(self):
        self.runs.ensure()
        path = self.runs.plan_path("P2")
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = ("| S1 | todo | main | - | read a | ok | - |\n| S2 | todo | main | - | read b | ok | - |\n"
                "| S3 | todo | main | S1, S2 | edit | ok | - |\n| R1 | todo | crew:codex | S3 | review | ok | S1, S2, S3 |\n")
        def plan(revisions=""):
            return ("---\nplan_id: \"P2\"\nstatus: active\n---\n## Goal\ng\n\n## Steps\n"
                    "| step | status | executor | depends_on | task | verify | reviews |\n| --- | --- | --- | --- | --- | --- | --- |\n"
                    + rows + "\n## Revisions\n| date | change | reason or evidence |\n| --- | --- | --- |\n" + revisions
                    + "\n## Approvals\n- none\n")
        path.write_text(plan(), encoding="utf-8")
        self.assertIn("steps S1, S2 run on main but do not depend on each other", " | ".join(self.warnings()))
        path.write_text(plan("| 2026-11-26 | no parallel | small steps |\n"), encoding="utf-8")
        self.assertIn("'small' or 'simple' is not an accepted reason to keep independent work", " | ".join(self.warnings()))
        path.write_text(plan("| 2026-11-26 | no parallel | S1 and S2 run shell tests |\n"), encoding="utf-8")
        self.assertNotIn("do not depend on each other", " | ".join(self.warnings()))
        path.write_text(plan().replace("| S2 | todo | main | - |", "| S2 | todo | main | S1 |"), encoding="utf-8")
        self.assertNotIn("do not depend on each other", " | ".join(self.warnings()))

    def test_reviewer_independence_by_model(self):
        from crew.plan import _same_model
        self.assertIsNone(_same_model("crew:codex/gpt-a/low", "crew:codex/gpt-b"))
        self.assertIsNone(_same_model("crew:claude", "crew:codex"))
        self.assertIsNone(_same_model("crew:codex", "main"))
        self.assertEqual(_same_model("crew:codex/GPT-A/low", "crew:codex/gpt-a/high"), "codex/GPT-A")
        self.assertEqual(_same_model("crew:codex/default/low", "crew:codex"), "codex/default")
        self.assertIn("may be the same", _same_model("crew:codex/gpt-a", "crew:codex/default"))
        self.assertIn("both run Claude", _same_model("crew:agy/claude-sonnet-5-5-low", "crew:claude/opus"))
        self.assertIn("both run Claude", _same_model("crew:cursor/claude-opus-5-thinking-high", "crew:claude"))
        self.assertIsNone(_same_model("crew:agy/gemini-4-neon-low", "crew:claude"))
        self.assertIn("Cursor can use", _same_model("crew:cursor", "crew:codex"))
        self.assertIsNone(_same_model("crew:cursor/gpt-5.2", "crew:codex/gpt-6.2-sol"))
        self.assertIn("both run Claude", _same_model("crew:claude/opus", "crew:claude/sonnet"))
        self.assertIn("may be the same", _same_model("crew:cursor/auto", "crew:cursor/gpt-5.2"))
        self.runs.ensure()
        path = self.runs.plan_path("P2")
        path.parent.mkdir(parents=True, exist_ok=True)
        plan = (PLAN7.replace("| crew:codex | - | Implement", "| crew:codex/gpt-a | - | Implement")
                .replace("| crew:codex | S1 | Review S1", "| crew:codex/gpt-b/low | S1 | Review S1")
                .replace("| main | S1 | Review S1", "| main-subagent | S1 | Review S1"))
        path.write_text(plan, encoding="utf-8")
        text = " | ".join(self.warnings())
        self.assertNotIn("step R1:", text)
        self.assertNotIn("step R2:", text)
        path.write_text(plan.replace("| crew:codex/gpt-a | - | Implement", "| main | - | Implement"), encoding="utf-8")
        self.assertIn("step R2: the reviewer (main-subagent) runs on the same model as the author of S1",
                      " | ".join(self.warnings()))

    def test_worker_header_allows_web_reading_but_not_sending_project_data(self):
        from crew.runner import HEADER
        self.assertNotIn("send data over the network", HEADER)
        self.assertIn("You may use web search and page reading tools", HEADER)
        self.assertIn("Never send project content", HEADER)

    def test_agy_write_contract_check_has_no_stale_unavailable_warning(self):
        p = self.contract(tid="T-agy-w", agent="agy", mode="write", wp='["a.txt"]')
        code, out, _ = crew("contract", "check", str(p), "--json")
        self.assertEqual(code, 0)
        self.assertNotIn("agy write mode is unavailable", " ".join(json.loads(out)["warnings"]))

    def test_review_block_parse(self):
        from crew import classify
        txt = "## REVIEW\nVERDICT: PARTIAL\nCRITERIA:\n- a: met (x)\n- b: unmet\n- c: unverified\nFINDINGS: none\n\n## RESULT\nSTATUS: success\n"
        self.assertEqual(classify.parse_review_block(txt), {"verdict": "PARTIAL", "criteria": 3})
        self.assertEqual(classify.parse_review_block("no block"), {"verdict": None, "criteria": 0})

    def test_review_kind_gets_the_reviewer_prompt_and_verdict(self):
        os.environ["FAKE_SCENARIO"] = "review_pass"
        p = self.contract(tid="T-r1")
        p.write_text(p.read_text(encoding="utf-8").replace("ephemeral: false", "ephemeral: false\ntask_kind: review"), encoding="utf-8")
        code, out, _ = crew("run", "--contract", str(p))
        self.assertEqual(code, 0, out)
        s = self.runs.load_state("T-r1")
        self.assertEqual((s["review_verdict"], s["review_criteria"]), ("PASS", 2))
        self.assertIn("review verdict: PASS (2 criteria lines)", out)
        self.assertIn("You are a REVIEWER", (self.runs.dir / "T-r1.prompt.md").read_text(encoding="utf-8"))

    def test_review_without_block_is_missing(self):
        os.environ["FAKE_SCENARIO"] = "done"
        p = self.contract(tid="T-r2")
        p.write_text(p.read_text(encoding="utf-8").replace("ephemeral: false", "ephemeral: false\ntask_kind: review"), encoding="utf-8")
        code, out, _ = crew("run", "--contract", str(p))
        self.assertIn("review verdict: MISSING", out)
