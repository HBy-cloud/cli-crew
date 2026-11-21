"""Main-agent plans: goal, steps, executors, and links to crew runs (SPEC 4.13)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew.common import AGENTS, TASK_ID_RE
from crew.contract import SECRET_RES, ContractError, parse_frontmatter, parse_sections

STEP_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"
PLAN_STATUSES = ("active", "done", "abandoned")
STEP_STATUSES = ("todo", "doing", "blocked", "done", "dropped")
COLUMNS = ("step", "status", "executor", "depends_on", "task", "verify")
EXECUTOR_HELP = "main | main-subagent | crew:<agent>[/<model or default>[/<effort>]]"
EXECUTOR_RE = re.compile(r"^(main|main-subagent|crew:(" + "|".join(AGENTS) + r")(/[^/\s|]+){0,2})$")

TEMPLATE = """---
plan_id: "{plan_id}"
status: active
---
## Goal
TODO: the outcome the user asked for and how main will verify it. This cplan covers the WHOLE task, including steps main does itself.

## Steps
| step | status | executor | depends_on | task | verify |
| --- | --- | --- | --- | --- | --- |
| S1 | todo | main | - | TODO | TODO |

<!-- status: todo | doing | blocked | done | dropped
     executor: {executors}
     depends_on: step ids separated by commas, or -
     Do not use '|' inside a cell. -->

## Revisions
| date | change | reason or evidence |
| --- | --- | --- |

## Approvals
- none

## Notes
"""


@dataclass
class Step:
    step: str
    status: str
    executor: str
    depends_on: list[str]
    task: str
    verify: str


@dataclass
class Plan:
    path: Path
    fields: dict[str, Any]
    sections: dict[str, str]
    steps: list[Step] = field(default_factory=list)
    table_errors: list[str] = field(default_factory=list)

    @property
    def plan_id(self) -> Any:
        return self.fields.get("plan_id")

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.step == step_id), None)


def redact(text: str) -> str:
    """Hide secret-looking strings (the contract secret patterns) before printing plan text."""
    for rx in SECRET_RES:
        text = rx.sub("[REDACTED]", text)
    return text


def render_template(plan_id: str) -> str:
    return TEMPLATE.format(plan_id=plan_id, executors=EXECUTOR_HELP)


def executor_agent(executor: str) -> str | None:
    """The worker CLI named by a crew executor, or None for main / main-subagent."""
    if executor.startswith("crew:"):
        return executor[5:].split("/", 1)[0]
    return None


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _parse_steps(text: str) -> tuple[list[Step], list[str]]:
    steps: list[Step] = []
    errors: list[str] = []
    rows = [l for l in text.splitlines() if l.strip().startswith("|")]
    if rows and tuple(c.lower() for c in _cells(rows[0])) != COLUMNS:
        errors.append(f"steps table must start with the header: | {' | '.join(COLUMNS)} |")
    for n, line in enumerate(rows):
        cells = _cells(line)
        if n == 0 and tuple(c.lower() for c in cells) == COLUMNS:
            continue
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue
        if len(cells) != len(COLUMNS):
            first = redact(cells[0]) if cells else ""
            errors.append(f"steps row must have {len(COLUMNS)} cells (row starting '{first}')")
            continue
        sid, status, executor, deps, task, verify = cells
        dep_list = [] if deps in ("", "-") else [d.strip() for d in deps.split(",") if d.strip()]
        steps.append(Step(sid, status.lower(), executor, dep_list, task, verify))
    return steps, errors


def load(path: str | Path) -> Plan:
    p = Path(path)
    if not p.is_file():
        raise ContractError(f"plan not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ContractError(f"cannot read plan {p}: {e.__class__.__name__}")
    data, body = parse_frontmatter(text)
    sections = parse_sections(body)
    steps, table_errors = _parse_steps(sections.get("Steps", ""))
    return Plan(path=p, fields=data, sections=sections, steps=steps, table_errors=table_errors)


def _cycle(steps: list[Step]) -> list[str] | None:
    deps = {s.step: [d for d in s.depends_on if d != s.step] for s in steps}
    state: dict[str, int] = {}
    trail: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        trail.append(node)
        for nxt in deps.get(node, []):
            if nxt not in deps:
                continue
            if state.get(nxt) == 1:
                return trail[trail.index(nxt):] + [nxt]
            if state.get(nxt) is None:
                found = visit(nxt)
                if found:
                    return found
        trail.pop()
        state[node] = 2
        return None

    for s in steps:
        if state.get(s.step) is None:
            found = visit(s.step)
            if found:
                return found
    return None


def _handoff_missing(plan: Plan) -> bool:
    """True when the plan is stopped (abandoned, or has a blocked step) and no handoff file is newer than the plan file."""
    status = plan.fields.get("status")
    if status == "done" or not (status == "abandoned" or any(s.status == "blocked" for s in plan.steps)):
        return False
    try:
        root = plan.path.parents[2]
        plan_time = plan.path.stat().st_mtime
        for name in ("handoffs", "handoff"):
            d = root / name
            if d.is_dir() and any(f.stat().st_mtime >= plan_time for f in d.rglob("*.md")):
                return False
    except (IndexError, OSError):
        return False
    return True


def validate(plan: Plan) -> tuple[list[str], list[str]]:
    errors: list[str] = list(plan.table_errors)
    warnings: list[str] = []
    known = ("plan_id", "status")
    for k in plan.fields:
        if k not in known:
            errors.append(f"unknown field: {k}")
    pid = plan.plan_id
    if not isinstance(pid, str) or not re.fullmatch(TASK_ID_RE, pid):
        errors.append(f"plan_id missing or invalid (must match {TASK_ID_RE})")
    elif pid != plan.path.stem:
        errors.append(f"plan_id '{pid}' does not match the file name '{plan.path.stem}'")
    if plan.fields.get("status") not in PLAN_STATUSES:
        errors.append(f"status must be one of {', '.join(PLAN_STATUSES)}")
    goal = plan.sections.get("Goal", "")
    if not goal or goal.startswith("TODO"):
        errors.append("section '## Goal' is missing or still a TODO placeholder")
    if "Steps" not in plan.sections:
        errors.append("section '## Steps' is missing")
    elif not plan.steps and not plan.table_errors:
        errors.append("steps table has no rows")

    ids = [s.step for s in plan.steps]
    for s in plan.steps:
        where = f"step {s.step}"
        if not re.fullmatch(STEP_ID_RE, s.step):
            errors.append(f"invalid step id '{s.step}' (must match {STEP_ID_RE})")
        if ids.count(s.step) > 1:
            errors.append(f"duplicate step id: {s.step}")
        if s.status not in STEP_STATUSES:
            errors.append(f"{where}: status must be one of {', '.join(STEP_STATUSES)}")
        if not EXECUTOR_RE.fullmatch(s.executor):
            errors.append(f"{where}: executor must be {EXECUTOR_HELP}")
        for d in s.depends_on:
            if d == s.step:
                errors.append(f"{where}: depends on itself")
            elif d not in ids:
                errors.append(f"{where}: depends on unknown step '{d}'")
        for name, value in (("task", s.task), ("verify", s.verify)):
            if not value or value.upper().startswith("TODO"):
                errors.append(f"{where}: {name} is empty or still TODO")
    errors = list(dict.fromkeys(errors))
    cyc = _cycle(plan.steps)
    if cyc:
        errors.append("dependency cycle: " + " -> ".join(cyc))
    if plan.fields.get("status") == "done":
        open_steps = [s.step for s in plan.steps if s.status in ("todo", "doing", "blocked")]
        if open_steps:
            warnings.append(f"plan is done but steps are still open: {', '.join(open_steps)}")

    if _handoff_missing(plan):
        warnings.append("NOTE: the plan is stopped (abandoned or a step is blocked) but no handoff is newer than it; run `crew handoff` and fill it in")
    rev_rows = [ln for ln in plan.sections.get("Revisions", "").splitlines() if ln.strip().startswith("|")]
    if any(s.status == "dropped" for s in plan.steps) and len(rev_rows) <= 2:
        warnings.append("a step is dropped but the Revisions table has no entry explaining why")
    raw = plan.path.read_text(encoding="utf-8")
    for rx in SECRET_RES:
        if rx.search(raw):
            errors.append(f"plan contains a secret-looking string (pattern {rx.pattern})")
    return errors, warnings


def ready_steps(plan: Plan) -> list[str]:
    """Todo steps whose dependencies are all done or dropped."""
    closed = {s.step for s in plan.steps if s.status in ("done", "dropped")}
    return [s.step for s in plan.steps if s.status == "todo" and all(d in closed for d in s.depends_on)]


def linked_runs(plan_id: str, records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """step id -> run summaries for state files and archived history that reference this plan."""
    out: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for r in records:
        tid = r.get("task_id")
        if r.get("plan") != plan_id or not tid or tid in seen:
            continue
        seen.add(tid)
        out.setdefault(str(r.get("plan_step")), []).append({
            "task_id": tid, "agent": r.get("agent"), "status": r.get("status"), "verdict": r.get("verdict"),
        })
    return out


def step_notes(plan: Plan, links: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Mechanical consistency notes between step status and linked runs. Main decides what to do."""
    notes = []
    for s in plan.steps:
        runs = links.get(s.step, [])
        agent = executor_agent(s.executor)
        if runs and agent is None:
            notes.append(f"{s.step}: executor is {s.executor} but crew runs are linked")
        other = [r["task_id"] for r in runs if agent is not None and r.get("agent") != agent]
        if other:
            notes.append(f"{s.step}: executor names {agent} but linked runs used another agent: {', '.join(other)}")
        if s.status == "done" and agent is not None:
            if not any(r.get("verdict") == "ACCEPTED" for r in runs):
                notes.append(f"{s.step}: marked done without an accepted crew run")
        if s.status in ("todo", "dropped") and any(r.get("verdict") == "ACCEPTED" for r in runs):
            notes.append(f"{s.step}: has an accepted run but is marked {s.status}")
    unknown = sorted(k for k in links if plan.step(k) is None)
    for k in unknown:
        notes.append(f"runs reference unknown step '{k}': {', '.join(r['task_id'] for r in links[k])}")
    return notes


def summary_line(plan: Plan, links: dict[str, list[dict[str, Any]]]) -> str:
    parts = []
    for s in plan.steps:
        runs = links.get(s.step, [])
        tail = ""
        if runs:
            tail = " [" + ", ".join(f"{r['task_id']} {r['status']}/{r['verdict'] or 'unjudged'}" for r in runs) + "]"
        parts.append(f"{s.step} {s.status} {s.executor}{tail}")
    return f"{plan.plan_id} ({plan.fields.get('status')}): " + "; ".join(parts)
