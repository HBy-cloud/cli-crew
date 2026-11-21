"""crew command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from crew import __version__, adapters, citations, classify, contract as ct, plan as pl, routing, runner, snapshot, state as st
from crew.common import (
    AGENTS, EXIT_DUP, EXIT_GUARD, EXIT_OK, EXIT_USAGE, KST, TASK_ID_RE, CrewError, atomic_write_text,
    home_dir, is_drive_root, load_config, now, now_iso, parse_iso, read_json, read_text, real, rel_to,
    same_path,
)
from crew.safety import INSTRUCTION_FILES, instruction_dirs, scan_instructions
from crew.subagents import path_size, verify_codex_rollout_for_delete

API_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def emit(data: Any, as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))


def _root_and_runs() -> tuple[Path, st.Runs]:
    from crew.common import find_project_root
    root = find_project_root()
    return root, st.Runs(root)


# ------------------------------------------------------------ detect

def handoff_dir(root: Path) -> Path | None:
    for name in ("handoffs", "handoff"):
        if (root / name).is_dir():
            return root / name
    return None


def latest_handoff(root: Path) -> Path | None:
    d = handoff_dir(root)
    if not d:
        return None
    files = [p for p in d.rglob("*.md") if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def cmd_init(a) -> int:
    root, _ = _root_and_runs()
    if os.environ.get("AGENT_ROLE") == "WORKER":
        raise CrewError(EXIT_GUARD, "project initialization is a main-agent task")
    for name in INSTRUCTION_FILES:
        path = root / name
        if os.path.lexists(path):
            if not path.is_file():
                raise CrewError(EXIT_GUARD, f"cannot use existing instruction path: {path}")
            print(f"Existing instructions: {path}")
            return EXIT_OK

    names = ("README.md", "CONTRIBUTING.md", "docs/SPEC.md", "pyproject.toml", "package.json",
             "Cargo.toml", "go.mod", "pubspec.yaml", "Makefile")
    sources = [name for name in names if (root / name).is_file()]
    folders = [name for name in ("src", "lib", "tests", "docs") if (root / name).is_dir()]
    lines = ["# Project instructions", ""]
    if sources:
        lines += ["Project references: " + ", ".join(f"`{name}`" for name in sources) + ".", ""]
    if folders:
        lines += ["Project directories: " + ", ".join(f"`{name}/`" for name in folders) + ".", ""]
    lines += [
        "## Starting or resuming work", "",
        "Read the applicable instructions and the requirements for this task. If this is a Git project,",
        "check the working tree. Read the latest relevant handoff when available and compare its claims",
        "with the current files. Preserve existing edits and check ownership before editing work in progress.", "",
        "## Working with other agents", "",
        "The main agent owns the goal, selects bounded tasks for workers when useful, and verifies their",
        "results. Keep workers within the agreed scope and follow project restrictions on external sharing.", "",
        "## Verification and handoff", "",
        "Use documented build and test commands when available. Record what actually ran, the results,",
        "and any checks that remain unverified. Follow the existing handoff convention; preserve prior",
        "records and give the next session the changed paths, open issues, and next action.",
    ]
    path = root / "AGENTS.md"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as out:
            out.write("\n".join(lines) + "\n")
    except FileExistsError:
        if not path.is_file():
            raise CrewError(EXIT_GUARD, f"cannot use existing instruction path: {path}")
        print(f"Existing instructions: {path}")
        return EXIT_OK
    print(f"Created instructions: {path}")
    return EXIT_OK


def cmd_detect(a) -> int:
    root, runs = _root_and_runs()
    cfg = load_config()
    clis = {}
    for agent in AGENTS:
        ver = adapters.cli_version(agent)
        clis[agent] = {"available": ver is not None, "version": ver,
                       "verified_version": adapters.VERIFIED_VERSIONS.get(agent),
                       "version_warning": adapters.version_warning(agent, ver),
                       "default_model": runner.default_model(agent) if ver else None}
    git = snapshot.is_git_repo(root)
    dirty = None
    if git:
        r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "."], capture_output=True, check=False)
        dirty = len([l for l in r.stdout.decode("utf-8", "replace").splitlines() if l.strip()]) if r.returncode == 0 else None
    scan = scan_instructions(instruction_dirs(root, Path.cwd(), a.paths or []))
    files, hits, privacy = scan["files"], scan["restrictions"], scan["privacy"]
    hd = handoff_dir(root)
    lh = latest_handoff(root)
    lang = "ko" if lh and re.search(r"[가-힣]", read_text(lh)) else ("en" if lh else None)
    active = runs.list_active()
    keys = [k for k in API_KEY_VARS if os.environ.get(k)]
    data = {"root": str(root), "git": git, "uncommitted": dirty, "clis": clis,
            "instruction_files": files, "possible_restrictions": hits, "sensitive_data_hints": privacy,
            "handoff_dir": str(hd) if hd else None, "handoff_lang": lang,
            "active": active, "api_key_env_present": keys,
            "approval_models": cfg.get("approval_models")}
    lines = [f"root: {root} (git={git}, uncommitted={dirty})"]
    for k, v in clis.items():
        lines.append(f"cli {k}: {'OK ' + str(v['version']) if v['available'] else 'not available'}"
                     + (f", default model {v['default_model']}" if v.get("default_model") else ""))
    for k, v in clis.items():
        if v["version_warning"]:
            lines.append(f"VERSION WARNING: {v['version_warning']}")
    lines.append(f"instruction files ({len(files)}): " + (", ".join(files) if files else "none"))
    if hits:
        lines.append("POSSIBLE DELEGATION RESTRICTIONS (read and judge yourself):")
        lines += ["  " + h for h in hits[:10]]
        if len(hits) > 10:
            lines.append(f"  ... {len(hits) - 10} more (use --json)")
    else:
        lines.append("no restriction keywords found (not a guarantee; read the instruction files)")
    if privacy:
        lines.append("SENSITIVE DATA HINTS (delegation sends content to another provider; ask the user; crew run needs --approve-data-sharing):")
        lines += ["  " + h for h in privacy[:5]]
        if len(privacy) > 5:
            lines.append(f"  ... {len(privacy) - 5} more (use --json)")
    lines.append(f"handoff dir: {hd or 'none'} (lang={lang or 'unknown'})")
    if active:
        lines.append("active: " + ", ".join(f"{x.get('task_id')}({x.get('mode')})" for x in active))
    if keys:
        lines.append("WARNING: API key env vars present (" + ", ".join(keys) + "): workers may bill the API instead of a subscription")
    emit(data, a.json, lines)
    return EXIT_OK


# ------------------------------------------------------------ contract

def cmd_contract_new(a) -> int:
    root, runs = _root_and_runs()
    runs.ensure()
    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", a.task_id):
        raise CrewError(EXIT_USAGE, "invalid task_id")
    path = runs.contracts / f"{a.task_id}.md"
    if path.exists() or a.task_id in runs.registry():
        raise CrewError(EXIT_DUP, f"task_id '{a.task_id}' already used")
    if (a.plan is None) != (a.step is None):
        raise CrewError(EXIT_USAGE, "--plan and --step must be given together")
    atomic_write_text(path, ct.render_template(a.task_id, a.agent, a.mode, a.write or [], a.kind, a.plan, a.step))
    print(str(path))
    return EXIT_OK


# ------------------------------------------------------------ plan (SPEC 4.13)

def _plan_records(runs: st.Runs) -> list[dict[str, Any]]:
    """Current state files first (they win), then archived history samples."""
    return list(runs.iter_states()) + list(runs.iter_history())


def _load_plan(runs: st.Runs, plan_id: str) -> pl.Plan:
    if not re.fullmatch(TASK_ID_RE, plan_id):
        raise CrewError(EXIT_USAGE, "invalid plan id")
    try:
        return pl.load(runs.plan_path(plan_id))
    except ct.ContractError as e:
        raise CrewError(EXIT_USAGE, str(e))


def cmd_plan_new(a) -> int:
    _, runs = _root_and_runs()
    if not re.fullmatch(TASK_ID_RE, a.plan_id):
        raise CrewError(EXIT_USAGE, "invalid plan id")
    runs.ensure()
    path = runs.plan_path(a.plan_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise CrewError(EXIT_DUP, f"plan '{a.plan_id}' already exists: {path}")
    # Write a complete temp file, then hard-link it into place: atomic and never overwrites (SPEC 8).
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(pl.render_template(a.plan_id))
        os.link(tmp, path)
    except FileExistsError:
        raise CrewError(EXIT_DUP, f"plan '{a.plan_id}' already exists: {path}")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    print(str(path))
    return EXIT_OK


def _redacted(obj: Any) -> Any:
    """Plan text is user-written; hide secret-looking strings in every printed value."""
    if isinstance(obj, str):
        return pl.redact(obj)
    if isinstance(obj, list):
        return [_redacted(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redacted(v) for k, v in obj.items()}
    return obj


def cmd_plan_check(a) -> int:
    _, runs = _root_and_runs()
    p = _load_plan(runs, a.plan_id)
    errors, warnings = pl.validate(p)
    notes = pl.step_notes(p, pl.linked_runs(a.plan_id, _plan_records(runs))) if not errors else []
    lines = [f"plan: {p.path}"] + [f"ERROR: {e}" for e in errors] + [f"warning: {w}" for w in warnings + notes]
    lines.append("OK" if not errors else f"{len(errors)} error(s)")
    emit(_redacted({"ok": not errors, "errors": errors, "warnings": warnings + notes}), a.json, _redacted(lines))
    return EXIT_OK if not errors else EXIT_USAGE


def _plan_view(runs: st.Runs, p: pl.Plan) -> dict[str, Any]:
    links = pl.linked_runs(str(p.plan_id), _plan_records(runs))
    errors, warnings = pl.validate(p)
    return {
        "plan_id": p.plan_id, "status": p.fields.get("status"), "path": str(p.path),
        "steps": [{**vars(s), "runs": links.get(s.step, [])} for s in p.steps],
        "ready": pl.ready_steps(p), "errors": errors,
        "notes": warnings + (pl.step_notes(p, links) if not errors else []),
        "summary": pl.summary_line(p, links),
    }


def cmd_plan_show(a) -> int:
    _, runs = _root_and_runs()
    if not a.plan_id:
        rows, lines = [], []
        for path in runs.plan_files():
            try:
                p = pl.load(path)
            except ct.ContractError as e:
                rows.append({"plan_id": path.stem, "error": str(e)})
                lines.append(f"{path.stem} | unreadable: {e}")
                continue
            done = sum(s.status in ("done", "dropped") for s in p.steps)
            rows.append({"plan_id": path.stem, "status": p.fields.get("status"), "steps": len(p.steps), "closed": done})
            lines.append(f"{path.stem} | {p.fields.get('status')} | {done}/{len(p.steps)} steps closed")
        emit(rows, a.json, ["plan | status | progress"] + lines if lines else ["no plans"])
        return EXIT_OK
    view = _plan_view(runs, _load_plan(runs, a.plan_id))
    lines = [f"plan {view['plan_id']} ({view['status']}): {view['path']}"]
    for s in view["steps"]:
        deps = ",".join(s["depends_on"]) or "-"
        lines.append(f"  {s['step']} | {s['status']} | {s['executor']} | after {deps} | {s['task']}")
        for r in s["runs"]:
            lines.append(f"      run {r['task_id']}: {r['agent']} {r['status']} verdict={r['verdict']}")
    lines.append("ready: " + (", ".join(view["ready"]) or "none"))
    lines += [f"ERROR: {e}" for e in view["errors"]] + [f"note: {n}" for n in view["notes"]]
    emit(_redacted(view), a.json, _redacted(lines))
    return EXIT_OK


def _check(a, root: Path, runs: st.Runs) -> tuple[ct.Contract, ct.CheckResult]:
    cfg = load_config()
    try:
        c = ct.load(a.file)
    except ct.ContractError as e:
        raise CrewError(EXIT_USAGE, str(e))
    s = ct.merge(c, cfg, agent=a.agent, effort=a.effort, model=a.model, timeout=getattr(a, "timeout", None))
    return c, ct.validate(c, s, root, runs, cfg)


def cmd_contract_check(a) -> int:
    root, runs = _root_and_runs()
    c, r = _check(a, root, runs)
    tid = r.settings.get("task_id")
    if tid and tid in runs.registry():
        r.warnings.append(f"task_id '{tid}' is already used; crew run will refuse it")
    scan = scan_instructions(instruction_dirs(root, Path.cwd(), r.settings.get("write_paths") or []))
    hits = scan["restrictions"]
    if scan["privacy"]:
        r.warnings.append("sensitive-data hints in instructions; crew run needs --approve-data-sharing after asking the user")
    if r.settings.get("agent") == "agy" and r.settings.get("mode") == "write":
        r.warnings.append("agy write mode is unavailable because write boundaries cannot be enforced")
    lines = [f"contract: {c.path}"]
    lines += [f"ERROR: {e}" for e in r.errors]
    lines += [f"warning: {w}" for w in r.warnings]
    if hits:
        lines.append("instructions on target paths mentioning delegation limits:")
        lines += ["  " + h for h in hits[:10]]
    lines.append("OK" if r.ok else f"{len(r.errors)} error(s)")
    emit({"ok": r.ok, "errors": r.errors, "warnings": r.warnings, "settings": r.settings,
          "instruction_hits": hits}, a.json, lines)
    return EXIT_OK if r.ok else EXIT_USAGE


# ------------------------------------------------------------ run / cancel

def summarize(state: dict[str, Any], runs: Any = None) -> list[str]:
    lines = [f"task {state['task_id']}: {state.get('status')} (agent={state.get('agent')}, mode={state.get('mode')}, "
             f"attempts={len(state.get('attempts', []))}, {state.get('duration_s')}s)"]
    if state.get("failure_reason"):
        lines.append(f"reason: {state['failure_reason']}")
    warning = adapters.version_warning(str(state.get("agent")), state.get("cli_version"))
    if warning:
        lines.append(f"VERSION WARNING: {warning}")
    lines.append(f"worker_status: {state.get('worker_status')}")
    v = state.get("boundary_violations") or []
    lines.append(f"boundary violations: {len(v)}" + (": " + ", ".join(f"{x['kind']} {x['path']}" for x in v[:10]) if v else ""))
    if state.get("worker_handoff"):
        lines.append(f"worker handoff: {state.get('handoff_slot')} ({'written' if state.get('handoff_written') else 'NOT written'})")
    if state.get("boundary_check"):
        lines.append(f"boundary check: {state['boundary_check']}")
    lines.append(f"subagent used: {state.get('subagent_used')} (violation={state.get('subagent_violation')})")
    if state.get("model_actual"):
        lines.append(f"model: {state['model_actual']}")
    if state.get("cost_usd") is not None:
        lines.append(f"cost_usd: {state['cost_usd']}")
    last = (state.get("attempts") or [{}])[-1]
    if last.get("result"):
        if runs is None or runs.file(last["result"]).exists():
            lines.append(f"result: .agent-runs/{last['result']}  (crew result {state['task_id']})")
        else:
            lines.append("result: none (no worker response was saved)")
    if state.get("message"):
        lines.append(f"note: {state['message'].strip()}")
    return lines


def cmd_run(a) -> int:
    root, runs = _root_and_runs()
    cfg = load_config()
    c, r = _check(a, root, runs)
    if not r.ok:
        for e in r.errors:
            print(f"ERROR: {e}")
        return EXIT_USAGE
    state = runner.run(root, runs, r, c.body, cfg, approve_cost=a.approve_cost,
                       approve_data_sharing=a.approve_data_sharing, allow_unbounded_write=a.allow_unbounded_write)
    lines = summarize(state, runs)
    if a.with_result:
        result = _result_text(runs, state, False)
        if a.json:
            state = {**state, "result_text": result}
        else:
            lines.append(result)
    emit(state, a.json, lines)
    return runner.exit_code_for(state)


def cmd_cancel(a) -> int:
    root, runs = _root_and_runs()
    code, msg = runner.cancel(root, runs, a.task_id, a.yes, a.target)
    print(msg)
    return code


# ------------------------------------------------------------ status / check / result

def _display_status(s: dict[str, Any]) -> str:
    from crew import procutil
    status = s.get("status")
    if status == "RUNNING" and not procutil.same_process(s.get("crew_pid"), s.get("crew_create_time")):
        alive = procutil.same_process(s.get("worker_pid"), s.get("worker_create_time"))
        return "ORPHANED?" if alive else "STALE?"
    return status


def cmd_routing(a) -> int:
    _, runs = _root_and_runs()
    rows = routing.recent(runs, a.kind, a.mode)
    lines = [f"{a.kind}/{a.mode}: {len(rows)} recent comparable worker runs (up to 10)"]
    for row in rows:
        model = row.get("model_actual")
        if not model:
            requested = row.get("model_requested")
            model = f"{requested} (requested)" if requested else "default (unknown)"
        lines.append(f"{row['task_id']} {row['agent']}/{model}/{row['effort']} "
                     f"{row['status']} {row['verdict'] or 'unjudged'} "
                     f"verification_recorded={row['verification_recorded']} override={row['override']} "
                     f"attempts={row['attempts_count']} retry_of={row['retry_of'] or '-'} "
                     f"seconds={row['duration_s']} tokens={row['tokens']} usd={row['cost_usd']}")
    emit(rows, a.json, lines)
    return EXIT_OK


def cmd_status(a) -> int:
    _, runs = _root_and_runs()
    states = sorted(runs.iter_states(), key=lambda s: s.get("started") or "", reverse=True)
    if not a.all:
        states = [s for s in states if s.get("status") in st.ACTIVE_STATES or not s.get("verdict")][:20]
    rows = []
    for s in states:
        rows.append({"task_id": s["task_id"], "agent": s.get("agent"), "mode": s.get("mode"),
                     "status": _display_status(s), "started": s.get("started"),
                     "duration_s": s.get("duration_s"), "verdict": s.get("verdict")})
    lines = ["task_id | agent | mode | status | started | dur | verdict"]
    lines += [f"{r['task_id']} | {r['agent']} | {r['mode']} | {r['status']} | {r['started']} | {r['duration_s']} | {r['verdict']}" for r in rows]
    if not rows:
        lines = ["no tasks"]
    emit(rows, a.json, lines)
    return EXIT_OK


def cmd_compare(a) -> int:
    _, runs = _root_and_runs()
    rows = []
    archived = {r["task_id"]: r for r in runs.iter_history() if r.get("task_id") in a.task_ids}
    scopes = {"codex": "input+output", "claude": "input+output+cache creation+cache reads"}
    lines = []
    for tid in a.task_ids:
        if not re.fullmatch(TASK_ID_RE, tid):
            raise CrewError(EXIT_USAGE, f"invalid task id: {tid}")
        state = runs.load_state(tid)
        if state and state.get("status") in st.ACTIVE_STATES:
            raise CrewError(EXIT_GUARD, f"{tid} is still active")
        row = routing.comparison_sample(state) if state else archived.get(tid)
        if row is None:
            raise CrewError(EXIT_USAGE, f"unknown task: {tid}")
        row = dict(row)
        for field in ("main_verification", "boundary_violation_count", "boundary_check", "subagent_violation",
                      "tokens", "cost_usd", "duration_s"):
            row.setdefault(field, None)
        row["token_scope"] = scopes.get(row.get("agent"), "unmeasured")
        rows.append(row)
        model = row.get("model_actual") or row.get("model_requested") or "unknown model"
        cost = row["cost_usd"] if row["cost_usd"] is not None else "unmeasured"
        tokens = row["tokens"] if row["tokens"] is not None else "unmeasured"
        violations = row["boundary_violation_count"]
        lines.extend([
            f"{tid} | {row.get('agent')}/{model}/{row.get('effort')} | {row.get('status')} | {row.get('verdict') or 'unjudged'}",
            f"  worker seconds={row['duration_s']} tokens={tokens} ({row['token_scope']}) usd={cost}",
            f"  boundary violations={violations if violations is not None else 'unknown'} "
            f"subagent violation={row['subagent_violation']} {row['boundary_check'] or ''}".rstrip(),
            f"  verification: {row['main_verification'] or 'no text recorded'}",
        ])
    emit(rows, a.json, lines)
    return EXIT_OK


def _require_state(runs: st.Runs, tid: str) -> dict[str, Any]:
    if not re.fullmatch(TASK_ID_RE, tid):
        raise CrewError(EXIT_USAGE, f"invalid task id: {tid}")
    s = runs.load_state(tid)
    if not s:
        raise CrewError(EXIT_USAGE, f"unknown task: {tid}")
    return s


def recheck(root: Path, runs: st.Runs, s: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"attempts": []}
    for att in s.get("attempts", []):
        n = att["n"]
        parsed = adapters.parse(s["agent"], runs.file(att.get("events") or f"{s['task_id']}.a{n}.events.jsonl"),
                                runs.file(att.get("log") or ""), runs.file(att.get("result") or ""))
        status, reason, ws = classify.classify(parsed, exit_code=att.get("exit_code"),
                                               timed_out=att.get("failure_reason") == "timeout",
                                               cancelled=False)
        sub_path = runs.file(att.get("subagents") or "")
        used = "UNKNOWN"
        if sub_path.is_file():
            try:
                used = read_json(sub_path).get("used")
            except (OSError, ValueError):
                pass
        out["attempts"].append({"n": n, "status": status, "reason": reason, "worker_status": ws, "subagent_used": used})
    try:
        before = read_json(runs.file(f"{s['task_id']}.snap.before.json"))
        after = read_json(runs.file(f"{s['task_id']}.snap.after.json"))
        changes = snapshot.diff(before, after, root)
        wp = [root / p for p in s.get("write_paths", [])]
        if s.get("worker_handoff"):
            out["boundary_violations"] = runner.handoff_violations(runs, changes, s, root)
        else:
            out["boundary_violations"] = snapshot.violations(changes, s["mode"], wp, root)
    except (OSError, ValueError, KeyError):
        out["boundary_violations"] = "UNKNOWN"
    return out


def cmd_check(a) -> int:
    root, runs = _root_and_runs()
    s = _require_state(runs, a.task_id)
    if a.recheck:
        r = recheck(root, runs, s)
        lines = [f"recheck {a.task_id} (stored evidence only; state file unchanged):"]
        for x in r["attempts"]:
            lines.append(f"  attempt {x['n']}: {x['status']} ({x['reason']}), worker_status={x['worker_status']}, subagent_used={x['subagent_used']}")
        bv = r["boundary_violations"]
        lines.append(f"  boundary violations: {bv if isinstance(bv, str) else len(bv)}")
        emit(r, a.json, lines)
        return EXIT_OK
    lines = summarize(s, runs) + [f"verdict: {s.get('verdict')}"]
    if s.get("mode") == "read":
        missing = citations.unverified(root, _result_text(runs, s, True))
        s = {**s, "unverified_citations": missing}
        lines.extend(f"unverified citation: {ref}" for ref in missing)
    emit(s, a.json, lines)
    return EXIT_OK


def cmd_result(a) -> int:
    _, runs = _root_and_runs()
    s = _require_state(runs, a.task_id)
    print(_result_text(runs, s, a.raw))
    return EXIT_OK


def _result_text(runs, s, raw):
    att = (s.get("attempts") or [{}])[-1]
    path = runs.file(att.get("result") or "__none__")
    if raw and s.get("agent") == "agy" and att.get("events"):
        parsed = adapters.parse("agy", runs.file(att["events"]), runs.file(att.get("log") or "__none__"), path)
        if parsed.transcript and parsed.transcript != parsed.final_text:
            return "[AGY response transcript; may include intermediate responses]\n" + parsed.transcript
        if parsed.final_text:
            return parsed.final_text
    if not path.exists():
        return f"(no worker response was saved for {s['task_id']}; status {s.get('status')})"
    text = read_text(path)
    if raw:
        return text
    block = classify.parse_result_block(text)["block"]
    return block or "(RESULT section missing; last 60 lines of the response)\n" + "\n".join(text.splitlines()[-60:])


# ------------------------------------------------------------ verdicts

def cmd_accept(a) -> int:
    _, runs = _root_and_runs()
    s = _require_state(runs, a.task_id)
    status = s.get("status")
    if status in st.ACTIVE_STATES:
        raise CrewError(EXIT_GUARD, f"{a.task_id} is {status}; cannot judge yet")
    if s.get("verdict"):
        raise CrewError(EXIT_GUARD, f"{a.task_id} already judged: {s['verdict']}")
    violated = bool(s.get("boundary_violations")) or bool(s.get("subagent_violation"))
    if status == "DONE" and s.get("boundary_check"):
        status = "DONE_UNVERIFIED"
    if status == "DONE" and not violated:
        pass
    elif status in ("DONE_UNVERIFIED", "PARTIAL") and not violated:
        if not a.verified:
            raise CrewError(EXIT_GUARD, f"{status}: run your own verification and pass --verified \"<what you ran/checked>\"")
    else:
        if not (a.override and a.reason):
            raise CrewError(EXIT_GUARD, f"{status}{' with violations' if violated else ''}: requires --override --reason")
    s["verdict"] = "ACCEPTED"
    s["main_verification"] = a.verified
    s["override_reason"] = a.reason if a.override else None
    s["judged_at"] = now_iso()
    runs.save_state(s)
    print(f"{a.task_id}: ACCEPTED")
    return EXIT_OK


def cmd_reject(a) -> int:
    _, runs = _root_and_runs()
    s = _require_state(runs, a.task_id)
    if s.get("status") in st.ACTIVE_STATES:
        raise CrewError(EXIT_GUARD, f"{a.task_id} is {s.get('status')}; cannot judge yet")
    if s.get("verdict"):
        raise CrewError(EXIT_GUARD, f"{a.task_id} already judged: {s['verdict']}")
    s["verdict"] = "REJECTED"
    s["verdict_reason"] = a.reason
    s["judged_at"] = now_iso()
    runs.save_state(s)
    n = st.rejected_chain_count(runs.registry(), a.task_id)
    print(f"{a.task_id}: REJECTED" + (" — chain rejected twice; report to the user instead of re-delegating" if n >= 2 else ""))
    return EXIT_OK


# ------------------------------------------------------------ handoff

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s) or "task"


HANDOFF_EN = """# Handoff

- Agent: {agent}
- Task: {task}
- Status: {status}
- Time: {time} (Asia/Seoul)

## Changes
- TODO: purpose and key changes.
{diffstat}
## Delegated runs (crew)
{runs}
## Plans (crew)
{plans}
## Verification
{verification}

## Next steps / Notes
- TODO
"""

HANDOFF_KO = """# Handoff

- Agent: {agent}
- Task: {task}
- Status: {status}
- Time: {time} (Asia/Seoul)

## 변경
- TODO: 목적과 핵심 변경.
{diffstat}
## 위임 실행 (crew)
{runs}
## 계획 (crew)
{plans}
## 검증
{verification}

## 다음 단계 / 참고
- TODO
"""


def cmd_handoff(a) -> int:
    root, runs = _root_and_runs()
    base = handoff_dir(root) or (root / "handoffs")
    t = now()
    lh = latest_handoff(root)
    if a.tasks:
        ids = [x.strip() for x in a.tasks.split(",") if x.strip()]
        states = [s for s in (runs.load_state(i) for i in ids) if s]
    else:
        if a.since and a.since != "last":
            since = parse_iso(a.since)
            if since.tzinfo is None:
                since = since.replace(tzinfo=KST)
        elif lh:
            since = datetime.fromtimestamp(lh.stat().st_mtime, KST)
        else:
            since = t - timedelta(hours=24)
        states = [s for s in runs.iter_states() if s.get("started") and parse_iso(s["started"]) >= since]
    lang = a.lang or ("ko" if lh and re.search(r"[가-힣]", read_text(lh)) else "en")
    run_lines = []
    for s in sorted(states, key=lambda x: x.get("started") or ""):
        bv = len(s.get("boundary_violations") or [])
        run_lines.append(f"- {s['task_id']}: agent={s.get('agent')}, mode={s.get('mode')}, status={s.get('status')}, "
                         f"verdict={s.get('verdict')}, boundary_violations={bv}, subagent_used={s.get('subagent_used')}")
    if not run_lines:
        run_lines = ["- none"]
    diffstat = ""
    if snapshot.is_git_repo(root):
        r = subprocess.run(["git", "-C", str(root), "diff", "--stat", "--", "."], capture_output=True, check=False)
        out = r.stdout.decode("utf-8", "replace").rstrip()  # keep the first line's leading space
        if out:
            diffstat = "\n```\n" + "\n".join(out.splitlines()[-30:]) + "\n```\n"
    tpl = HANDOFF_KO if lang == "ko" else HANDOFF_EN
    plan_ids = {str(s["plan"]) for s in states if s.get("plan")}
    plan_lines = []
    try:
        plan_files = runs.plan_files()
    except CrewError as e:
        plan_files, plan_ids = [], set()
        plan_lines.append(f"- plans skipped: {e.message}")
    for path in plan_files:
        try:
            if pl.load(path).fields.get("status") == "active":
                plan_ids.add(path.stem)
        except ct.ContractError:
            continue
    records = _plan_records(runs)
    for pid in sorted(plan_ids):
        try:
            p = pl.load(runs.plan_path(pid))
        except (ct.ContractError, CrewError):
            plan_lines.append(f"- {pid}: plan file missing or unreadable")
            continue
        links = pl.linked_runs(pid, records)
        plan_lines.append(pl.redact("- " + pl.summary_line(p, links)))
        plan_lines += [pl.redact(f"  - note: {n}") for n in pl.step_notes(p, links)]
    checks = [f"- {s['task_id']}: {s['main_verification']}" for s in states if s.get("main_verification")]
    text = tpl.format(agent=a.agent, task=a.task, status=a.status, time=t.strftime("%Y-%m-%d %H:%M"),
                      verification="\n".join(checks) or ('- TODO: main이 실행한 검증과 결과.' if lang == "ko" else '- TODO: checks run by the main agent and their results.'),
                      diffstat=diffstat, runs="\n".join(run_lines) + "\n",
                      plans="\n".join(plan_lines or ["- none"]) + "\n")
    folder = base / t.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{t.strftime('%H%M')}-{_slug(a.agent)}-{_slug(a.task)}"
    path = folder / f"{stem}.md"
    n = 2
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            path = folder / f"{stem}-{n}.md"
            n += 1
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(str(path))
    return EXIT_OK


# ------------------------------------------------------------ clean

def cmd_clean(a) -> int:
    root, runs = _root_and_runs()
    do_sessions = a.sessions or not a.runs
    do_runs = a.runs
    cutoff = now() - timedelta(days=a.older_than)
    active_ids = {x.get("task_id") for x in runs.list_active()}
    lines = [("DELETE" if a.yes else "[dry-run]") + f" crew clean (older than {a.older_than}d, judged tasks only)"]
    report: dict[str, Any] = {"sessions": [], "runs": [], "skipped": [], "agy_report": []}
    total_bytes = 0
    for s in list(runs.iter_states()):
        tid = s["task_id"]
        if not s.get("verdict") or tid in active_ids or not s.get("ended"):
            continue
        if parse_iso(s["ended"]) > cutoff:
            continue
        if do_sessions:
            if s.get("agent") == "codex":
                tids = {att.get("session_ref") for att in s.get("attempts", []) if att.get("session_ref")}
                for p in s.get("worker_session_paths", []):
                    why = verify_codex_rollout_for_delete(p, tids)
                    if why:
                        report["skipped"].append({"task": tid, "path": p, "reason": why})
                        continue
                    size = path_size(Path(p))
                    total_bytes += size
                    report["sessions"].append({"task": tid, "path": p, "bytes": size})
                    if a.yes:
                        os.remove(p)
            elif s.get("agent") == "agy":
                for p in s.get("worker_session_paths", []):
                    size = path_size(Path(p)) if Path(p).exists() else 0
                    report["agy_report"].append({"task": tid, "path": p, "bytes": size})
        if do_runs:
            names = s.get("files") or []
            entry = {**routing.comparison_sample(s), "started": s.get("started"), "cleaned_at": now_iso()}
            report["runs"].append({"task": tid, "files": names})
            if a.yes:
                try:
                    runs.append_history(entry)
                except OSError as e:
                    report["skipped"].append({"task": tid, "path": ".agent-runs", "reason": f"history append failed: {e}"})
                    continue
                for name in names:
                    if name == f"{tid}.json":
                        continue
                    p = runs.file(name)
                    if p.is_file() and p.parent == runs.dir:
                        p.unlink()
                c = runs.contracts / f"{tid}.md"
                if c.is_file():
                    c.unlink()
                runs.state_path(tid).unlink()
    lines.append(f"codex session files: {len(report['sessions'])} ({total_bytes / 1e6:.1f} MB)")
    agy_bytes = sum(x["bytes"] for x in report["agy_report"])
    lines.append(f"agy conversations (report only, not deleted): {len(report['agy_report'])} paths ({agy_bytes / 1e6:.1f} MB)")
    if do_runs:
        lines.append(f".agent-runs tasks: {len(report['runs'])}")
    for sk in report["skipped"][:10]:
        lines.append(f"skipped {sk['path']}: {sk['reason']}")
    if not a.yes:
        lines.append("re-run with --yes to delete")
    emit(report, a.json, lines)
    return EXIT_OK


# ------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crew", description="Delegate tasks to worker AI CLIs and verify them.")
    p.add_argument("--version", action="version", version=f"crew {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_json(sp):
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("init", help="create project instructions if none exist at the project root")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("detect", help="inspect CLIs, project root, instructions, active tasks")
    sp.add_argument("--paths", nargs="*", help="target paths whose instruction files should also be scanned")
    add_json(sp)
    sp.set_defaults(func=cmd_detect)

    cp = sub.add_parser("contract", help="create or check a task contract")
    csub = cp.add_subparsers(dest="ccmd", required=True)
    sp = csub.add_parser("new")
    sp.add_argument("task_id")
    sp.add_argument("--agent", choices=AGENTS)
    sp.add_argument("--mode", choices=("read", "write"), default="read")
    sp.add_argument("--write", nargs="*")
    sp.add_argument("--kind", choices=ct.TASK_KINDS)
    sp.add_argument("--plan", help="plan id this task belongs to (requires --step)")
    sp.add_argument("--step", help="step id in that plan")
    sp.set_defaults(func=cmd_contract_new)
    sp = csub.add_parser("check")
    sp.add_argument("file")
    sp.add_argument("--agent", choices=AGENTS)
    sp.add_argument("--effort")
    sp.add_argument("--model")
    add_json(sp)
    sp.set_defaults(func=cmd_contract_check)

    pp = sub.add_parser("plan", help="create, check or show main's plan for a multi-step goal")
    psub = pp.add_subparsers(dest="pcmd", required=True)
    sp = psub.add_parser("new", help="create .agent-runs/plans/<PLAN_ID>.md from the template")
    sp.add_argument("plan_id")
    sp.set_defaults(func=cmd_plan_new)
    sp = psub.add_parser("check", help="validate a plan and compare step status with linked runs")
    sp.add_argument("plan_id")
    add_json(sp)
    sp.set_defaults(func=cmd_plan_check)
    sp = psub.add_parser("show", help="list plans, or show one plan with linked runs and ready steps")
    sp.add_argument("plan_id", nargs="?")
    add_json(sp)
    sp.set_defaults(func=cmd_plan_show)

    sp = sub.add_parser("run", help="run a worker for a contract (waits until done)")
    sp.add_argument("--contract", dest="file", required=True)
    sp.add_argument("--agent", choices=AGENTS)
    sp.add_argument("--effort")
    sp.add_argument("--model")
    sp.add_argument("--timeout", type=int, help="minutes")
    sp.add_argument("--approve-cost", action="store_true")
    sp.add_argument("--approve-data-sharing", action="store_true",
                    help="user confirmed sending this project's content to the worker's provider")
    sp.add_argument("--allow-unbounded-write", action="store_true",
                    help="deprecated; agy write tasks are refused")
    sp.add_argument("--with-result", action="store_true", help="include the saved worker result in the run output")
    add_json(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("routing", help="show recent outcomes for a similar kind of worker task")
    sp.add_argument("--kind", choices=ct.TASK_KINDS, required=True)
    sp.add_argument("--mode", choices=("read", "write"), required=True)
    add_json(sp)
    sp.set_defaults(func=cmd_routing)

    sp = sub.add_parser("compare", help="compare completed worker runs by task ID")
    sp.add_argument("task_ids", nargs="+")
    add_json(sp)
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("status")
    sp.add_argument("--all", action="store_true")
    add_json(sp)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("check")
    sp.add_argument("task_id")
    sp.add_argument("--recheck", action="store_true")
    add_json(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("result")
    sp.add_argument("task_id")
    sp.add_argument("--raw", action="store_true")
    sp.set_defaults(func=cmd_result)

    sp = sub.add_parser("accept")
    sp.add_argument("task_id")
    sp.add_argument("--verified")
    sp.add_argument("--override", action="store_true")
    sp.add_argument("--reason")
    sp.set_defaults(func=cmd_accept)

    sp = sub.add_parser("reject")
    sp.add_argument("task_id")
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_reject)

    sp = sub.add_parser("handoff")
    sp.add_argument("--agent", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--status", required=True, choices=("Done", "In progress", "Blocked"))
    sp.add_argument("--since")
    sp.add_argument("--tasks")
    sp.add_argument("--lang", choices=("en", "ko"))
    sp.set_defaults(func=cmd_handoff)

    sp = sub.add_parser("clean")
    sp.add_argument("--sessions", action="store_true")
    sp.add_argument("--runs", action="store_true")
    sp.add_argument("--older-than", type=int, default=7)
    sp.add_argument("--yes", action="store_true")
    add_json(sp)
    sp.set_defaults(func=cmd_clean)

    sp = sub.add_parser("cancel")
    sp.add_argument("task_id")
    sp.add_argument("--target", help="worker identity printed by the cancellation preview")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_cancel)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except CrewError as e:
        print(f"crew: {e.message}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("crew: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
