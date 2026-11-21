"""Task contract: template, frontmatter parsing, merge with CLI options, validation (SPEC 5)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew.common import (
    AGENTS, EFFORTS, HIGH_EFFORTS, TASK_ID_RE, CrewError, home_dir, is_drive_root, is_within,
    real, resolve_nearest,
)
from crew.state import Runs, retry_of_problem

TASK_KINDS = ("investigate", "implement", "review", "document", "other")

FIELDS: dict[str, Any] = {
    "task_kind": None,
    "task_id": None,
    "agent": None,
    "mode": None,
    "write_paths": [],
    "add_dirs": [],
    "allow_subagents": False,
    "effort": None,
    "model": None,
    "timeout_min": None,
    "max_retries": 1,
    "retry_of": None,
    "ephemeral": False,
    "worker_handoff": False,
    "plan": None,
    "plan_step": None,
}

REQUIRED_SECTIONS = ("Objective", "Done when")

SECRET_RES = [re.compile(p) for p in (
    r"sk-[A-Za-z0-9_-]{20,}",
    r"ghp_[A-Za-z0-9]{30,}",
    r"github_pat_",
    r"AIza[0-9A-Za-z_-]{30,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[baprs]-",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)]

TEMPLATE = """---
task_id: "{task_id}"
agent: {agent}
mode: {mode}
task_kind: {task_kind}
write_paths: {write_paths}
add_dirs: []
allow_subagents: false
effort: low
model: null
timeout_min: 20
max_retries: 1
retry_of: null
ephemeral: false
worker_handoff: false
plan: {plan}
plan_step: {plan_step}
---
## Objective
TODO: one or two sentences describing the goal.

## Context
TODO: relevant file paths, decisions and constraints (paths, not file contents).

## Done when
TODO: conditions that mean the task is complete.

## Notes
"""


class ContractError(Exception):
    pass


@dataclass
class Contract:
    path: Path
    fields: dict[str, Any]
    body: str
    sections: dict[str, str]
    unknown: list[str] = field(default_factory=list)


# ------------------------------------------------------------ parsing

def _strip_comment(value: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or value[i - 1].isspace()):
            break
        out.append(ch)
    return "".join(out).strip()


def _scalar(v: str) -> Any:
    v = v.strip()
    if v == "" or v in ("null", "~", "None"):
        return None
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _inline_list(v: str) -> list[Any]:
    inner = v.strip()[1:-1].strip()
    if not inner:
        return []
    return [_scalar(x) for x in inner.split(",")]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ContractError("contract must start with '---' frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ContractError("frontmatter is not closed with '---'")
    data: dict[str, Any] = {}
    seen: set[str] = set()
    key: str | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        if stripped.startswith("- ") and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_scalar(_strip_comment(stripped[2:])))
            continue
        if ":" not in raw:
            raise ContractError(f"cannot parse frontmatter line: {raw!r}")
        k, v = raw.split(":", 1)
        key = k.strip()
        if key in seen:
            raise ContractError(f"duplicate frontmatter key: {key}")
        seen.add(key)
        v = _strip_comment(v)
        if v.startswith("[") and v.endswith("]"):
            data[key] = _inline_list(v)
        elif v == "":
            data[key] = None
        else:
            data[key] = _scalar(v)
    body = "\n".join(lines[end + 1:])
    return data, body


def parse_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                sections[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf).strip()
    return sections


def load(path: str | os.PathLike) -> Contract:
    p = Path(path)
    if not p.exists():
        raise ContractError(f"contract not found: {p}")
    text = p.read_text(encoding="utf-8")
    data, body = parse_frontmatter(text)
    unknown = [k for k in data if k not in FIELDS]
    fields = dict(FIELDS)
    for k, v in data.items():
        if k in FIELDS:
            fields[k] = v
    for k in ("write_paths", "add_dirs"):
        if fields[k] is None:
            fields[k] = []
    return Contract(path=p, fields=fields, body=body, sections=parse_sections(body), unknown=unknown)


def render_template(task_id: str, agent: str | None, mode: str, write_paths: list[str], task_kind: str | None = None,
                    plan: str | None = None, plan_step: str | None = None) -> str:
    wp = "[" + ", ".join(write_paths) + "]" if write_paths else "[]"
    def q(v):
        return f'"{v}"' if v else "null"
    return TEMPLATE.format(task_id=task_id, agent=agent or "null", mode=mode, write_paths=wp,
                           task_kind=task_kind or "null", plan=q(plan), plan_step=q(plan_step))


# ------------------------------------------------------------ merge + validate

def merge(c: Contract, cfg: dict[str, Any], *, agent=None, effort=None, model=None, timeout=None) -> dict[str, Any]:
    s = dict(c.fields)
    if agent:
        s["agent"] = agent
    if effort:
        s["effort"] = effort
    if model:
        s["model"] = model
    if timeout:
        s["timeout_min"] = timeout
    if s.get("effort") is None:
        s["effort"] = cfg["default_effort"]
    if s.get("timeout_min") is None:
        s["timeout_min"] = cfg["default_timeout_min"]
    return s


@dataclass
class CheckResult:
    errors: list[str]
    warnings: list[str]
    settings: dict[str, Any]
    write_paths_abs: list[Path]
    add_dirs_abs: list[Path]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(c: Contract, s: dict[str, Any], root: Path, runs: Runs, cfg: dict[str, Any]) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []
    wp_abs: list[Path] = []
    ad_abs: list[Path] = []

    for k in c.unknown:
        errors.append(f"unknown field: {k}")

    tid = s.get("task_id")
    if not isinstance(tid, str) or not re.fullmatch(TASK_ID_RE, tid):
        errors.append(f"task_id missing or invalid (must match {TASK_ID_RE})")

    agent = s.get("agent")
    if agent is not None and agent not in AGENTS:
        errors.append(f"agent must be one of {AGENTS}")

    mode = s.get("mode")
    if s.get("task_kind") is not None and s["task_kind"] not in TASK_KINDS:
        errors.append(f"task_kind must be one of {TASK_KINDS} or null")
    if mode not in ("read", "write"):
        errors.append("mode must be 'read' or 'write'")

    if not isinstance(s.get("write_paths"), list):
        errors.append("write_paths must be a list")
    if not isinstance(s.get("add_dirs"), list):
        errors.append("add_dirs must be a list")
    for k in ("allow_subagents", "ephemeral", "worker_handoff"):
        if not isinstance(s.get(k), bool):
            errors.append(f"{k} must be true or false")
    t = s.get("timeout_min")
    if not isinstance(t, int) or not 1 <= t <= 120:
        errors.append("timeout_min must be an integer 1..120")
    if s.get("max_retries") not in (0, 1):
        errors.append("max_retries must be 0 or 1")

    # effort
    effort = s.get("effort")
    if agent in EFFORTS:
        if effort not in EFFORTS[agent]:
            errors.append(f"effort '{effort}' not allowed for {agent} (allowed: {', '.join(EFFORTS[agent])})")
    else:
        allowed = sorted({e for v in EFFORTS.values() for e in v})
        if effort not in allowed:
            errors.append(f"effort '{effort}' not allowed (any of {', '.join(allowed)})")
        if agent is None:
            warnings.append("agent not set; pass --agent to crew run")
    if effort in HIGH_EFFORTS:
        warnings.append(f"effort '{effort}' requires --approve-cost at run time")
    model = s.get("model")
    if model and model in (cfg.get("approval_models") or []):
        warnings.append(f"model '{model}' is in approval_models; requires --approve-cost")

    # write_paths
    if mode == "write" and not s.get("write_paths"):
        errors.append("mode=write requires non-empty write_paths")
    if mode == "read" and s.get("write_paths"):
        errors.append("write_paths must be empty in mode=read")
    for wp in s.get("write_paths") or []:
        if not isinstance(wp, str) or not wp.strip():
            errors.append(f"invalid write_path: {wp!r}")
            continue
        if os.path.isabs(wp) or re.match(r"^[A-Za-z]:", wp):
            errors.append(f"write_path must be relative to the project root: {wp}")
            continue
        target = resolve_nearest(root / wp)
        if not is_within(target, root):
            errors.append(f"write_path escapes the project root: {wp}")
            continue
        if is_within(target, root / ".git") or is_within(target, root / ".agent-runs"):
            errors.append(f"write_path inside .git or .agent-runs: {wp}")
            continue
        wp_abs.append(target)

    # add_dirs
    if s.get("add_dirs") and mode == "write":
        errors.append("add_dirs is not allowed with mode=write (it would grant write access for codex/claude); split into a read task")
    for ad in s.get("add_dirs") or []:
        if not isinstance(ad, str) or not os.path.isabs(ad):
            errors.append(f"add_dir must be an absolute path: {ad!r}")
            continue
        p = real(ad)
        if not p.is_dir():
            errors.append(f"add_dir does not exist or is not a directory: {ad}")
            continue
        if is_drive_root(p) or p == home_dir():
            errors.append(f"add_dir must not be a drive root or the home directory: {ad}")
            continue
        ad_abs.append(p)

    if s.get("allow_subagents") and mode == "write":
        errors.append("allow_subagents=true is only allowed with mode=read")
    if s.get("worker_handoff") is True:
        if mode != "read":
            errors.append("worker_handoff=true is only for mode=read (a write task may list its handoff file in write_paths)")
        if s.get("add_dirs"):
            errors.append("worker_handoff=true cannot be combined with add_dirs (the worker runs with write access)")
    if s.get("ephemeral"):
        if agent is None:
            errors.append("ephemeral=true requires agent=codex")
        elif agent != "codex":
            errors.append("ephemeral is codex-only")
        if not s.get("allow_subagents"):
            errors.append("ephemeral=true with allow_subagents=false is not allowed (subagent ban could not be verified)")

    # plan link (SPEC 4.13)
    plan_id, plan_step = s.get("plan"), s.get("plan_step")
    if (plan_id is None) != (plan_step is None):
        errors.append("plan and plan_step must be set together")
    elif plan_id is not None:
        from crew import plan as pl
        if not isinstance(plan_id, str) or not re.fullmatch(TASK_ID_RE, plan_id):
            errors.append(f"plan must be a plan id matching {TASK_ID_RE}")
        else:
            try:
                p = pl.load(runs.plan_path(plan_id))
            except ContractError as e:
                errors.append(f"plan '{plan_id}': {e}")
            except CrewError as e:
                errors.append(f"plan '{plan_id}': {e.message}")
            else:
                if pl.validate(p)[0]:
                    errors.append(f"plan '{plan_id}' is invalid; run: crew plan check {plan_id}")
                step = p.step(str(plan_step))
                if step is None:
                    errors.append(f"plan '{plan_id}' has no step '{plan_step}'")
                else:
                    want = pl.executor_agent(step.executor)
                    if want is None:
                        warnings.append(f"plan step {plan_step} executor is {step.executor}; "
                                        "update the plan if you delegate it")
                    elif agent and want != agent:
                        warnings.append(f"plan step {plan_step} names {want} but the contract uses {agent}; "
                                        "update one of them")
                if p.fields.get("status") != "active":
                    warnings.append(f"plan '{plan_id}' status is {p.fields.get('status')}")

    # body sections
    for name in REQUIRED_SECTIONS:
        text = c.sections.get(name, "")
        if not text or text.startswith("TODO"):
            errors.append(f"section '## {name}' is missing or still a TODO placeholder")

    # secrets
    raw = c.path.read_text(encoding="utf-8")
    for rx in SECRET_RES:
        if rx.search(raw):
            errors.append(f"contract contains a secret-looking string (pattern {rx.pattern})")

    # retry_of
    if isinstance(tid, str):
        prob = retry_of_problem(runs.registry(), tid, s.get("retry_of"))
        if prob:
            errors.append(prob)

    return CheckResult(errors, warnings, s, wp_abs, ad_abs)
