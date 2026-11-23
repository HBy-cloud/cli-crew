# cli-crew

[![tests](https://github.com/HBy-cloud/cli-crew/actions/workflows/test.yml/badge.svg)](https://github.com/HBy-cloud/cli-crew/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab.svg)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4.svg)
![Version 0.1.0](https://img.shields.io/badge/version-0.1.0-informational.svg)

**English** · [한국어](README.ko.md)

**Delegate scoped tasks between AI coding CLIs, verify the results, and keep the evidence in your project.**

cli-crew lets the AI CLI you are already using (the *main* agent) hand bounded work to Codex, Claude Code, and Antigravity. GitHub Copilot CLI is also available as an opt-in worker for small single-file edits. A skill tells the main agent when delegation is worth it and how to verify the result. The Python CLI, `crew`, launches workers in safe modes, enforces timeouts and approvals, checks file boundaries, and records every run. The main agent always makes the final call.

![The main agent defines a contract, a worker runs it, and the main agent verifies the result.](docs/assets/workflow.svg)

[Features](#features) · [Installation](#installation) · [Quick start](#quick-start) · [Commands](#commands) · [Inspecting a run](#inspecting-a-run) · [Workers](#supported-workers) · [Safety](#safety-model-and-limitations) · [Compared](#how-cli-crew-differs-from-other-orchestrators) · [Update](#updating) · [Uninstall](#uninstalling) · [CLI reference](crew/README.md)

## Features

- **Goal-driven delegation.** You state the outcome. The main agent decides what to do itself, what to give its own subagents, what to hand to a worker, and when an independent review from a different model is worth the extra call.
- **Plans for multi-step goals.** Before starting, the main agent writes a plan (a *cplan*) in which each step names its executor (itself, its own built-in subagents, or a crew worker), its dependencies, and how it will be verified. When evidence changes the plan, the change and its reason are recorded. Runs are linked to steps, so progress survives a handoff.
- **Independent review.** Plans call for a reviewer on a different model for each deliverable. If the review is skipped, the main agent records the reason in the plan. `crew` records the reviewer's verdict and the response retains the evidence.
- **Explicit task contracts.** Each delegated task is a Markdown contract with an objective, file paths for context, completion criteria, and, for edits, the exact paths the worker may change.
- **Verification before acceptance.** `crew` classifies runs and checks for detectable out-of-scope changes and subagent use. The main agent records acceptance or rejection and can attach its verification with `--verified`.
- **Guardrails by default.** Read-only mode is the default, writes run one at a time, and credential-like environment variables are stripped from worker processes. Higher-cost runs and delegation when Crew detects sensitive-data hints in project instructions require approval.
- **Local, inspectable records.** Contracts, prompts, logs, results, and verdicts live under `.agent-runs/` in your project. `crew handoff` drafts a handoff note so another CLI can pick up the work.
- **Crew Runs.** See worker progress, saved results, and review verdicts in a local List or Kanban view. On Windows, the optional CLI session shares a new Codex session between your terminal and Crew Runs.

## How it works

1. The main agent reads the project instructions and runs `crew detect` to see which workers are available.
2. For a separable task, it writes a contract (`crew contract new`) and launches it (`crew run`).
3. `crew` runs the worker in a safe mode and enforces the timeout. It classifies the outcome from the CLI's exit status and diagnostics and from the worker's result block.
4. `crew` compares a before/after snapshot of the project against the contract's permitted paths.
5. The main agent checks the claims against the source, runs the tests, and records `crew accept ID --verified "<what it checked>"` or `crew reject ID --reason "<reason>"`.

## Installation

cli-crew is currently verified on **Windows 11** only.

**Requirements**

- Git, PowerShell 7 (`pwsh`), Python 3.12 or later, and [uv](https://docs.astral.sh/uv/)
- At least one installed and signed-in worker CLI. Use [Codex CLI](https://github.com/openai/codex), Claude Code, Antigravity (`agy`), or a dedicated GitHub Copilot CLI profile for single-file edits.

**Install**

```powershell
git clone https://github.com/HBy-cloud/cli-crew.git
cd cli-crew
uv tool install ./crew
$env:Path = "$(uv tool dir --bin);$env:Path"
pwsh -File scripts/install-skill.ps1
crew --version
```

The base installation has no third-party Python runtime dependencies. To enable the shared Codex session on Windows, use `uv tool install "./crew[terminal]"` in place of `uv tool install ./crew`. The browser assets ship with Crew. There is no separate npm or Docker installation. [Shared CLI session setup](crew/README.md#shared-cli-session)

The installer registers the skill for Claude Code, Codex, and agy. To choose clients, pass `-Agents`, for example `pwsh -File scripts/install-skill.ps1 -Agents claude,codex`. Claude Code and Codex use directory junctions to the clone, and agy registers the skill directory in `~/.gemini/config/skills.json`, so keep the clone where it is. If a new terminal cannot find `crew`, run `uv tool update-shell` and reopen the terminal.

## Quick start

Open your **target project** in a new AI CLI session and describe the outcome you want:

> The CSV export drops the last row. Reproduce the bug, fix it, and add a regression test.

The main agent may fix a small bug directly. If an independent check helps, it can give a read-only worker one focused question, such as how the exporter handles a missing final newline, while it owns the fix and the regression test itself. The worker gets a contract with the question, the relevant paths, and the completion criteria. It does not get the whole conversation.

### Using the skill

**Invoke it by name when you want cli-crew.** The skill loads on its own only when a request matches its description, for example "get an independent review from another model". In our tests a plain goal such as "fix this bug and add a test" never loaded it (0 of 6), while requests that asked for another model's review did (4 of 4).

| Main CLI | How to invoke | Status |
| --- | --- | --- |
| Claude Code | `/cli-crew <your goal>` | Verified in interactive and headless sessions |
| Codex | `$cli-crew <your goal>` | Skill loading verified; Codex as main is less tested |
| Antigravity (`agy`) | Mention `cli-crew` in your request | Skill loading verified; agy as main is less tested |

Example: `/cli-crew Fix the CSV export bug, then have another model review the change.`

Even when invoked, the main agent still decides what to delegate. Small tasks are usually done directly. Run `crew detect` in the project to see which workers are available. After updating cli-crew, start a new CLI session: a session that is already open keeps the skill text it loaded at the start.

**Project instructions.** When you ask for project setup, or invoke the skill by name with a request that permits edits, the main agent runs `crew init`. Existing root-level `AGENTS.md`, `CLAUDE.md`, or `GEMINI.md` files are used as they are. Otherwise a starter `AGENTS.md` is created and the main agent fills in the purpose, conventions, and test commands it can verify from the repository. If the main agent loads the skill on its own during other work, or the request is read-only, no instruction file is created. Instead, the final report notes once that the guide is missing and offers to set it up.

### Use GitHub Copilot CLI for a small edit

In PowerShell, set up a dedicated Copilot profile outside the target project. Sign in and disable hooks once for that profile.

```powershell
$env:COPILOT_HOME = Join-Path $env:LOCALAPPDATA 'crew-copilot-profile'
copilot login
copilot config disableAllHooks true
```

From the project directory, create a contract with one existing non-secret file and leave `effort: low`. Replace `src/example.py` with your file.

```powershell
crew contract new T-EDIT --agent copilot --kind implement --mode write --write src/example.py
```

Fill in **Objective**, **Context**, and **Done when** in the generated contract. Then check and run it in the same terminal.

```powershell
crew contract check .agent-runs/contracts/T-EDIT.md
crew run --contract .agent-runs/contracts/T-EDIT.md --with-result
```

Review the diff and run the relevant tests before accepting the edit. [Copilot profile restrictions and file permissions](skill/cli-crew/references/adapters.md#github-copilot-cli-setup)

## Commands

The main agent normally runs these for you. You can also use them directly in PowerShell. Start with `crew --help`.

| Stage | Commands |
| --- | --- |
| Prepare the project | `crew init`, `crew detect` |
| Plan a multi-step goal | `crew plan new`, `crew plan check`, `crew plan show` |
| Choose a worker | `crew routing`, `crew compare` |
| Define the task | `crew contract new`, `crew contract check` |
| Run and monitor | `crew run`, `crew status`, `crew cancel`, `crew trace view` |
| Verify and decide | `crew result`, `crew check`, `crew accept`, `crew reject` |
| Hand off and clean up | `crew handoff`, `crew clean` |

See the [command reference](crew/README.md#command-reference) for full syntax and options.

## Inspecting a run

Run `crew trace view` from the project and open the printed `127.0.0.1` URL.

- On Windows, a shared Codex session mirrors output and accepts input in both your terminal and Crew Runs. See [Shared CLI session](crew/README.md#shared-cli-session) for setup.
- Select a run in the left list. **Timeline** shows live activity and available subagent details. **Output** loads the worker's result, and **Verification** shows recorded verdicts and runs `check` for that task.
- **New run** previews a saved contract before launch. **Cancel run** appears beside an active task and asks you to confirm the worker identity. Contracts that require approval follow the CLI workflow. A started run continues after you close the viewer.
- **Project status** runs `status`. **Command history** keeps recent command output and exit codes available after reload.

See the [trace viewer guide](crew/README.md#live-trace-viewer) for setup and coverage.

### Using Kanban

1. In the viewer, select **Kanban** beside **List**. Use **Plan** to show one cplan or **All plans** to see everything.
2. Select a **Not run** card to preview its saved contract. Check the task, worker and mode, then select **Confirm run** if you want to start it.
3. Select a run card to open its details. Check **Output** and **Verification** for a run in **Needs review**. The card moves to **Complete** when the main agent records acceptance.

For the four columns and how to cancel a run, see [Kanban in the CLI guide](crew/README.md#kanban).

### Inspecting from the CLI

Suppose the main agent ran a review as task `T-01`. From the same project:

```powershell
crew status
crew result T-01
crew check T-01 --recheck
```

| Question | Where to look |
| --- | --- |
| What was the worker asked to do? | `.agent-runs/contracts/T-01.md`: scope, completion criteria, and permitted paths for write tasks. |
| What did it report? | `crew result T-01` shows the saved result. Add `--raw` for the full response. |
| Did it stay in scope? | `crew check T-01 --recheck` recomputes the boundary and subagent checks from saved evidence. |

Example: a worker can report success even when the final file-boundary check fails. `crew` then records `DONE_UNVERIFIED`, and the work cannot be accepted until the main agent records its own verification. The [regression tests](crew/tests/test_crew.py) cover this case. They also cover a read-only worker that modifies a file and a cancellation right after the worker starts.

## Supported workers

| Worker | Read and review | Edit files | Read access |
| --- | --- | --- | --- |
| Codex (`codex`) | Yes | Paths named in the contract | Any file the user can read |
| Claude Code (`claude`) | Yes | Paths named in the contract | Project and listed directories only |
| Antigravity (`agy`) | Yes | Paths named in the contract. See boundary check limits below. | Any file the user can read |
| GitHub Copilot CLI (`copilot`) | — | One existing non-secret file | Files in the project |

Adapters were verified with codex-cli 0.171.0, agy 1.5.14, Claude Code 2.1.330, and GitHub Copilot CLI 1.0.112. Re-check after upgrading a CLI. See [worker adapters and Copilot setup](skill/cli-crew/references/adapters.md#github-copilot-cli-setup).

## Safety model and limitations

- **Concurrency.** Up to three independent read tasks can run at once by default (`max_concurrent`). A write task always runs alone. Reservations take an OS file lock on `.agent-runs/locks/reserve.lock`, which is released if the holder dies; do not run an older `crew` version on the same project at the same time.
- **Shell commands, builds, and tests.** The workflow assigns these to the main agent. Codex uses its filesystem sandbox and a worker instruction to use file tools. Shell restrictions depend on the CLI and environment. [Worker adapter restrictions](skill/cli-crew/references/adapters.md#known-behaviour)
- **Boundary checks.** `crew` reports detectable changes inside the project. Writes outside the project escape this check. agy write tasks use per-session settings that block outside writes and overwrites of unlisted files. agy can create files inside the project, and the Git boundary check skips ignored files except those with secret-like names.
- **Web access.** Workers may search and read the public web, but their instructions forbid sending project content, secrets or personal data. `crew` does not block the network. How much each CLI can actually fetch has not been re-verified.
- **Data leaves your machine.** Workers use their own provider accounts, so delegated content goes to OpenAI, Anthropic, Google, or GitHub Copilot. If project instructions mention sensitive data, `crew run` requires explicit approval. If they restrict external agents, the main agent asks before delegating.
- **Cost approval.** High reasoning effort, or models you list in `~/.crew/config.toml`, require approval before launch.
- **No automatic rollback.** `crew` reports violations but does not revert changes.

The full rules are in the [verification and approval protocol](skill/cli-crew/references/protocol.md).

## How cli-crew differs from other orchestrators

Built-in subagents and native swarms (such as Claude Code subagents or Antigravity `/swarm`) and autonomy-first orchestrators (such as [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode)) optimize for **speed and throughput**. cli-crew is a **cross-model audit and handoff harness** that optimizes for **control, independent verification, and verifiable records**:

| | Native subagents / swarms | Autonomy-first orchestrators (e.g. oh-my-claudecode) | cli-crew |
| --- | --- | --- | --- |
| Primary goal | Fast parallel exploration and edits inside one CLI | Run teams of agents until tests/build pass | Cross-provider audit, bounded delegation, and multi-CLI handoffs |
| Providers & models | Same CLI / provider ecosystem | Claude Code driving external CLIs | Claude Code, Codex, or Antigravity as main or worker, plus GitHub Copilot CLI for small edits |
| Worker permissions | Shares session tools or isolated workspace branches | Headless workers can run with auto-approve flags | Restricted launch modes and contract-scoped edits. Shell restrictions vary by adapter. |
| Edit & test loop | Workers can edit files and run tests directly | Automated verify/fix loops until tests, build, and lint pass | Write tasks run one at a time. The main agent runs tests and builds. |
| Communication | Interactive multi-turn or shared message board | Automated multi-stage loop | One-shot contract execution (`codex exec`, `claude -p`, `agy --print`, `copilot -p`) |
| Audit & boundaries | Session transcript only | Shared filesystem (listed as a known limitation in their SECURITY.md) | Per-task `write_paths`, before/after file snapshots, `## REVIEW` verdicts, and recorded acceptance reasons under `.agent-runs/` |

**Honest trade-offs (when to use what):**
- **Routine coding and implementation speed.** Serial writes, contracts, and independent verification add time and token cost for small tasks a main agent can finish on its own.
- **Cross-provider code review and defect detection:** This is where cli-crew pays off. A worker from a different provider (`codex`, `claude`, `agy`) checks deliverables against explicit criteria (`## REVIEW`) without trusting the author's claims, catching blind spots that single-model self-review misses.
- **Strict governance and cross-CLI handoffs:** Choose cli-crew when you cannot allow `--yolo` / bypass flags, need credential-like environment variables stripped, want detective file-boundary checks, or need to hand off an active plan (`cplan` + `handoffs/`) when switching between Claude Code, Codex, and Antigravity.

The oh-my-claudecode comparison reflects its README and SECURITY.md as read on 2026-11-21.

## Handing off to another CLI

Before ending a session, run this from the project. Replace `codex` and `csv-export` with your agent and task name:

```powershell
crew handoff --agent codex --task csv-export --status "In progress" --lang en
```

This creates a dated Markdown draft under `handoffs/`, or under an existing `handoff/` directory. The draft includes the selected runs and the main agent's recorded verification. Complete the remaining work and next steps, then open the **same working copy** in the next CLI and ask it to read the latest handoff and compare it with the current Git state. Handoff is manual for now. Automatic main-agent switching is planned future work.

## Updating

From your cli-crew clone, run:

```powershell
git pull --ff-only && uv tool install --reinstall ./crew && crew --version
```

If you use the shared CLI session, keep the extra when reinstalling with `uv tool install --reinstall "./crew[terminal]"`.

Use `crew --version` to confirm the command runs. For the shared CLI session, also check `crew trace view --help` for `--interactive`. Start a new AI CLI session to load the updated skill.

An editable development installation reads Python code from the checkout. After pulling, changes to that code are available on the next `crew` invocation. Dependency changes still require an install. See [updating and recovering an editable install](crew/README.md#updating-an-editable-install).

> **Tip:** In File Explorer, open the clone folder (it contains `crew` and `scripts`), type `pwsh` in the address bar, and press Enter to open PowerShell there.

## Uninstalling

From the original clone, unregister the skill first, then remove the command. The package is named `cli-crew`. The executable is named `crew`.

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall
uv tool uninstall cli-crew
```

To remove the skill from one client only, for example Codex:

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall -Agents codex
```

The script removes only junctions and entries that point to this clone. Other agy settings and backups are preserved. Your project files (`AGENTS.md`, `.agent-runs/`, handoffs) and the clone itself are left in place. After a full uninstall, `uv tool list` should no longer show `cli-crew`. If `crew` still runs, `Get-Command crew -All` shows which executable PowerShell finds.

## Project status

cli-crew is an early release (0.1.0). Windows installation, reinstallation, and removal are tested in disposable home directories. macOS and Linux have not been verified.

## Development

### Safely remove local files

From the repository root, preview the move with [`scripts/trash.py`](scripts/trash.py). Review the listed paths and destinations before adding `--yes`.

```powershell
py -3 scripts/trash.py path\to\file
py -3 scripts/trash.py path\to\file --yes
```

On Windows, the script uses the Recycle Bin when it can verify that recycling is available for the item. Otherwise it moves the item to a dated backup folder on the same volume, under `<volume>\cli-crew-trash` by default. If the script refuses any path during the preview checks, it moves nothing. See [the repository's deletion rules](AGENTS.md) for exceptions.

### Run tests

The base `crew` installation uses the Python standard library. The test suite uses local fake workers and makes no provider calls.

```powershell
cd crew
py -3 -m unittest discover -s tests
```

GitHub Actions runs the same suite on Windows with Python 3.12 and 3.13 for every push to `main` and every pull request ([workflow](.github/workflows/test.yml)).

| Document | Contents |
| --- | --- |
| [crew/README.md](crew/README.md) | CLI command reference |
| [skill/cli-crew/SKILL.md](skill/cli-crew/SKILL.md) | Instructions the main agent follows |

This project was built with AI assistance from Claude Code, Codex, and agy. They helped with specification drafting, implementation, cross-review, testing, and documentation.

## License

Released under the [MIT License](LICENSE).

cli-crew is an independent project and is not affiliated with, endorsed by, or sponsored by Anthropic, OpenAI, Google, or GitHub. Claude, Claude Code, Codex, Antigravity, and GitHub Copilot are trademarks of their respective owners and identify the tools cli-crew works with.
