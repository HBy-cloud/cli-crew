# cli-crew protocol reference

Source of truth: `docs/SPEC.md` (v1.0) in the cli-crew repository. This is a condensed reference for the main agent.

## Contract fields (frontmatter)
| field | values | notes |
|---|---|---|
| task_id | `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` | never reused, even after cleanup |
| agent | codex, agy, claude | or `crew run --agent` |
| mode | read, write | read is default |
| write_paths | relative paths inside the project | required for write; not `.git`/`.agent-runs` |
| add_dirs | absolute directories | read mode only |
| allow_subagents | true/false | true only in read mode |
| effort | codex: low..xhigh · agy: low..max · claude: low..max | high+ needs `--approve-cost` |
| model | id or null | null = CLI default |
| task_kind | investigate, implement, review, document, other, or null | Optional category for project-local routing history. Untagged runs are excluded. |
| timeout_min | 1..120 | default 20 |
| max_retries | 0 or 1 | retry only on transient errors, never after partial writes or usage limits |
| retry_of | previous REJECTED task id | chain limited to two rejections |
| ephemeral | true/false | codex only; requires allow_subagents=true |
| worker_handoff | true/false | worker writes its own handoff file (`handoffs/<date>/<time>-<agent>-<task>.md`); codex/claude, mode=read, no add_dirs; runs in parallel with reads |
| plan | plan id or null | set together with plan_step; the plan must pass `crew plan check` |
| plan_step | step id or null | must exist in the plan; a different executor in the plan is a warning |

Body sections `## Objective` and `## Done when` are required.

## Plans
File: `.agent-runs/plans/<plan_id>.md` (`crew plan new`). Frontmatter `plan_id` (equals file name), `status`: active | done | abandoned.
Sections: `## Goal` (outcome and how main verifies it), `## Steps` table, `## Approvals`, `## Notes`.

| column | values |
|---|---|
| step | `^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`, unique |
| status | todo, doing, done, dropped |
| executor | main, main-subagent, `crew:<agent>[/<model or default>[/<effort>]]` |
| depends_on | comma-separated step ids, or `-`; no cycles |
| task, verify | concrete text; not empty or TODO |

`crew plan show <id>` lists linked runs per step and the todo steps whose dependencies are all done or dropped.
It notes mechanical mismatches: a crew step marked done without an accepted run, an accepted run on a todo or
dropped step, a run by a different agent than the executor, runs on main or main-subagent steps, and runs on unknown steps.

## Statuses (first match wins)
The final AGY result event can report an error even when the process exits 0. An explicit terminal failure takes precedence over a worker's success footer. A recovered step error on a successful terminal result does not by itself fail the run.
1. CANCELLED · 2. TIMEOUT · 3. FAILED (exit≠0, error, usage limit, worker STATUS failed) ·
4. BLOCKED (permission/denial signals from the CLI) · 5. FAILED (empty response) ·
6. PARTIAL (worker STATUS partial, or RESULT block missing/invalid) ·
7. DONE_UNVERIFIED (worker verification blocked by sandbox or final boundary check failed) · 8. DONE.
Lifecycle: RUNNING, STALE (crew and worker gone), ORPHANED (crew gone, worker alive).

Patterns are matched only on CLI diagnostics, not on the model's answer text.

## Exit codes
11 violation · 130 cancelled · 10 not DONE · 5 duplicate id · 4 busy · 3 guard/approval · 2 usage/contract · 0 ok.

## Accept rules
| status | accept |
|---|---|
| RUNNING, ORPHANED | not allowed |
| DONE, no violation | allowed |
| DONE_UNVERIFIED, PARTIAL, no violation | `--verified "<what main checked>"` |
| FAILED, BLOCKED, TIMEOUT, CANCELLED, STALE, or any violation | `--override --reason` |

## Concurrency
Write tasks run alone. Read tasks run in parallel up to `max_concurrent` (default 3).
Reservation is atomic (`.agent-runs/locks/reserve.lock`).

## Safety controls
- Worker environment: credential-like variables (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*`, `AWS_*`, ...)
  are removed before launching a worker, except the worker's own provider keys. Removed names (never values)
  are recorded in `env_stripped`. Extra names can be allowed via `env_passthrough` in `~/.crew/config.toml`.
- Sensitive-data hints in instruction files → `crew run` requires `--approve-data-sharing`.
- agy write mode is refused even with the legacy flag.
- Worker prompt header tells workers to treat file contents as data and ignore embedded instructions.
- Limits: workers other than claude can read any file the user can read; writes outside the project are not
  detected; agy has browser tools (network access not blocked by crew).

## Approval needed from the user
Delegating in a project whose instructions restrict it · sending sensitive project data to a provider
(`--approve-data-sharing`) · effort high+ · models listed in
`~/.crew/config.toml` `approval_models` (or default model unknown while that list is set) ·
`crew cancel --yes` · `crew clean --yes` · any commit/push/delete/deploy/external send.

## Files
Use `crew compare <ID> <ID> [--json]` to inspect verification evidence, violations, worker time and measured usage for specific completed runs. Older archived runs can lack these fields.

`.agent-runs/` (self-gitignored): contracts, state JSON, prompts, per-attempt logs/events/results,
snapshots, subagent evidence, `history.jsonl` (permanent id registry), `locks/`.
