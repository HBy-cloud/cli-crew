# cli-crew

**English** · [한국어](README.ko.md)

Give your AI coding assistant a goal. The **cli-crew skill** helps it decide what to do itself, what to delegate to another CLI, and how to verify the result. The **`crew` command** runs those workers and records what happened.

## Example

From the project you want to work on, ask your assistant:

> Investigate why the CSV export drops the last row. Fix it if you can reproduce it, and add a regression test.

For a small fix, the assistant can do the work directly. If a second review would help, it can give another CLI a specific question and a set of files. The assistant checks the findings against the code and runs the tests. You can name a preferred worker or leave that choice to the assistant.

## How delegation works

Each worker gets a **task contract** with an objective, a completion condition, and permitted write paths for edit tasks. Independent read tasks can run concurrently (up to three). Write tasks run one at a time.

| Worker CLI | Tasks |
| --- | --- |
| Codex (`codex`) | Investigation, review, scoped edits |
| Claude Code (`claude`) | Investigation, review, scoped edits |
| Antigravity (`agy`) | Investigation and review only |

Delegation can send project content to the selected CLI’s provider and use its configured account. The main assistant checks project instructions before delegation. After a run, `crew` reports changes detected inside the project and saves the worker’s result under `.agent-runs/`. The main assistant checks the evidence and records whether it accepts the work.

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

## Update or remove

After pulling changes into the clone, rebuild the command and refresh skill registrations:

```powershell
uv tool install --reinstall ./crew
pwsh -File scripts/install-skill.ps1
```

To remove both components, run these commands from the clone:

```powershell
pwsh -File scripts/install-skill.ps1 -Uninstall
uv tool uninstall cli-crew
```

Add `-Agents claude,codex` to the uninstall script to remove only those skill registrations. The script preserves unrelated registrations, backs up existing agy settings before editing them, and retains the settings file even if its registration list becomes empty.

## Development and AI use

This project was developed with AI assistance from Claude Code, Codex, and agy, including specification drafting, implementation, cross-review, testing, and documentation.

Windows installation, reinstallation, and removal were tested in disposable homes. From `crew/`, run `py -3 -m unittest discover -s tests` to run the Python test suite with local test workers. macOS and Linux have not been verified.

[CLI commands](crew/README.md) · [Skill instructions](skill/cli-crew/SKILL.md) · [Architecture overview](docs/SPEC.en.md) · [Full specification (Korean)](docs/SPEC.md)
