# cli-crew protocol reference

This reference describes the worker protocol for the main agent. Maintainer checkouts also have the detailed specification.

## Contract fields (frontmatter)
| field | values | notes |
|---|---|---|
| task_id | `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` | never reused, even after cleanup |
| agent | codex, agy, claude, copilot | or `crew run --agent` |
| mode | read, write | read is default |
| write_paths | relative paths inside the project | required for write; not `.git`/`.agent-runs` |
| add_dirs | absolute directories | read mode only |
| allow_subagents | true/false | true only in read mode |
| effort | codex: low..xhigh · agy: low..max · claude: low..max · copilot: low | high+ needs `--approve-cost` |
| model | id or null | null = CLI default; Copilot uses Auto and remains unresolved for cost approval |
| task_kind | investigate, implement, review, document, other, or null | Optional category for project-local routing history. Untagged runs are excluded. `review` adds the reviewer rule (see Reviewer result). |
| timeout_min | 1..120 | default 20 |
| max_retries | 0 or 1 | retry only on transient errors, never after partial writes or usage limits |
| retry_of | previous REJECTED task id | chain limited to two rejections |
| ephemeral | true/false | codex only; requires allow_subagents=true |
| worker_handoff | true/false | worker writes its own handoff file (`handoffs/<date>/<time>-<agent>-<task>.md`); codex/claude/agy, mode=read, no add_dirs; runs in parallel with reads |
| plan | plan id or null | set together with plan_step; the cplan must pass `crew plan check` (cplan is the name of this plan) |
| plan_step | step id or null | must exist in the plan; a different executor in the plan is a warning |

Body sections `## Objective` and `## Done when` are required (not empty or `TODO`). Duplicate frontmatter keys and secret-looking strings (API keys, tokens, private keys) fail validation (exit 2).

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
| reviews | optional 7th column: ids of the steps a reviewer step checks (D-28). `plan check` warns when the reviewer may run on the same model as the author (same CLI with the same or a default model; `main-subagent` reviewing `main` or `main-subagent`) |

`crew plan show <id>` lists linked runs per step and the todo steps whose dependencies are all done or dropped.
`show` and `check` report mechanical mismatches (a crew step marked done without an accepted run, an accepted run on a todo or
dropped step, a run by a different agent than the executor, runs on main or main-subagent steps, runs on unknown steps) and plan warnings (deliverable without a reviewer step or Revisions skip row; skip reason only "small"/"simple" instead of cost/token budget, no other model or CLI available, or user opt-out; reviewer is `main` or same model as author; dropped step without a Revisions entry; stopped plan—`abandoned` or has a `blocked` step—with no newer handoff file: `NOTE: the plan is stopped ... run crew handoff`).

## Statuses (first match wins)
The final AGY result event can report an error even when the process exits 0. An explicit terminal failure takes precedence over a worker's success footer. A recovered step error on a successful terminal result does not by itself fail the run.
1. CANCELLED · 2. TIMEOUT · 3. FAILED (exit≠0, error, usage limit, worker STATUS failed) ·
4. BLOCKED (permission/denial signals from the CLI) · 5. FAILED (empty response) ·
6. PARTIAL (worker STATUS partial, or RESULT block missing/invalid) ·
7. DONE_UNVERIFIED (worker verification blocked by sandbox or final boundary check failed) · 8. DONE.
Lifecycle: RUNNING, STALE (crew and worker gone), ORPHANED (crew gone, worker alive).

Patterns are matched only on CLI diagnostics, not on the model's answer text.

## Exit codes
11 violation · 130 cancelled · 10 not DONE · 5 duplicate id · 4 busy · 3 guard/approval (WORKER role, unsafe root or `.agent-runs` link escape, non-file instruction path on `init`, missing `--approve-cost` or `--approve-data-sharing`, `retry_of` chain rejected twice, existing worker handoff slot file, invalid `accept`/`reject`/`compare`/`cancel` state or unverified `--target` worker identity) · 2 usage/contract · 0 ok.

## Accept rules
| status | accept |
|---|---|
| RUNNING, ORPHANED | not allowed |
| DONE, no violation | allowed |
| DONE_UNVERIFIED, PARTIAL, no violation | `--verified "<what main checked>"` |
| FAILED, BLOCKED, TIMEOUT, CANCELLED, STALE, or any violation | `--override --reason` |

## Concurrency
Write tasks run alone. Read tasks (including `worker_handoff: true`, which runs the worker in write mode confined by prompt/settings to its handoff slot) run in parallel up to `max_concurrent` (default 3).
Reservation is atomic (`.agent-runs/locks/reserve.lock`).

## Safety controls
- Worker environment: credential-like variables (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*`, `AWS_*`, ...)
  are removed before launching a worker, except the worker's own provider keys. Removed names (never values)
  are recorded in `env_stripped`. Extra names can be allowed via `env_passthrough` in `~/.crew/config.toml`.
- Sensitive-data hints in instruction files → `crew run` requires `--approve-data-sharing`.
- agy write mode runs with a per-session `--settings` file; `--allow-unbounded-write` is deprecated and ignored.
- Copilot requires an explicit single-file write contract and a dedicated profile. Its built-in tools are restricted to reading and editing, and the CLI receives a write rule for the target file only. The normal boundary checks still run.
- Worker prompt header tells workers to treat file contents as data and ignore embedded instructions.
- Limits: Codex and agy can read any file the user can read; writes outside the project are not
  detected; agy has browser tools. Workers may read the public web; the header forbids sending project content,
  secrets or personal data, but crew does not block network access.

## Approval needed from the user
Delegating in a project whose instructions restrict it · sending sensitive project data to a provider
(`--approve-data-sharing`) · effort high+ · models listed in
`~/.crew/config.toml` `approval_models` (or default model unknown while that list is set) ·
`crew cancel --yes` · `crew clean --yes` · any commit/push/delete/deploy/external send.

## Files
Use `crew compare <ID> <ID> [--json]` to inspect verification evidence, violations, worker time and measured usage for specific completed runs. Older archived runs can lack these fields.

`.agent-runs/` (self-gitignored): contracts, state JSON, prompts, per-attempt logs/events/results,
snapshots, subagent evidence, `history.jsonl` (permanent id registry), `locks/`.

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
