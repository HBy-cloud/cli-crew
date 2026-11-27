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
    # Bare "quota" is too broad: agy logs "Failed to retrieve user quota summary" when it has no network.
    r"usage limit", r"quota (exceeded|exhausted|reached)", r"(exceeded|out of) (your |the )?quota", r"exceeded your",
    r"insufficient_quota", r"limit reached",
]
# A refused connection (no network, sandbox, firewall) does not clear on an immediate retry.
NETWORK_BLOCK_PATTERNS = [r"ECONNREFUSED", r"connection refused", r"firewall or proxy"]
TRANSIENT_PATTERNS = [
    r"ECONNRESET", r"ETIMEDOUT", r"connection (reset|closed|error)",
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
    blocks = list(re.finditer(r"(?ms)^## REVIEW[ \t]*\r?$(.*?)(?=^## |\Z)", text))
    m = blocks[-1] if blocks else None  # the last review wins over an earlier draft
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


def _failure(diag: list[str], fallback: str) -> str:
    if _any(NETWORK_BLOCK_PATTERNS, diag):
        return "network_blocked"
    return "transient" if _any(TRANSIENT_PATTERNS, diag) else fallback


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
        return "FAILED", _failure(diag, f"exit_{exit_code}"), ws
    if p.is_error:
        return "FAILED", _failure(diag, "worker_error"), ws
    if ws == "failed":
        return "FAILED", "worker_reported_failed", ws
    if _any(BLOCK_PATTERNS, signals) or p.permission_denials or ws == "blocked":
        return "BLOCKED", "blocked", ws
    if not (p.final_text or "").strip():
        return "FAILED", _failure(diag, "empty_response"), ws
    if ws in (None, "partial"):
        return "PARTIAL", "no_result_block" if rb["block"] is None else ("invalid_status" if ws is None else "partial"), ws
    if _any(UNVERIFIED_PATTERNS, signals):
        return "DONE_UNVERIFIED", "verification_blocked", ws
    return "DONE", None, ws
