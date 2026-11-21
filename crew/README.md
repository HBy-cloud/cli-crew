# crew

**English** · [한국어](README.ko.md)

Python CLI behind the `cli-crew` skill. It lets a main AI CLI delegate bounded tasks to worker CLIs
(`codex`, `agy`, `claude`, and `copilot` for single-file edits) in safe, non-interactive modes and records verifiable results.
[Worker protocol](../skill/cli-crew/references/protocol.md) · [Skill instructions](../skill/cli-crew/SKILL.md) · [Windows installation](../README.md)

## Install (development)
```
uv tool install -e .
crew --version
```

## Initialize project guidance

Run `crew init` from the target project. It creates a starter `AGENTS.md` only when the root has no supported instruction file. When setup is requested through the skill, main adds verified project details.

## Typical flow (run inside a project)
```
crew detect --paths src/
crew routing --kind review --mode read
crew contract new T-01 --agent codex --kind review --mode read
# edit .agent-runs/contracts/T-01.md
crew run --contract .agent-runs/contracts/T-01.md --with-result
# inspect status, boundaries and RESULT; verify the evidence before acceptance
crew accept T-01            # or: crew reject T-01 --reason "..."
crew handoff --agent claude --task my-task --status Done
```

agy write tasks run with a per-session `--settings` file that confines writes to the project; new files inside it are reported by the boundary check (in a Git project, git-ignored files are not checked except secret-named ones).

For a small edit, explicitly select `--agent copilot --mode write --write <file>`. The file must already exist and the contract must use `effort: low`. [Dedicated Copilot profile setup](../skill/cli-crew/references/adapters.md#github-copilot-cli-setup)

Safety flags for `crew run` (ask the user first): `--approve-data-sharing` (instructions mention personal/sensitive
data), `--approve-cost` (high effort / approval models).
Credential-like environment variables are removed from the worker environment (except the worker's own provider key).

## Command reference

Run commands from the target project. Replace uppercase placeholders with your values. Use `crew --help`, `crew contract --help`, or `crew run --help` to inspect each level. `crew --version` prints the installed version.

| Usage | Purpose |
| --- | --- |
| `crew init` | Create missing project instructions; reuse existing guides. |
| `crew detect [--paths PATH ...] [--json]` | List available workers, applicable instructions and active runs. |
| `crew plan new ID` | Create a plan template under `.agent-runs/plans/` for a multi-step goal. |
| `crew plan check ID [--json]` | Validate a plan: step ids, executors, dependencies, verification entries. |
| `crew plan show [ID] [--json]` | List plans, or show one plan with linked runs, ready steps and status mismatches. |
| `crew contract new ID [--agent CLI] [--mode MODE] [--kind KIND] [--write PATH ...] [--plan ID --step STEP]` | Create the contract template, optionally linked to a plan step. Fill in the objective, context and completion criteria before running. |
| `crew routing --kind KIND --mode MODE [--json]` | Show up to ten recent completed worker runs with matching mode and kind (investigate, implement, review, document, other). |
| `crew compare ID... [--json]` | Compare verification evidence, violations, worker time and provider-specific usage for completed runs. |
| `crew contract check FILE [--agent CLI] [--effort LEVEL] [--model MODEL] [--json]` | Validate a contract without starting a worker. |
| `crew run --contract FILE [--agent CLI] [--effort LEVEL] [--model MODEL] [--timeout MIN] [--with-result] [--json] [--approve-cost] [--approve-data-sharing]` | Start the worker and wait for its result. Timeout is in minutes. The legacy --allow-unbounded-write flag is deprecated and ignored. |
| `crew status [--all] [--json]` | Show current and recent task states; use --all for older tasks. |
| `crew result ID [--raw]` | Read the saved worker result; --raw shows the full response. |
| `crew check ID [--recheck] [--json]` | Inspect the run checks. Recheck uses saved evidence. |
| `crew accept ID [--verified TEXT] [--override --reason TEXT]` | Record acceptance. Supply actual verification for a partial or unverified result. |
| `crew reject ID --reason TEXT` | Reject the result and record why. |
| `crew cancel ID [--target IDENTITY] [--yes]` | Preview the worker tree. After approval, use the printed confirmation command to stop that worker. |
| `crew handoff --agent NAME --task SLUG --status STATUS [--since ISO_OR_LAST] [--tasks ID,ID] [--lang LANG]` | Draft a handoff for the next session. Use en or ko; status is Done, "In progress", or Blocked. |
| `crew clean [--sessions] [--runs] [--older-than DAYS] [--yes] [--json]` | Preview cleanup of eligible records. The age threshold defaults to 7 days; --yes applies the plan. |

Optional config `~/.crew/config.toml`:
```toml
max_concurrent = 3
default_timeout_min = 20
default_effort = "low"
approval_models = []        # model ids that need --approve-cost
agy_prompt_mode = "file"    # or "inline"
env_passthrough = []        # extra credential-like env var names to pass to workers
```

## Tests
```
py -3 -m unittest discover -s tests
```
Tests use `tests/fake_worker.py` instead of real CLIs (`CREW_BIN_<AGENT>` override) and create
throwaway git repos under `tests/_work/` (git-ignored).
