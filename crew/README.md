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

### Updating an editable install

An editable installation loads Crew from this checkout, including local code changes. Pull the latest changes and start a new AI CLI session to reload the skill. To add shared Codex sessions, run `uv tool install --reinstall -e "./crew[terminal]"` from the repository root.

If Windows reports an access error during reinstall and `crew` no longer starts, you can repair the existing uv environment if its Python executable is still present. From the repository root:

```powershell
$crewPython = Join-Path (uv tool dir) 'cli-crew/Scripts/python.exe'
uv pip install --python $crewPython -e "./crew[terminal]"
crew --version
crew trace view --help
```

Use `-e ./crew` in the repair command for the base installation. The `terminal` extra installs `pywinpty`.

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
crew accept T-01 --verified "<what you checked>"  # or: crew reject T-01 --reason "..."
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
| `crew trace view [--port PORT] [--interactive codex]` | Open Crew Runs on loopback. The optional Windows mode shares a new Codex CLI session. |
| `crew handoff --agent NAME --task SLUG --status STATUS [--since ISO_OR_LAST] [--tasks ID,ID] [--lang LANG]` | Draft a handoff for the next session. Use en or ko; status is Done, "In progress", or Blocked. |
| `crew clean [--sessions] [--runs] [--older-than DAYS] [--yes] [--json]` | Preview cleanup of eligible records. The age threshold defaults to 7 days; --yes applies the plan. `--runs` keeps `history.jsonl`, `locks/` and `contracts/<task_id>.md`. |

Optional config `~/.crew/config.toml`:
```toml
max_concurrent = 3
default_timeout_min = 20
default_effort = "low"
approval_models = []        # model ids that need --approve-cost
agy_prompt_mode = "file"    # or "inline"
env_passthrough = []        # extra credential-like env var names to pass to workers
```

`crew detect` warns when an API key variable is set (`OPENAI_API_KEY`, `CODEX_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`), because a worker may then bill the API instead of your subscription. If `config.toml`, or the state file of the task a command works on, cannot be parsed, `crew` exits with code 2 and prints the file path instead of a traceback; fix or move that file. Listing commands such as `crew status` skip unreadable state files.

## Live trace viewer

Run `crew trace view` in the project, then open the printed `http://127.0.0.1:…/` URL. Use `--port 8765` for a fixed port. Open tabs share one project observer. The local server streams updates through Server-Sent Events (SSE) and replays recent updates when a connection resumes. If that replay is unavailable, the view reloads the current state and timeline from the records still in `.agent-runs/`. Watch worker status, elapsed time, retries and cancellation, the review verdict, and the main agent's decision.

Codex and agy subagents appear live when their structured output exposes child IDs. Saved subagent evidence can also identify children after a run. Claude workers emit terminal JSON in their current launch mode, so their subagent coverage depends on the final evidence. Copilot exposes live worker activity and may report subagent usage without child IDs. Event times for worker activity show when `crew` observed the event. Missing child states are shown as unknown.

Select a run in the left list. **Timeline** shows the run, attempts, and subagent activity. **Output** offers **Load result** and **Verification** offers **Run check** alongside the recorded review and main-agent verdicts. These buttons use the selected task ID and default CLI options. **Project status** runs `crew status` in the displayed project. Start the viewer from another project to query it. **Command history** shows the latest 20 commands, their standard output, standard error, and exit codes. Each command saves up to 32,768 characters in a local `.viewer-command` record under `.agent-runs/`.

Choose **New run**, select an existing contract, and use **Preview run** to check the task, worker and mode before confirming. Use the CLI for contracts that require approval or review of delegation instructions. The existing CLI reservation, checks and results apply. The run controller keeps running when the viewer closes. In an active run's detail pane, choose **Cancel run** to see the worker PID and creation time. Confirmation passes that identity to `crew cancel`, so a replacement worker requires a fresh preview.

To include a Claude Code **main** session, configure a project-local command hook for `SubagentStart` and `SubagentStop`. For example, merge these entries into `.claude/settings.local.json` and restart the session if required by Claude Code. Append `--plan P-example` to both commands to group the session with that cplan. The hook records IDs, lifecycle events, and timestamps in `.agent-runs/` and ignores the transcript and message fields.

```json
{
  "hooks": {
    "SubagentStart": [{"hooks": [{"type": "command", "command": "crew trace claude-hook"}]}],
    "SubagentStop": [{"hooks": [{"type": "command", "command": "crew trace claude-hook"}]}]
  }
}
```

### Kanban

Run `crew trace view` from your project, open the printed URL, and choose **Kanban** beside **List**. Use **Plan** to filter by cplan. The board follows live changes to Crew's records, and switching views keeps the selected task and detail tab.

| Column | What appears there |
| --- | --- |
| **Not run** | Saved contracts whose task IDs have never been registered. Select a card to preview the contract, then use **Confirm run** to start it. |
| **Running** | Active runs, including retries. Select a card to see the latest attempt or use **Cancel run** and confirm the worker shown in the preview. |
| **Needs review** | Runs awaiting acceptance, rejected runs, and runs requiring attention, such as orphaned or stale workers. Open **Output** and **Verification** to inspect the result and recorded evidence. |
| **Complete** | Runs the main agent has accepted. An override-accepted failure still shows its original status on the card. |

To record a verdict, use `crew accept <task_id> --verified "<what you checked>"` or `crew reject <task_id> --reason "<reason>"` in the CLI. The board updates when Crew records the verdict. Contracts that require approval or instruction review follow the CLI workflow.

### Shared CLI session

On Windows, install the `terminal` extra and make sure `codex.exe` is on PATH. The extra installs `pywinpty`. The browser assets are packaged with Crew. Run the first command from the Crew checkout and the second from your project in an interactive Windows terminal.

```powershell
uv tool install --reinstall "./crew[terminal]"
crew trace view --port 8765 --interactive codex
```

Open `http://127.0.0.1:8765/` and select **CLI session**, or open the printed session URL directly. Both views display output and accept input for the same Codex process. The local terminal sets the shared dimensions. Closing the browser tab leaves Codex running. Reopening it replays session output before enabling input. Crew buffers output in memory and uses a temporary file for longer sessions, which is removed when the viewer closes. Each launch starts a new session.

## Tests
```
py -3 -m unittest discover -s tests
```
Tests use `tests/fake_worker.py` instead of real CLIs (`CREW_BIN_<AGENT>` override) and create
throwaway git repos under `tests/_work/` (git-ignored).
