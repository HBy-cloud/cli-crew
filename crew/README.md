# crew

**English** · [한국어](README.ko.md)

Python CLI behind the `cli-crew` skill. It lets a main AI CLI delegate bounded tasks to worker CLIs
(`codex`, `agy`, `claude`) in safe, non-interactive modes and records verifiable results.
[Architecture overview](../docs/SPEC.en.md) · [Skill instructions](../skill/cli-crew/SKILL.md) · [Windows installation](../README.md)

## Install (development)
```
uv tool install -e .
crew --version
```

## Typical flow (run inside a project)
```
crew detect --paths src/
crew contract new T-01 --agent codex --mode read
# edit .agent-runs/contracts/T-01.md
crew run --contract .agent-runs/contracts/T-01.md --with-result
# inspect status, boundaries and RESULT; verify the evidence before acceptance
crew accept T-01            # or: crew reject T-01 --reason "..."
crew handoff --agent claude --task my-task --status Done
```

agy workers support read tasks only.

Safety flags for `crew run` (ask the user first): `--approve-data-sharing` (instructions mention personal/sensitive
data), `--approve-cost` (high effort / approval models).
Credential-like environment variables are removed from the worker environment (except the worker's own provider key).

Other commands: `crew status`, `crew check <id> --recheck`, `crew cancel <id> [--yes]`,
`crew clean [--sessions] [--runs] [--older-than N] [--yes]`.

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
