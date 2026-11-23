# Worker CLI adapters (reference)

`crew` assembles these commands; you normally do not run them yourself. Verified on Windows 11 with
codex-cli 0.171.0, agy 1.5.14, Claude Code 2.1.330, and GitHub Copilot CLI 1.0.112. Re-verify after CLI upgrades.
These versions are recorded in `VERIFIED_VERSIONS` (crew/src/crew/adapters.py). `crew detect` and run summaries
print a VERSION WARNING when an installed CLI differs. Update both places together after re-verifying.

| agent | read | write | prompt | evidence |
|---|---|---|---|---|
| codex | `codex exec -s read-only -C <root> [--add-dir D ...] --skip-git-repo-check --json [--ephemeral] -o <result> -c model_reasoning_effort="<E>" [-m M] -` | `-s workspace-write` (no `--add-dir`) | stdin | `-o` file, JSONL (`thread.started` → thread_id, error events), `~/.codex/sessions` rollouts, stderr |
| agy | `agy --mode plan --effort <E> [--model M] --output-format stream-json --print-timeout <T>m [--settings <file>] [--add-dir D ...] --print=<instruction>` | `--mode accept-edits` with a per-session `--settings` file (no `--add-dir`) | short instruction pointing at `.agent-runs/<id>.prompt.md` | stream-json (`init.model`, `step_update`, `result`, `conversation_id`), stderr |
| claude | `claude -p --restricted --permission-mode plan --permission-prompts none --output-format json --no-session-persistence --effort <E> [--model M] [--add-dir D ...]` | `--permission-mode acceptEdits` (no `--add-dir`) | stdin | JSON (`result`, `is_error`, `permission_denials`, `subagent_stats`, `total_cost_usd`, `modelUsage`, `session_id`) |
| copilot | — | `--available-tools view apply_patch --allow-tool=write(<absolute file>) --max-ai-credits=30 --output-format=json` with the profile below | `-p <contract>` | JSONL session, assistant and tool events, stderr |

## GitHub Copilot CLI setup

Use a dedicated authenticated profile outside the target project's directory and the default `.copilot` directory. Set `COPILOT_HOME` in the terminal that runs `crew`. For example, in PowerShell:

```powershell
$env:COPILOT_HOME = Join-Path $env:LOCALAPPDATA 'crew-copilot-profile'
copilot login
copilot config disableAllHooks true
crew contract new T-EDIT --agent copilot --kind implement --mode write --write src/example.py
```

Use this profile for crew only. `crew run` rejects profiles containing `permissions-config.json`, `mcp-config.json` or `providers.json`, and projects with settings under `.github/copilot` or `.claude` that could override hook handling. The worker uses the profile's saved OAuth sign-in.

The adapter uses prompt mode with `--mode interactive` and `--no-ask-user`. It disables built-in MCP, custom instructions, automatic updates, session sync, temporary-directory access, shell and memory tools. Copilot's file tools can read project files, with deny rules for crew's secret-name patterns. A write rule scopes `apply_patch` to the one existing file. The post-run check reports any deletion or rename as a boundary violation.

Copilot chooses the model with Auto unless you name one. The 30 AI-credit session cap is soft and uses the installed CLI's minimum. The prompt is a process argument and should contain only contract context. The profile retains local sessions outside `crew clean` management.

## Known behaviour
- Web (verified 2026-11-21, one run each, under the old no-network header): codex was BLOCKED by that header; agy ran `search_web` and claude ran `WebSearch`,
  both with summaries only and no page fetch, so postings and deadlines stayed unverified. The header now allows reading the public web (2026-11-21
  user decision) but forbids sending project content; real fetch ability per CLI is not re-verified. Do not rely on workers for page contents yet.
- Main runs shell commands, tests, and builds. The Codex adapter selects a filesystem sandbox and the worker
  prompt assigns file tools. A Windows smoke test observed `CreateProcessAsUserW` denied, which is specific
  to that environment. agy print mode auto-denied commands in testing, and Claude's `--restricted` mode omits shell access.
- Exit code 0 does not mean success. agy prints `jetski: no output produced ... auto-denied` with exit 0.
- `--add-dir` grants write access in codex/claude write modes, so crew forbids it in write mode.
- agy writes outside cwd under the default config. Crew passes per-session `--settings` with `allowNonWorkspaceAccess: false` and `write_file` rules for the contract's write paths or handoff file. In 1.5.14, outside writes and overwrites of unlisted files were auto-denied. New files inside the workspace can be created. In Git projects the snapshot skips ignored files except those with secret-like names.
- Codex, agy, and Claude can spawn built-in subagents non-interactively; codex cannot disable it via flags.
  crew detects use: codex child rollouts (`parent_thread_id`), agy `invoke_subagent` steps,
  claude `subagent_stats.spawned`.
- codex fails to write when its cwd is in the OS temp folder; crew refuses temp roots.
- Reading: codex read-only and agy plan can read files anywhere the user can; claude `--restricted` is confined
  to the project and `--add-dir`. "Do not open secret files" is only an instruction for codex/agy.
- claude workers use the user's default Claude model (observed claude-opus-5-5, ~0.08 USD for a tiny read).
- Timeouts: agy `--print-timeout` (exit 3); codex/claude are killed by crew as a process tree.
- Session logs: codex keeps rollouts (`--ephemeral` disables but blocks subagent checks); agy keeps
  conversations (~0.5 MB each, no off switch); claude keeps none with `--no-session-persistence`.
