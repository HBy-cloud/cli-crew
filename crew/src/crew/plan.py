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
COLUMNS_REVIEW = COLUMNS + ("reviews",)
EXECUTOR_HELP = "main | main-subagent | crew:<agent>[/<model or default>[/<effort>]]"
EXECUTOR_RE = re.compile(r"^(main|main-subagent|crew:(" + "|".join(AGENTS) + r")(/[^/\s|]+){0,2})$")

TEMPLATE = """---
plan_id: "{plan_id}"
status: active
---
## Goal
TODO: the outcome the user asked for and how main will verify it. This cplan covers the WHOLE task, including steps main does itself.

## Steps
| step | status | executor | depends_on | task | verify | reviews |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | todo | main | - | TODO | TODO | - |
| R1 | todo | crew:codex/default/low | S1 | Independent review of S1 against its verify criteria | PASS verdict with evidence per criterion | S1 |

<!-- status: todo | doing | blocked | done | dropped
     executor: {executors}
     depends_on: step ids separated by commas, or -
     reviews: ids of the steps this step reviews (a reviewer step), or -. Every deliverable needs a reviewer step
              (executor: a different model than the author: another CLI, or the same CLI with another explicit model), or a Revisions row that says why the review is skipped. -->
<!--
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
    reviews: list[str] = field(default_factory=list)


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


def executor_model(executor: str) -> str | None:
    """The model named by a crew executor; None when it is omitted or 'default' (the CLI default model)."""
    if not executor.startswith("crew:"):
        return None
    parts = executor[5:].split("/")
    model = parts[1].strip() if len(parts) > 1 else ""
    return None if model.lower() in ("", "default") else model


def _same_model(reviewer: str, author: str) -> str | None:
    """Reviewer independence is judged by model (2026-11-21 user decision). Returns 'agent/model' when the reviewer
    may run on the same model as the author: same CLI and the same model, or a model that cannot be told apart
    (one side uses the CLI default). Copilot and Cursor can select models also available via other CLIs, and
    Claude models count as one model on every CLI (claude, agy, cursor, copilot; 2026-11-26 user decision)."""
    ra, ta = executor_agent(reviewer), executor_agent(author)
    if not ra or not ta:
        return None
    rm, tm = executor_model(reviewer), executor_model(author)
    rm, tm = (None if m and m.lower() == "auto" else m for m in (rm, tm))  # Auto picks a model like the default

    def claude_side(agent: str, model: str | None) -> bool:
        return agent == "claude" or bool(model and model.lower().startswith("claude"))
    if claude_side(ra, rm) and claude_side(ta, tm):
        return f"{ra}/{rm or 'default'} and {ta}/{tm or 'default'} both run Claude models"
    if ra != ta:
        multi = [a for a in (ra, ta) if a in ("copilot", "cursor")]
        if not multi:
            return None
        if not rm or not tm:
            return f"{multi[0].capitalize()} can use the same model as another CLI; verify the actual model"
        return rm if rm.lower() == tm.lower() else None
    if rm and tm and rm.lower() != tm.lower():
        return None
    if rm and tm:
        return f"{ra}/{rm}"
    if rm or tm:
        return f"{ra}/{rm or tm} vs {ra}/default; the default model may be the same"
    return f"{ra}/default"


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
    head = tuple(c.lower() for c in _cells(rows[0])) if rows else ()
    with_reviews = head == COLUMNS_REVIEW
    if rows and head not in (COLUMNS, COLUMNS_REVIEW):
        errors.append(f"steps table must start with the header: | {' | '.join(COLUMNS)} | (optional last column: reviews)")
    for n, line in enumerate(rows):
        cells = _cells(line)
        if n == 0 and tuple(c.lower() for c in cells) in (COLUMNS, COLUMNS_REVIEW):
            continue
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue
        want = len(COLUMNS_REVIEW) if with_reviews else len(COLUMNS)
        if len(cells) != want:
            first = redact(cells[0]) if cells else ""
            errors.append(f"steps row must have {want} cells (row starting '{first}')")
            continue
        sid, status, executor, deps, task, verify = cells[:6]
        rev = cells[6] if with_reviews else "-"
        dep_list = [] if deps in ("", "-") else [d.strip() for d in deps.split(",") if d.strip()]
        rev_list = [] if rev in ("", "-") else [d.strip() for d in rev.split(",") if d.strip()]
        steps.append(Step(sid, status.lower(), executor, dep_list, task, verify, rev_list))
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


NOT_OK_SKIP = re.compile(r"(?i)small|simple|trivial|easy|작|단순|간단|사소")
OK_SKIP = re.compile(r"(?i)cost|token|budget|credit|unavailable|no other|no_other|opt|비용|토큰|예산|불가|없음|거절|옵트")


def _review_warnings(plan: Plan) -> list[str]:
    """Reviewer rules (D-28): every deliverable has an independent reviewer step, or a Revisions row says why not."""
    out: list[str] = []
    by_id = {s.step: s for s in plan.steps}
    reviewed: set[str] = set()
    for r in plan.steps:
        for target in r.reviews:
            t = by_id.get(target)
            if t is None or target == r.step:
                out.append(f"step {r.step}: reviews unknown or own step '{target}'")
                continue
            reviewed.add(target)
            if r.executor == "main":
                out.append(f"step {r.step}: the reviewer is main itself, so the review of {target} is not independent")
            elif r.executor == "main-subagent" and t.executor in ("main", "main-subagent"):
                out.append(f"step {r.step}: the reviewer (main-subagent) runs on the same model as the author of {target}")
            same = _same_model(r.executor, t.executor)
            if same:
                out.append(f"step {r.step}: the reviewer uses the same model ({same}) as the author of {target}")
    dependents = {d for s in plan.steps for d in s.depends_on}
    rev_text = plan.sections.get("Revisions", "")
    for s in plan.steps:
        if s.reviews or s.status == "dropped":
            continue
        deliverable = s.step not in dependents or (executor_agent(s.executor) is not None)
        if deliverable and s.step not in reviewed:
            lines = [ln for ln in rev_text.splitlines() if s.step in ln and re.search(r"(?i)review|리뷰|검증", ln)]
            if not lines:
                out.append(f"step {s.step} has no reviewer step (add one with the reviews column, or record in Revisions why the review is skipped)")
            elif all(NOT_OK_SKIP.search(ln) and not OK_SKIP.search(ln) for ln in lines):
                out.append(f"step {s.step}: 'small' or 'simple' is not an accepted reason to skip the review; accepted: cost or token budget, no other model or CLI available, the user opted out")
    return out


PARALLEL_NOTE = re.compile(r"(?i)parallel|병렬")


def _parallel_warnings(plan: Plan) -> list[str]:
    """Parallel-first rule (2026-11-26 user decision): main's own tokens are a cost, so two or more main steps that do
    not depend on each other should be delegated in parallel, or a Revisions row must say why main does them."""
    by_id = {s.step: s for s in plan.steps}

    def ancestors(step_id: str, seen: set[str] | None = None) -> set[str]:
        seen = set() if seen is None else seen
        for d in by_id[step_id].depends_on if step_id in by_id else []:
            if d not in seen:
                seen.add(d)
                ancestors(d, seen)
        return seen
    # Only open steps: finished work cannot be re-planned.
    mains = [s.step for s in plan.steps if s.executor == "main" and not s.reviews and s.status in ("todo", "doing", "blocked")]
    up = {m: ancestors(m) for m in mains}
    free = sorted({a for a in mains for b in mains if a != b and a not in up[b] and b not in up[a]})
    if not free:
        return []
    lines = [ln for ln in plan.sections.get("Revisions", "").splitlines() if PARALLEL_NOTE.search(ln)]
    if not lines:
        return [f"steps {', '.join(free)} run on main but do not depend on each other; delegate independent reads in "
                "parallel (crew read workers or main-subagent, up to max_concurrent), or record in Revisions why main "
                "does them (for example edits, shell or tests, or work that is tightly coupled)"]
    if all(NOT_OK_SKIP.search(ln) and not re.search(r"(?i)edit|write|shell|test|build|coupled|수정|쓰기|셸|테스트|빌드|얽", ln)
           for ln in lines):
        return [f"steps {', '.join(free)}: 'small' or 'simple' is not an accepted reason to keep independent work on "
                "main; name the coupling, edits, or shell/test work instead"]
    return []


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

    warnings.extend(_review_warnings(plan))
    warnings.extend(_parallel_warnings(plan))
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
            "model": r.get("model_actual"),
        })
    return out


def _observed_models(runs: list[dict[str, Any]]) -> set[str] | None:
    """Recorded models of runs that were not rejected; None when one of them has no recorded model."""
    models: set[str] = set()
    for r in runs:
        if r.get("verdict") == "REJECTED":
            continue
        names = {m.strip().lower() for m in str(r.get("model") or "").split(",") if m.strip()}
        if not names:
            return None
        models |= names
    return models


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
        elif s.status == "todo" and runs:
            notes.append(f"{s.step}: has linked runs but is still marked todo; set it to doing")
        unjudged = [r["task_id"] for r in runs if not r.get("verdict")]
        if s.status == "done" and unjudged:
            notes.append(f"{s.step}: marked done but runs are not judged yet: {', '.join(unjudged)}")
    for s in plan.steps:  # reviewer independence from the models the runs actually used
        for target in s.reviews:
            rev, auth = links.get(s.step, []), links.get(target, [])
            if not rev or not auth:
                continue
            rm, am = _observed_models(rev), _observed_models(auth)
            if rm is None or am is None:
                notes.append(f"{s.step}: the actual model of a run on {s.step} or {target} is not recorded; "
                             "check that the review ran on a different model")
            elif rm & am or (any("claude" in m for m in rm) and any("claude" in m for m in am)):
                same = ", ".join(sorted(rm & am)) or "Claude models"
                notes.append(f"{s.step}: the review ran on the same model as {target} ({same}); it is not independent")
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
