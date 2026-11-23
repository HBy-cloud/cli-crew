"""RESULT-block parsing and run status classification (SPEC 5.3, 7.1)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from crew.adapters import Parsed

WORKER_STATUSES = {"success", "partial", "failed", "blocked"}

BLOCK_PATTERNS = [
    r"permission check failed", r"user denied permission", r"patch rejected",
    r"rejected by user approval", r"auto-denied", r"no output produced", r"failed to write file",
]
UNVERIFIED_PATTERNS = [
    r"\bEPERM\b", r"CreateProcessAsUserW", r"access is denied", r"액세스가 거부",
]
USAGE_LIMIT_PATTERNS = [
    r"usage limit", r"quota", r"exceeded your", r"insufficient_quota", r"limit reached",
]
TRANSIENT_PATTERNS = [
    r"ECONNRESET", r"ETIMEDOUT", r"ECONNREFUSED", r"connection (reset|refused|closed|error)",
    r"\b50[234]\b", r"service unavailable", r"\b429\b", r"rate[ _-]?limit", r"overloaded",
    r"stream disconnected", r"network error",
]


def _any(patterns: list[str], lines: list[str]) -> bool:
    text = "\n".join(lines)
    return any(re.search(p, text, re.I) for p in patterns)


def last_result_block(text: str) -> str | None:
    last = None
    for heading in re.finditer(r"(?m)^## RESULT[ \t]*\r?$", text):
        last = heading.start()
    return text[last:].strip() if last is not None else None


def parse_review_block(text: str) -> dict[str, Any]:
    """Reviewer output: '## REVIEW' block with VERDICT (PASS | FAIL | PARTIAL) and per-criterion lines (met | unmet | unverified)."""
    m = re.search(r"(?ms)^## REVIEW[ \t]*\r?$(.*?)(?=^## |\Z)", text)
    if not m:
        return {"verdict": None, "criteria": 0}
    block = m.group(1)
    v = re.search(r"(?m)^\s*VERDICT\s*:\s*(PASS|FAIL|PARTIAL)\b", block)
    crit = [ln for ln in block.splitlines() if re.search(r"(?i)\b(unmet|unverified|met)\b", ln) and not re.match(r"\s*VERDICT", ln)]
    return {"verdict": v.group(1) if v else None, "criteria": len(crit)}


def parse_result_block(text: str) -> dict[str, Any]:
    """Last '## RESULT' block (header included) and its fields."""
    block = last_result_block(text)
    if block is None:
        return {"block": None, "status": None, "fields": {}}
    fields: dict[str, str] = {}
    key = None
    for line in block.splitlines()[1:]:
        m = re.match(r"^\s*([A-Z_]+)\s*:\s*(.*)$", line)
        if m:
            key = m.group(1)
            fields[key] = m.group(2).strip()
        elif key:
            fields[key] += "\n" + line
    status = (fields.get("STATUS") or "").strip().lower()
    return {"block": block, "status": status if status in WORKER_STATUSES else None, "fields": fields}


def classify(p: Parsed, *, exit_code: int | None, timed_out: bool, cancelled: bool) -> tuple[str, str | None, str | None]:
    """Return (status, failure_reason, worker_status)."""
    rb = parse_result_block(p.final_text or "")
    ws = rb["status"]
    diag = p.diag
    signals = diag + p.tool_output  # permission/sandbox signals may also appear in failed command output
    if cancelled:
        return "CANCELLED", "cancelled", ws
    if timed_out or p.print_timeout:
        return "TIMEOUT", "timeout", ws
    if _any(USAGE_LIMIT_PATTERNS, diag):
        return "FAILED", "usage_limit", ws
    if exit_code not in (0, None):
        reason = "transient" if _any(TRANSIENT_PATTERNS, diag) else f"exit_{exit_code}"
        return "FAILED", reason, ws
    if p.is_error:
        reason = "transient" if _any(TRANSIENT_PATTERNS, diag) else "worker_error"
        return "FAILED", reason, ws
    if ws == "failed":
        return "FAILED", "worker_reported_failed", ws
    if _any(BLOCK_PATTERNS, signals) or p.permission_denials or ws == "blocked":
        return "BLOCKED", "blocked", ws
    if not (p.final_text or "").strip():
        reason = "transient" if _any(TRANSIENT_PATTERNS, diag) else "empty_response"
        return "FAILED", reason, ws
    if ws in (None, "partial"):
        return "PARTIAL", "no_result_block" if rb["block"] is None else ("invalid_status" if ws is None else "partial"), ws
    if _any(UNVERIFIED_PATTERNS, signals):
        return "DONE_UNVERIFIED", "verification_blocked", ws
    return "DONE", None, ws
