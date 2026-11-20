# cli-crew

**English** · [한국어](README.ko.md)

**Delegate work across AI coding CLIs and keep the evidence in your project.**

Stay in the CLI where you started. Give it a goal, and the cli-crew skill helps it decide whether to work directly, bring in another CLI for a scoped task, or ask for an independent review. The Python command `crew` launches workers and records their results. Your main assistant owns the final decision.

[Install on Windows](#install-on-windows) · [Your first task](#your-first-task) · [Commands](#commands) · [Inspect a run](#inspect-a-run) · [Update](#update) · [Uninstall](#uninstall) · [CLI reference](crew/README.md)

![The main assistant defines a contract, a worker runs it, and main verifies the result.](docs/assets/workflow.svg)

## Your first task

After installation, open your **target project** in a new AI CLI session. For example:

> The CSV export drops the last row. Reproduce the bug, fix it, and add a regression test.

You supply the outcome. The assistant can reproduce and fix a small bug itself. If a second set of eyes would help, it can ask a read-only worker to check how the exporter handles a missing final newline while main owns the fix and regression test. The worker does not need the whole conversation: its contract names the question, relevant paths, and completion criteria.

**On first use**, if the request permits edits, main runs `crew init`. It reuses an existing root-level `AGENTS.md`, `CLAUDE.md`, or `GEMINI.md`. In a project without one, it creates a starter `AGENTS.md` and main fills in the purpose, working conventions, and test commands it can verify. Read-only requests leave the project guide untouched.

## Commands

In a Codex conversation, mention `$cli-crew` with your goal. In PowerShell, use `crew` to perform or inspect individual steps. Start with `crew --help`.

| When | Commands |
| --- | --- |
| Prepare the project | `crew init`, `crew detect` |
| Define the task | `crew contract new`, `crew contract check` |
| Run and monitor | `crew run`, `crew status`, `crew cancel` |
| Verify and decide | `crew result`, `crew check`, `crew accept`, `crew reject` |
| Hand off or clean up | `crew handoff`, `crew clean` |

[Full command syntax and options](crew/README.md#command-reference)

## Inspect a run

Suppose main assigned the export review task ID `T-01`. From the same project:

```powershell
crew status
crew result T-01
crew check T-01 --recheck
```

| Question | Where to look |
| --- | --- |
| What was the worker asked to do? | `.agent-runs/contracts/T-01.md` records the scope and completion criteria. Write contracts also name the permitted paths. |
| What did the worker report? | `crew result T-01` shows the saved result. Add `--raw` to read the full response. |
| Did it stay within scope? | `crew check T-01 --recheck` uses saved evidence to recheck project changes and subagent use. Main checks findings against the files and runs the relevant tests before recording a verdict. |

**A concrete failure case:** a worker can report success even if the final file-boundary check fails. In that case `crew` records `DONE_UNVERIFIED` and requires main to supply verification before accepting the work. The test suite also exercises a read-only worker modifying a file and cancellation just after the worker process starts. [Regression tests](crew/tests/test_crew.py)

## Workers and execution limits

| Worker | Read and review | Edit files |
| --- | --- | --- |
| Codex (`codex`) | Yes | Scoped write contract |
| Claude Code (`claude`) | Yes | Scoped write contract |
| Antigravity (`agy`) | Yes | Refused before launch |

Up to three independent read tasks may run concurrently. Writes run alone, and main owns shell commands, builds, and tests. File-boundary checks report changes detectable **inside the project**; they do not provide OS-level isolation. An agy write is refused because its adapter cannot enforce that boundary.

Workers use their configured provider accounts and may send project content to those providers. Before delegation, main reads applicable project instructions and observes data-sharing and cost approvals. [Worker adapters](skill/cli-crew/references/adapters.md) · [Verification and approval rules](skill/cli-crew/references/protocol.md)

## Picking up work in another CLI

Run this from the project before ending a session. Replace `codex` and `csv-export` with the current main agent and task name.

```powershell
crew handoff --agent codex --task csv-export --status "In progress" --lang en
```

The command creates a dated Markdown draft under `handoffs/` (or an existing `handoff/` directory). It includes the selected runs and any recorded main verification. Complete the draft with the remaining work and the first check the next session should run.

Open the **same working copy** in the next CLI and ask it to read the latest handoff and compare it with the current Git state. This is a manual handoff. Automatic main-agent switching and end-to-end cross-CLI resumption are listed as future work in the [specification, T-06](docs/SPEC.md). This repository ignores `handoffs/` and `.agent-runs/`, so a fresh clone does not include those local records.

## Install on Windows

You need Git, PowerShell 7 (`pwsh`), Python 3.12+, and [uv](https://docs.astral.sh/uv/). Install and sign in to at least one worker CLI to use delegation. Run the following commands in PowerShell:

```powershell
git clone https://github.com/HBy-cloud/cli-crew.git
cd cli-crew
uv tool install ./crew
$env:Path = "$(uv tool dir --bin);$env:Path"
pwsh -File scripts/install-skill.ps1
crew --version
```

The installer registers the skill for all three clients. To select clients, use `pwsh -File scripts/install-skill.ps1 -Agents claude,codex`. Claude Code and Codex use directory junctions; agy registers the skill directory in `~/.gemini/config/skills.json`. Keep the clone at its installed path. If a new terminal cannot find `crew`, run `uv tool update-shell` and reopen the terminal.

Start a new AI CLI session in your target project. Run `crew detect` there to see which workers are available. To request the skill explicitly, mention `cli-crew` in your prompt.

## Update

Open PowerShell in the **original cli-crew clone** (the folder containing both `crew/` and `scripts/`). The skill points to files in this clone; the standard installation of the `crew` executable is a separate copy managed by uv.

1. Fetch the latest source. If Git reports a conflict or cannot fast-forward, stop here and resolve the Git issue before reinstalling.

   ```powershell
   git pull --ff-only
   ```

2. Reinstall the command from the updated source and check that it starts:

   ```powershell
   uv tool install --reinstall ./crew
   crew --version
   ```

3. Start a **new AI CLI session** in your target project to load the updated skill. Existing registrations already point at the updated files. If a registration is missing, restore just that client, for example `pwsh -File scripts/install-skill.ps1 -Agents codex`. Running the installer without `-Agents` registers all three clients.

## Uninstall

**Remove everything installed by the quick start.** From the original clone, unregister the skill first, then remove the shared command. The uv package name is `cli-crew`; the executable name is `crew`.

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall
uv tool uninstall cli-crew
```

**Remove the skill from one client only.** For example, to remove it from Codex while keeping Claude Code, agy, and the shared `crew` command:

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall -Agents codex
```

The script reports the junction or entry removed for each selected client. Start a new client session for the change to take effect. After a **full uninstall**, run `uv tool list`: it should no longer list `cli-crew`. If `crew` still runs, `Get-Command crew -All` shows what PowerShell is finding.

**What stays on disk:** the source clone and your project files, including `AGENTS.md`, `.agent-runs/`, and handoffs. The script removes only registrations pointing to this clone. It preserves unrelated agy settings, the settings file, and its backups. If you plan to delete the clone yourself, unregister the skill first.

## Development and AI use

This project was developed with AI assistance from Claude Code, Codex, and agy, including specification drafting, implementation, cross-review, testing, and documentation.

Windows installation, reinstallation, and removal were tested in disposable homes. From `crew/`, run `py -3 -m unittest discover -s tests` to run the Python test suite with local test workers. macOS and Linux have not been verified.

[CLI commands](crew/README.md) · [Skill instructions](skill/cli-crew/SKILL.md) · [Architecture overview](docs/SPEC.en.md) · [Full specification (Korean)](docs/SPEC.md)

## License

Released under the [MIT License](LICENSE).
