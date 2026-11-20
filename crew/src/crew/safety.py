"""Safety helpers: instruction scanning (delegation limits, sensitive data) and worker env filtering."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from crew.common import home_dir, is_drive_root, read_text, real, same_path

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")

RESTRICT_RE = re.compile(
    r"(non-?interactive|비대화형|external agent|외부 (에이전트|프로세스)|agent process|background service|"
    r"delegat|위임|subagent|helper agent|codex exec|\bagy\b|copilot|claude -p|other ai)", re.I)
NEG_RE = re.compile(
    r"(must not|do not|don't|never|not be introduced|without|금지|않|하지 마|별도 요청|only when|"
    r"requires? (approval|permission))", re.I)

# Personal / sensitive data hints (deliberately not generic "secrets", which almost every AGENTS.md mentions)
PRIVACY_RE = re.compile(
    r"(개인\s?정보|개인 원문|민감\s?(정보|데이터)|personal (data|information)|\bPII\b|privacy|"
    r"이력서|자기소개서|(?<![-\w])r[eé]sum[eé]s?\b(?!\s+(?:id|ID)\b)|curriculum vitae|주민(등록)?번호|전화번호|phone number|email address|"
    r"\[EMAIL\]|\[PHONE\]|mask(ing)?|마스킹|confidential|기밀|고객 (데이터|정보)|customer data|patient|의료|health record)",
    re.I)

# Environment variables that look like credentials (case-insensitive glob)
SECRET_ENV_PATTERNS = (
    "*TOKEN*", "*SECRET*", "*PASSWORD*", "*PASSWD*", "*API_KEY*", "*APIKEY*", "*ACCESS_KEY*",
    "*PRIVATE_KEY*", "*CREDENTIAL*", "AWS_*", "AZURE_*", "GCP_*", "GOOGLE_APPLICATION_CREDENTIALS",
    "*_PAT", "NPM_CONFIG__AUTH*", "*SESSION_KEY*", "*COOKIE*",
)
# Each worker keeps only its own provider credentials (needed if the user authenticates by API key)
OWN_PROVIDER_ENV = {
    "codex": {"OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT", "CODEX_API_KEY"},
    "agy": {"GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT"},
    "claude": {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN"},
}


def instruction_dirs(root: Path, cwd: Path, targets: list[str]) -> list[Path]:
    """cwd and its ancestors (stopping before home/drive root) + root..target chains."""
    dirs: list[Path] = []
    cur = real(cwd)
    home = home_dir()
    while not is_drive_root(cur) and not same_path(cur, home):
        dirs.append(cur)
        cur = cur.parent
    for t in targets:
        target = real(root / t) if not os.path.isabs(t) else real(t)
        if target.is_file() or not target.exists():
            target = target.parent
        chain = []
        cur = target
        while True:
            chain.append(cur)
            if same_path(cur, root) or is_drive_root(cur):
                break
            cur = cur.parent
        dirs.extend(reversed(chain))
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(str(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def scan_instructions(dirs: list[Path]) -> dict[str, list[str]]:
    files, restrict, privacy = [], [], []
    for d in dirs:
        for name in INSTRUCTION_FILES:
            p = d / name
            if not p.is_file():
                continue
            files.append(str(p))
            for i, line in enumerate(read_text(p).splitlines(), 1):
                text = line.strip()
                if RESTRICT_RE.search(text) and NEG_RE.search(text):
                    restrict.append(f"{p}:{i}: {text[:200]}")
                if PRIVACY_RE.search(text):
                    privacy.append(f"{p}:{i}: {text[:200]}")
    return {"files": files, "restrictions": restrict, "privacy": privacy}


def is_secret_env(name: str) -> bool:
    up = name.upper()
    return any(fnmatch.fnmatchcase(up, pat) for pat in SECRET_ENV_PATTERNS)


def worker_env(agent: str, base: dict[str, str], passthrough: list[str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Copy of `base` without credential-like variables, except the worker's own provider keys."""
    keep = OWN_PROVIDER_ENV.get(agent, set()) | {p.upper() for p in (passthrough or [])}
    env, stripped = {}, []
    for k, v in base.items():
        if is_secret_env(k) and k.upper() not in keep:
            stripped.append(k)
            continue
        env[k] = v
    return env, sorted(stripped)
