"""RESULT-block parsing and run status classification (SPEC 5.3, 7.1)."""

from __future__ import annotations

import re
from typing import Any

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


def parse_result_block(text: str) -> dict[str, Any]:
    """Last '## RESULT' block (header included) and its fields."""
    idx = text.rfind("## RESULT")
    if idx < 0:
        return {"block": None, "status": None, "fields": {}}
    block = text[idx:].strip()
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
    status = re.split(r"[\s|,]", status)[0] if status else ""
    return {"block": block, "status": status if status in WORKER_STATUSES else None, "fields": fields}


def classify(p: Parsed, *, exit_code: int | None, timed_out: bool, cancelled: bool) -> tuple[str, str | None, str | None]:
    """Return (status, failure_reason, worker_status)."""
    rb = parse_result_block(p.final_text or "")
    ws = rb["status"]
    diag = p.diag
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
    if _any(BLOCK_PATTERNS, diag) or p.permission_denials or ws == "blocked":
        return "BLOCKED", "blocked", ws
    if not (p.final_text or "").strip():
        reason = "transient" if _any(TRANSIENT_PATTERNS, diag) else "empty_response"
        return "FAILED", reason, ws
    if ws in (None, "partial"):
        return "PARTIAL", "no_result_block" if rb["block"] is None else ("invalid_status" if ws is None else "partial"), ws
    if _any(UNVERIFIED_PATTERNS, diag):
        return "DONE_UNVERIFIED", "verification_blocked", ws
    return "DONE", None, ws
