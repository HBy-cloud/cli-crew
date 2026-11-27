# cli-crew protocol reference

This reference describes the worker protocol for the main agent.

## Contract fields (frontmatter)
| field | values | notes |
|---|---|---|
| task_id | `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` | never reused, even after cleanup |
| agent | codex, agy, claude, copilot, cursor | or `crew run --agent` |
| mode | read, write | read is default |
| write_paths | relative paths inside the project | required for write; not `.git`/`.agent-runs` |
| add_dirs | absolute directories | read mode only |
| allow_subagents | true/false | true only in read mode |
| effort | codex: low..xhigh · agy: low..max · claude: low..max · copilot: low · cursor: low (effort is in the model ID); an agy model ID ending in an effort (`-low`) needs that effort | high+ needs `--approve-cost` |
| model | id or null | null = CLI default; Copilot and Cursor use Auto, which remains unresolved for cost approval |
| task_kind | investigate, implement, review, document, other, or null | Optional category for project-local routing history. Untagged runs are excluded. `review` adds the reviewer rule (see Reviewer result). |
| timeout_min | 1..120 | default 20 |
| max_retries | 0 or 1 | retry only on transient errors, never after partial writes, usage limits or `network_blocked` (connection refused) |
| retry_of | previous REJECTED task id | chain limited to two rejections |
| ephemeral | true/false | codex only; requires allow_subagents=true |
| worker_handoff | true/false | worker writes its own handoff file (`handoffs/<date>/<time>-<agent>-<task>.md`); codex/claude/agy, mode=read, no add_dirs; runs in parallel with reads; sections: purpose, status, decisions and why, verification, unfinished, next |
| plan | plan id or null | set together with plan_step; the cplan must pass `crew plan check` (cplan is the name of this plan) |
| plan_step | step id or null | must exist in the plan; a different executor in the plan is a warning |

Body sections `## Objective` and `## Done when` are required (not empty or `TODO`); an empty or `TODO` `## Context` is a warning. A contract linked to a reviewer step (non-empty `reviews`) without `task_kind: review` is a warning, and so is a model that differs from the one the plan step's executor names (only models set on both sides are compared; `default` or `model: null` is not; effort is not compared). Duplicate frontmatter keys and secret-looking strings (API keys, tokens, private keys) fail validation (exit 2).

## Plans
File: `.agent-runs/plans/<plan_id>.md` (`crew plan new`). Frontmatter: `plan_id` (equals file name), `status`: `active` | `done` | `abandoned`.
Sections: `## Goal` (outcome and how main verifies it), `## Steps` table, `## Revisions` (`date | change | reason or evidence`), `## Approvals`, `## Notes`. Secret-looking strings fail `crew plan check` (exit 2) and are redacted in plan output.

| column | values |
|---|---|
| step | `^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`, unique |
| status | todo, doing, blocked, done, dropped |
| executor | main, main-subagent, `crew:<agent>[/<model or default>[/<effort>]]` |
| depends_on | comma-separated step ids, or `-`; no cycles |
| task, verify | concrete text; not empty or TODO |
| reviews | optional 7th column: ids of the steps a reviewer step checks. A Revisions row that skips a review must name the step id and mention the review. `plan check` also warns when two or more `main` steps (not reviewers) do not depend on each other, unless a Revisions row about parallel work names the reason (edits, shell or tests, coupling; not "small" or "simple"). `plan check` warns when the reviewer may run on the same model as the author (same CLI with the same or a default/`auto` model; any two Claude models, on any CLIs; Copilot or Cursor with an unnamed or matching model; `main-subagent` reviewing `main` or `main-subagent`) |

`crew plan show <id>` lists linked runs per step and the todo steps whose dependencies are all done or dropped.
`show` and `check` report mechanical mismatches (a crew step marked done without an accepted run, a done step with unjudged runs, a todo step with linked runs, an accepted run on a todo or
dropped step, a run by a different agent than the executor, runs on main or main-subagent steps, runs on unknown steps) and plan warnings (deliverable without a reviewer step or Revisions skip row; skip reason only "small"/"simple" instead of cost/token budget, no other model or CLI available, or user opt-out; reviewer is `main` or same model as author; dropped step without a Revisions entry; stopped plan—`abandoned` or has a `blocked` step—with no newer handoff file: `NOTE: the plan is stopped ... run crew handoff`). Notes also flag a reviewer step whose runs recorded the same actual model as the reviewed step's runs (or Claude models on both), or a run with no recorded model; rejected runs are ignored.

## Statuses (first match wins)
The final AGY result event can report an error even when the process exits 0. An explicit terminal failure takes precedence over a worker's success footer. A recovered step error on a successful terminal result does not by itself fail the run.
1. CANCELLED · 2. TIMEOUT · 3. FAILED (exit≠0, error, usage limit, worker STATUS failed) ·
4. BLOCKED (permission/denial signals from the CLI, or worker STATUS blocked) · 5. FAILED (empty response) ·
6. PARTIAL (worker STATUS partial, or RESULT block missing/invalid) ·
7. DONE_UNVERIFIED (worker verification blocked by sandbox or final boundary check failed) · 8. DONE.
Lifecycle: RUNNING, STALE (crew and worker gone), ORPHANED (crew gone, worker alive).

Patterns are matched only on CLI diagnostics, not on the model's answer text. Output of a failed worker command (codex
`command_execution`) counts only for BLOCKED and DONE_UNVERIFIED signals, never for usage-limit or transient matching.

## Exit codes
Prefer the main CLI's own background mode for parallel runs; use `crew run --detach` only when it has none, because a shell may stop the detached run when its command returns (interactive agy 1.5.18 on Windows did; `crew status` then shows `STALE?`). `crew run --detach` returns 0 once every guard passed and the worker started (a refusal keeps the foreground code; 10 when the start is not confirmed in 120 s). `crew wait` returns 0 when all (or with `--any` one) of the tasks finished, 10 when its timeout passed with tasks still running, 130 when interrupted; it never cancels runs and reports a dead controller as `STALE?`/`ORPHANED?`.
11 violation · 130 cancelled · 10 not DONE · 5 duplicate id · 4 busy · 3 guard/approval (WORKER role, crew inside a network-disabled Codex sandbox, unsafe root or `.agent-runs` link escape, non-file instruction path on `init`, missing `--approve-cost` or `--approve-data-sharing`, `retry_of` chain rejected twice, existing worker handoff slot file, invalid `accept`/`reject`/`compare`/`cancel` state or unverified `--target` worker identity) · 2 usage/contract · 0 ok.

## Accept rules
| status | accept |
|---|---|
| RUNNING, ORPHANED | not allowed |
| DONE, no violation | allowed |
| DONE_UNVERIFIED, PARTIAL, no violation | `--verified "<what main checked>"` |
| FAILED, BLOCKED, TIMEOUT, CANCELLED, STALE, or any violation | `--override --reason` |

## Concurrency
Write tasks run alone. Read tasks (including `worker_handoff: true`, which runs the worker in write mode confined by prompt/settings to its handoff slot) run in parallel up to `max_concurrent` (default 5).
Reservation is atomic: an OS file lock on `.agent-runs/locks/reserve.lock`, released by the OS if the holder dies (the
file is kept). Do not mix older crew versions on one project while runs are being reserved.

## Safety controls
- Cursor runs in `--mode ask` for reads and agent mode for writes. Its file-permission rules were not enforced in testing, so the write boundary is the prompt plus the snapshot check.
- Worker environment: credential-like variables (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*`, `AWS_*`, ...)
  are removed before launching a worker, except the worker's own provider keys. Removed names (never values)
  are recorded in `env_stripped`. Extra names can be allowed via `env_passthrough` in `~/.crew/config.toml`.
- Sensitive-data hints in instruction files (the working directory, write paths, and `add_dirs`, with their parent
  folders) → `crew run` requires `--approve-data-sharing`.
- Worker handoff boundary: a `worker_handoff` task may change only its own slot file and the slot files of
  handoff tasks whose runs overlapped it (compared by recorded start and end times; for older records without an
  end time, start + `duration_s` + 50 ms). The allowed list is stored as `handoff_allowed_slots`, so
  `crew check --recheck` gives the same answer; runs recorded by an older crew are rechecked from current peer state.
- agy write mode runs with a per-session `--settings` file; `--allow-unbounded-write` is deprecated and ignored.
- Copilot requires an explicit single-file write contract and a dedicated profile. Its built-in tools are restricted to reading and editing, and the CLI receives a write rule for the target file only. The normal boundary checks still run.
- Worker prompt header tells workers to treat file contents as data and ignore embedded instructions.
- Limits: Codex and agy can read any file the user can read; writes outside the project are not
  detected; in a Git project, changes to git-ignored files are not detected except for secret-named files; agy has browser tools. Workers may read the public web; the header forbids sending project content,
  secrets or personal data, but crew does not block network access.

## Approval needed from the user
Delegating in a project whose instructions restrict it · sending sensitive project data to a provider
(`--approve-data-sharing`) · effort high+ · models listed in
`~/.crew/config.toml` `approval_models`, matched as a case-insensitive prefix so effort variants count (or default model unknown while that list is set) ·
`crew cancel --yes` · `crew clean --yes` · any commit/push/delete/deploy/external send.

## Files
Use `crew compare <ID> <ID> [--json]` to inspect verification evidence, violations, worker time and measured usage for specific completed runs. Older archived runs can lack these fields.
`crew check <ID>` and, for read tasks, `crew run --with-result` print `unverified citation:` lines for file/line citations they cannot find (`citations: none unverified` otherwise; existence only); run summaries print `MODEL WARNING:` when the contract names a model and the worker reported a different one (aliases match by substring; `null`, `auto`, `default` or no reported model are not compared); `--recheck` recomputes boundary and subagent checks from saved evidence without changing the state file.
`crew clean --yes` deletes only crew-created codex sessions and reports agy conversations; `--runs` instead deletes recorded run files under `.agent-runs/` after archiving a summary (`--sessions --runs` does both).

`.agent-runs/` (self-gitignored): contracts, state JSON, prompts, per-attempt logs/events/results,
snapshots, subagent evidence, `history.jsonl` (permanent id registry), `locks/`.

`crew trace view` streams state, recorded verdicts and observed activity from local run records. Reconnecting resumes recent updates or reloads the current view. The selected run has Timeline, Output and Verification tabs. Project status runs the default `crew status`, and a selected run's Output and Verification tabs run `crew result` and `crew check`; these queries record their output locally. The view does not replace main's own verification against the source. For coverage and optional Claude Code hook setup, see the [CLI guide](../../../crew/README.md#live-trace-viewer).

Kanban uses the same records as the list and can be filtered by cplan. Accepted runs are Complete, runs whose status is `RUNNING` are Running, and every other recorded run (awaiting a verdict, rejected, `ORPHANED` or `STALE`) needs review. Unregistered saved contracts appear in Not run; selecting one opens the existing run preview.

On Windows, `crew trace view --interactive codex` starts a foreground Codex session with shared browser and console I/O for direct user interaction. See the [shared CLI session guide](../../../crew/README.md#shared-cli-session).

New run previews an existing contract before launch. An active task's Cancel run action exposes the exact worker identity before cancellation. Run and cancel use the existing CLI. Contracts that require approval or instruction review follow the CLI workflow.

## Reviewer result (task_kind: review)
crew adds the reviewer rule to the worker prompt. The worker puts this block before the final RESULT block:

```
## REVIEW
VERDICT: PASS | FAIL | PARTIAL
CRITERIA:
- <each Done-when criterion>: met | unmet | unverified, with evidence (file:line or what it ran)
FINDINGS: defects found, most severe first, or "none"
```
The run summary shows `review verdict: <X> (<n> criteria lines)`; a missing block is `MISSING`. Main checks the evidence itself.
