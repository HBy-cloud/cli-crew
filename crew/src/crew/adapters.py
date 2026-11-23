"""Worker CLI adapters: command assembly and output parsing (SPEC 6)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew.classify import last_result_block
from crew.common import (
    EXIT_GUARD, EXIT_USAGE, SECRET_NAME_PATTERNS, CrewError, home_dir, is_secret_name, is_within, read_text, real, same_path,
)

FORBIDDEN_FLAGS = ("--dangerously", "bypassPermissions", "--allow-all", "--yolo")
AGY_MAX_CMDLINE_UTF16 = 30000

# CLI versions these adapters were verified against (skill/cli-crew/references/adapters.md, SPEC 6).
# Update together with that reference after re-verifying flags and output formats.
VERIFIED_VERSIONS = {"codex": "0.171.0", "agy": "1.5.14", "claude": "2.1.330", "copilot": "1.0.112"}


def version_number(text: str | None) -> str | None:
    m = re.search(r"\d+(?:\.\d+)+(?:-[0-9A-Za-z.-]+)?", text or "")  # keep pre-release suffixes
    return m.group(0) if m else None


def version_warning(agent: str, text: str | None) -> str | None:
    """A warning when an installed CLI is not the version the adapter was verified with."""
    want = VERIFIED_VERSIONS.get(agent)
    if text is None or want is None:
        return None
    found = version_number(text)
    if found is None:
        return f"{agent}: no version number in {text!r}; adapter verified with {want}"
    if found != want:
        return (f"{agent} {found} differs from the adapter-verified {want}; "
                "flags or output formats may have changed, so check run results closely")
    return None


def binary_prefix(agent: str) -> list[str]:
    """Executable for an agent. CREW_BIN_<AGENT> (JSON list or path) overrides, for tests."""
    override = os.environ.get(f"CREW_BIN_{agent.upper()}")
    if override:
        try:
            val = json.loads(override)
            if isinstance(val, list):
                return [str(x) for x in val]
        except json.JSONDecodeError:
            pass
        return [override]
    exe = shutil.which(agent)
    if os.name == "nt" and agent == "copilot" and (not exe or Path(exe).suffix.lower() != ".exe"):
        # The npm shim is a batch file. Launch its JavaScript entry point without a shell.
        if exe and Path(exe).suffix.lower() == ".cmd":
            loader = Path(exe).parent / "node_modules" / "@github" / "copilot" / "npm-loader.js"
            node = Path(exe).with_name("node.exe")
            node = str(node) if node.is_file() else shutil.which("node.exe")
            if node and loader.is_file():
                return [node, str(loader)]
        exe = shutil.which("copilot.exe")
    if not exe:
        raise CrewError(EXIT_USAGE, f"{agent} CLI not found on PATH")
    return [exe]


def cli_version(agent: str) -> str | None:
    try:
        args = binary_prefix(agent) + ["--version"]
        if agent == "copilot":
            args.append("--no-auto-update")
        r = subprocess.run(args, capture_output=True, timeout=30, check=False)
    except (CrewError, OSError, subprocess.TimeoutExpired):
        return None
    text = (r.stdout or b"").decode("utf-8", "replace").strip() or (r.stderr or b"").decode("utf-8", "replace").strip()
    return text.splitlines()[0] if text else None


@dataclass
class Invocation:
    args: list[str]
    stdin_text: str | None


def _assert_safe(args: list[str]) -> None:
    for a in args:
        for bad in FORBIDDEN_FLAGS:
            if bad in a and not a.startswith("--print="):
                raise CrewError(EXIT_USAGE, f"refusing to assemble forbidden flag: {a}")


def agy_file_instruction(prompt_rel: str) -> str:
    return (
        "You are a WORKER agent. Read the file "
        f"'{prompt_rel}' (relative to the current working directory) using your file viewing tool, "
        "then carry out the task it describes exactly, following every rule in it, "
        "and end your reply with the '## RESULT' block it specifies. Do not run shell commands."
    )


def copilot_permission_pattern(path: Path) -> str:
    """Escape literal paths for the Copilot tool permission glob syntax."""
    return re.sub(r"([*?\[\]])", lambda m: f"[{m[0]}]", path.as_posix())


def copilot_environment(env: dict[str, str], root: Path) -> dict[str, str]:
    """Run with an explicitly selected, authenticated profile and controlled permissions."""
    configured = env.get("COPILOT_HOME")
    if not configured or not Path(configured).is_absolute():
        raise CrewError(EXIT_GUARD, "copilot requires an absolute COPILOT_HOME for a dedicated profile")
    profile = real(configured)
    if (not profile.is_dir() or same_path(profile, home_dir() / ".copilot")
            or is_within(profile, root) or is_within(root, profile)):
        raise CrewError(EXIT_GUARD, "use a dedicated COPILOT_HOME separate from the project and default profile")
    for name in ("permissions-config.json", "mcp-config.json", "providers.json"):
        if (profile / name).exists() or (profile / name).is_symlink():
            raise CrewError(EXIT_GUARD, f"the dedicated Copilot profile must not contain {name}")
    # Repository settings can supply hooks or override disableAllHooks. Inspect their presence only.
    for directory in (root, *root.parents):
        for settings_dir in (".github/copilot", ".claude"):
            for name in ("settings.json", "settings.local.json"):
                settings_path = directory / settings_dir / name
                if settings_path.exists() or settings_path.is_symlink():
                    raise CrewError(EXIT_GUARD, f"copilot cannot safely run with repository settings: {settings_path}")
        if (directory / ".git").exists() or directory == home_dir():
            break
    result = {key: value for key, value in env.items() if not key.upper().startswith("COPILOT_")}
    result.update(
        COPILOT_HOME=str(profile),
        COPILOT_CACHE_HOME=str(profile / "cache"),
        COPILOT_ALLOW_ALL="false",
        COPILOT_ASSISTED_APPROVAL="false",
        COPILOT_AUTO_UPDATE="false",
        COPILOT_PLUGIN_DIR_ONLY="true",
        COPILOT_TELEMETRY_DISABLED="true",
        USE_TGREP="false",
    )
    try:
        check = subprocess.run(
            binary_prefix("copilot") + ["--no-auto-update", "config", "disableAllHooks"],
            cwd=root, env=result, capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CrewError(EXIT_GUARD, "could not check the dedicated Copilot profile's hook setting") from exc
    if check.returncode or check.stdout.strip() != b"true":
        raise CrewError(EXIT_GUARD, "set disableAllHooks to true in the dedicated Copilot profile")
    return result


def copilot_model(model: Any) -> str | None:
    """Explicit Copilot model id, or None for Auto (null or any case/whitespace variant of 'auto')."""
    if not isinstance(model, str) or not model.strip() or model.strip().lower() == "auto":
        return None
    return model


def build(agent: str, s: dict[str, Any], *, root: Path, add_dirs: list[Path], prompt_text: str,
          prompt_rel: str, result_path: Path, cfg: dict[str, Any]) -> Invocation:
    mode = s.get("cli_mode") or s["mode"]
    effort = s["effort"]
    model = s.get("model")
    prefix = binary_prefix(agent)
    if agent == "codex":
        args = prefix + ["exec", "-s", "read-only" if mode == "read" else "workspace-write",
                         "-C", str(root)]
        if mode == "read":
            for d in add_dirs:
                args += ["--add-dir", str(d)]
        args += ["--skip-git-repo-check", "--json"]
        if s.get("ephemeral"):
            args.append("--ephemeral")
        args += ["-o", str(result_path), "-c", f'model_reasoning_effort="{effort}"']
        if model:
            args += ["-m", model]
        args.append("-")
        inv = Invocation(args, prompt_text)
    elif agent == "agy":
        args = prefix + ["--mode", "plan" if mode == "read" else "accept-edits", "--effort", effort]
        if model:
            args += ["--model", model]
        args += ["--output-format", "stream-json", "--print-timeout", f"{s['timeout_min']}m"]
        if s.get("agy_settings"):
            args += ["--settings", str(s["agy_settings"])]
        if mode == "read":
            for d in add_dirs:
                args += ["--add-dir", str(d)]
        if cfg.get("agy_prompt_mode", "file") == "inline":
            text = prompt_text
        else:
            text = agy_file_instruction(prompt_rel)
        args.append("--print=" + text)
        if len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2 > AGY_MAX_CMDLINE_UTF16:
            raise CrewError(EXIT_USAGE, "agy command line too long; shorten the contract or use agy_prompt_mode=file")
        inv = Invocation(args, None)
    elif agent == "claude":
        args = prefix + ["-p", "--restricted", "--permission-mode",
                         "plan" if mode == "read" else "acceptEdits",
                         "--permission-prompts", "none", "--output-format", "json",
                         "--no-session-persistence", "--effort", effort]
        if model:
            args += ["--model", model]
        if mode == "read":
            for d in add_dirs:
                args += ["--add-dir", str(d)]
        inv = Invocation(args, prompt_text)
    elif agent == "copilot":
        target_path = Path(s["copilot_target"])
        path = real(target_path)
        if (os.path.normcase(path) != os.path.normcase(target_path) or not is_within(path, root)
                or not path.is_file() or is_secret_name(path.name)):
            raise CrewError(EXIT_GUARD, "copilot write target must still be an existing, non-secret project file")
        target = copilot_permission_pattern(path)
        read_denials = [f"--deny-tool=read({pattern})" for pattern in SECRET_NAME_PATTERNS]
        args = prefix + [
            "--no-auto-update", "--no-custom-instructions", "--disable-builtin-mcps",
            "--disallow-temp-dir", "--no-ask-user", "--no-color", "--sync-session=off",
            "--mode", "interactive", "--available-tools", "view", "apply_patch",
            "--deny-tool=shell", "--deny-tool=memory", *read_denials,
            f"--allow-tool=write({target})", "--max-ai-credits=30", "--output-format=json",
            f"--model={copilot_model(model) or 'auto'}", "-p", prompt_text,
        ]
        if os.name == "nt" and len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2 > 30000:
            raise CrewError(EXIT_USAGE, "copilot command line too long; shorten the contract")
        inv = Invocation(args, None)
    else:
        raise CrewError(EXIT_USAGE, f"unknown agent: {agent}")
    flags_end = -1 if agent == "copilot" else len(inv.args)
    _assert_safe(inv.args[len(prefix):flags_end])
    return inv


# ------------------------------------------------------------ parsing

@dataclass
class Parsed:
    final_text: str = ""
    transcript: str = ""
    diag: list[str] = field(default_factory=list)  # diagnostic-channel lines
    # Output of failed worker tool commands: may contain permission/sandbox signals, but also arbitrary program text,
    # so it is used only for BLOCKED/UNVERIFIED matching, never for usage-limit or transient matching.
    tool_output: list[str] = field(default_factory=list)
    session_ref: str | None = None
    model_actual: str | None = None
    cost_usd: float | None = None
    tokens: int | None = None
    is_error: bool = False
    permission_denials: list[Any] = field(default_factory=list)
    print_timeout: bool = False
    subagent_used: bool | None = None  # None = unknown from this source
    subagent_ids: list[str] = field(default_factory=list)


AGY_DIAG_PREFIX = re.compile(r"^\s*(jetski:|\[agy\]|AGY_ERROR|error:)", re.I)


def _token_sum(usage: Any, fields: tuple[str, ...]) -> int | None:
    if not isinstance(usage, dict):
        return None
    counts = [usage.get(k) for k in fields]
    if any(type(n) is not int or n < 0 for n in counts):
        return None
    return sum(counts)


def _jsonl(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    objs, other = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                objs.append(json.loads(line))
                continue
            except json.JSONDecodeError:
                pass
        other.append(line)
    return objs, other


def parse(agent: str, stdout_path: Path, stderr_path: Path, result_path: Path) -> Parsed:
    out = read_text(stdout_path)
    err = read_text(stderr_path)
    p = Parsed()
    p.diag.extend(l for l in err.splitlines() if l.strip())

    if agent == "codex":
        objs, other = _jsonl(out)
        p.diag.extend(other)
        completed = [o for o in objs if o.get("type") == "turn.completed"]
        totals = [_token_sum(o.get("usage"), ("input_tokens", "output_tokens")) for o in completed]
        started = sum(o.get("type") == "turn.started" for o in objs)
        if (totals and None not in totals and started <= len(completed)
                and not any(o.get("type") == "turn.failed" for o in objs)):
            p.tokens = sum(totals)
        for o in objs:
            t = o.get("type")
            if t == "thread.started":
                p.session_ref = o.get("thread_id") or p.session_ref
            if t in ("error", "turn.failed"):
                p.diag.append(json.dumps(o, ensure_ascii=False))
            item = o.get("item") or {}
            if item.get("type") == "error":
                p.diag.append(str(item.get("message", "")))
            if item.get("type") == "command_execution" and item.get("status") == "failed":
                p.tool_output.append(str(item.get("aggregated_output", ""))[:2000])
        p.final_text = read_text(result_path)

    elif agent == "agy":
        objs, other = _jsonl(out)
        p.diag.extend(other)  # non-JSON lines are CLI messages (jetski:, [agy], ...)
        steps: dict[int, list[str]] = {}
        result_obj: dict[str, Any] | None = None
        step_errors = []
        subagent_used = False
        for o in objs:
            ev = o.get("event")
            if ev == "init":
                p.session_ref = o.get("conversation_id") or p.session_ref
                p.model_actual = (o.get("init") or {}).get("model")
            elif ev == "step_update":
                su = o.get("step_update") or {}
                if su.get("state") == "ERROR" or su.get("error"):
                    step_errors.append(json.dumps(su.get("error") or su, ensure_ascii=False)[:2000])
                if su.get("step_type") == "subagent" or su.get("tool_name") == "invoke_subagent":
                    subagent_used = True
                    for sub in (su.get("subagent_info") or {}).get("subagents") or []:
                        cid = sub.get("conversation_id")
                        if cid and cid not in p.subagent_ids:
                            p.subagent_ids.append(cid)
                if su.get("step_type") == "agent_response" and su.get("text_delta"):
                    steps.setdefault(int(su.get("step_index", 0)), []).append(su["text_delta"])
            elif ev == "result":
                result_obj = o.get("result") or {}
        p.subagent_used = subagent_used
        if result_obj is not None:
            p.session_ref = result_obj.get("conversation_id") or p.session_ref
            if result_obj.get("finished_reason") == "print_timeout":
                p.print_timeout = True
            if result_obj.get("status") not in (None, "SUCCESS"):
                p.is_error = True
                p.diag.append(f"agy result status={result_obj.get('status')} error={result_obj.get('error')}")
            elif result_obj.get("error"):
                p.is_error = True
                p.diag.append(f"agy error={result_obj.get('error')}")
        if not result_obj or result_obj.get("status") != "SUCCESS" or p.is_error:
            p.diag.extend(step_errors)
        messages = ["".join(steps[i]) for i in sorted(steps)]
        if messages:
            p.final_text = messages[-1]
        response = (result_obj or {}).get("response") or ""
        if response and all(message in response for message in messages):
            p.transcript = response
        else:
            p.transcript = "\n\n".join(messages)
            if response and response not in p.transcript:
                p.transcript += "\n\n[Terminal response]\n" + response
        # A footer from an earlier streamed response cannot complete the last response.
        older_footer = any(last_result_block(message) for message in messages[:-1])
        if response and not older_footer and not last_result_block(p.final_text):
            p.final_text = last_result_block(response) or p.final_text or response
        if not objs and out.strip():
            # text-mode style output (e.g. 'jetski: no output produced ...')
            for line in out.splitlines():
                if AGY_DIAG_PREFIX.match(line):
                    p.diag.append(line)

    elif agent == "claude":
        data = None
        try:
            data = json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            objs, other = _jsonl(out)
            p.diag.extend(other)
            data = objs[-1] if objs else None
        if isinstance(data, dict):
            p.final_text = data.get("result") or ""
            p.is_error = bool(data.get("is_error"))
            p.permission_denials = data.get("permission_denials") or []
            p.session_ref = data.get("session_id")
            p.cost_usd = data.get("total_cost_usd")
            p.tokens = _token_sum(data.get("usage"), ("input_tokens", "output_tokens",
                                                      "cache_creation_input_tokens", "cache_read_input_tokens"))
            mu = data.get("modelUsage") or {}
            if mu:
                p.model_actual = ",".join(mu.keys())
            ss = data.get("subagent_stats")
            if isinstance(ss, dict):
                p.subagent_used = (ss.get("spawned") or 0) > 0
            if p.is_error:
                p.diag.append(f"claude is_error subtype={data.get('subtype')} api_error_status={data.get('api_error_status')}")
            for d in p.permission_denials:
                p.diag.append(f"claude permission_denial: {json.dumps(d, ensure_ascii=False)[:500]}")
    elif agent == "copilot":
        objs, other = _jsonl(out)
        p.diag.extend(other)
        terminal = False
        for event in objs:
            kind = event.get("type")
            data = event.get("data", event)
            if not isinstance(data, dict):
                continue
            if kind == "session.start":
                p.session_ref = data.get("sessionId")
                if p.subagent_used is None:
                    p.subagent_used = False
            elif kind == "assistant.message" and not data.get("toolRequests"):
                if isinstance(data.get("content"), str):
                    p.final_text = data["content"]
                    p.model_actual = data.get("model")
            elif kind == "tool.execution_start":
                p.final_text = ""
                if data.get("toolName") in ("task", "invoke_subagent"):
                    p.subagent_used = True
            elif kind == "tool.execution_complete" and data.get("success") is False:
                detail = json.dumps(data.get("error") or data.get("result"), ensure_ascii=False)[:500]
                if re.search(r"permission|not allowed|denied|requires approval", detail, re.I):
                    p.permission_denials.append(detail)
            elif kind in ("result", "session.error"):
                terminal = terminal or kind == "result"
                status = data.get("subtype")
                if (kind == "session.error" or data.get("exitCode") not in (None, 0)
                        or data.get("isError") or data.get("is_error") or status == "error"):
                    p.is_error = True
                    p.diag.append(str(data.get("error") or data.get("message") or status or "copilot error")[:500])
        if not terminal:
            p.is_error = True
            p.diag.append("copilot terminal result event is missing")
    return p
