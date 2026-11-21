# Worker CLI adapters (reference)

`crew` assembles these commands; you normally do not run them yourself. Verified on Windows 11 with
codex-cli 0.171.0, agy 1.5.14, Claude Code 2.1.330 (2026-11-20). Re-verify after CLI upgrades.
These versions are recorded in `VERIFIED_VERSIONS` (crew/src/crew/adapters.py). `crew detect` and run summaries
print a VERSION WARNING when an installed CLI differs. Update both places together after re-verifying.

| agent | read | write | prompt | evidence |
|---|---|---|---|---|
| codex | `codex exec -s read-only -C <root> [--add-dir D] --skip-git-repo-check --json -o <result> -c model_reasoning_effort="<E>" -` | `-s workspace-write` | stdin | `-o` file, JSONL (`thread.started` → thread_id), `~/.codex/sessions` rollouts |
| agy | `agy --mode plan --effort <E> --output-format stream-json --print-timeout <T>m [--add-dir D] --print=<instruction>` | refused by crew | short instruction pointing at `.agent-runs/<id>.prompt.md` | stream-json (`init.model`, `step_update`, `result`) |
| claude | `claude -p --restricted --permission-mode plan --permission-prompts none --output-format json --no-session-persistence --effort <E> [--add-dir D]` | `--permission-mode acceptEdits` | stdin | JSON (`result`, `is_error`, `permission_denials`, `subagent_stats`, `total_cost_usd`) |

## Known behaviour
- Web (verified 2026-11-21, one run each): codex is BLOCKED because crew's worker header forbids network requests; agy ran `search_web` and claude ran `WebSearch`,
  both with summaries only and no page fetch, so postings and deadlines stayed unverified. Do not hand them web collection that needs page contents.
- Safe-mode workers cannot run shell commands (codex sandbox `CreateProcessAsUserW` denied; agy print mode
  auto-denies commands; claude `--restricted` has no shell). Main runs tests/builds.
- Exit code 0 does not mean success. agy prints `jetski: no output produced ... auto-denied` with exit 0.
- `--add-dir` grants write access in codex/claude write modes, so crew forbids it in write mode.
- agy writes outside cwd under the default config. Crew passes a per-session `--settings` file (`allowNonWorkspaceAccess: false`, `write_file` allow rules only for the contract's write paths or handoff file). Verified on 1.5.14: outside writes and overwrites of unlisted files are auto-denied; creating new files inside the workspace is not prevented (the snapshot check reports it).
- All three can spawn built-in subagents non-interactively; codex cannot disable it via flags.
  crew detects use: codex child rollouts (`parent_thread_id`), agy `invoke_subagent` steps,
  claude `subagent_stats.spawned`.
- codex fails to write when its cwd is in the OS temp folder; crew refuses temp roots.
- Reading: codex read-only and agy plan can read files anywhere the user can; claude `--restricted` is confined
  to the project and `--add-dir`. "Do not open secret files" is only an instruction for codex/agy.
- claude workers use the user's default Claude model (observed claude-opus-5-5, ~0.08 USD for a tiny read).
- Timeouts: agy `--print-timeout` (exit 3); codex/claude are killed by crew as a process tree.
- Session logs: codex keeps rollouts (`--ephemeral` disables but blocks subagent checks); agy keeps
  conversations (~0.5 MB each, no off switch); claude keeps none with `--no-session-persistence`.
