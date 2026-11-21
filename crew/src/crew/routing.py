"""Compact project-local evidence for main's next delegation decision."""

from __future__ import annotations

from typing import Any

from crew.state import Runs


def sample(state: dict[str, Any]) -> dict[str, Any]:
    row = {k: state.get(k) for k in (
        "task_id", "task_kind", "mode", "agent", "effort", "model_actual", "model_requested",
        "ended", "duration_s", "status", "verdict", "retry_of",
    )}
    row["verification_recorded"] = bool(state.get("main_verification"))
    row["override"] = bool(state.get("override_reason"))
    attempts = state.get("attempts") or []
    row["attempts_count"] = len(attempts)
    for metric in ("tokens", "cost_usd"):
        values = [a.get(metric) for a in attempts]
        row[metric] = sum(values) if values and None not in values else None
    return row


def recent(runs: Runs, kind: str, mode: str) -> list[dict[str, Any]]:
    rows = {}
    for record in runs.iter_history():
        if record.get("task_kind") == kind and record.get("mode") == mode and record.get("ended"):
            rows[record["task_id"]] = record
    for state in runs.iter_states():
        if state.get("task_kind") == kind and state.get("mode") == mode and state.get("ended"):
            rows[state["task_id"]] = sample(state)
    return sorted(rows.values(), key=lambda r: (r["ended"], r["task_id"]), reverse=True)[:10]


def comparison_sample(state: dict[str, Any]) -> dict[str, Any]:
    return {
        **sample(state),
        "main_verification": state.get("main_verification"),
        "boundary_violation_count": len(state["boundary_violations"]) if "boundary_violations" in state else None,
        "boundary_check": state.get("boundary_check"),
        "subagent_violation": state.get("subagent_violation"),
        "failure_reason": state.get("failure_reason"),
        "plan": state.get("plan"),
        "plan_step": state.get("plan_step"),
    }
