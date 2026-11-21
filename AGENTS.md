# cli-crew — Agent Instructions

## Project
`cli-crew` lets a main AI CLI delegate bounded tasks to non-interactive worker CLIs (codex, agy, claude)
and verify the results. It has two parts:
- `crew/` — Python CLI `crew` (mechanics: detection, contracts, runs, checks, records).
- `skill/cli-crew/` — the skill that tells the main agent how and when to use `crew`.

## Source of truth
- Maintainer checkouts keep the current specification in `docs/SPEC.md`. Its decisions are binding.
- In a public checkout, use the README, CLI guides, and `skill/cli-crew/references/`.
The following private records may be present in a maintainer checkout:
- `docs/original-requirements.md` — the user's original requirements (do not edit).
- `docs/SPEC_DRAFT.md`, `docs/DEV_PLAN.md` — decision history only (superseded).
- `docs/research/` — experiment and review evidence. Do not edit past records.

## Language
- Communicate with the user in the user's language.
- Write this file, code comments, and handoff records in English. `docs/SPEC.md` is in Korean.

## Rules
1. Before work, read this file and the relevant project documentation. In a maintainer checkout, read
   `docs/SPEC.md` (§1–§2, §4–§10, and §14; read §3, §11–§13, §15 when needed). Check `git status`
   and read the latest local handoff, if present. Preserve existing changes.
2. Stay within the requested scope. Do not invent requirements; ask the user when the spec is silent.
3. Keep behaviour changes to `crew` or the skill in sync across `skill/cli-crew/` (`SKILL.md`, `references/`,
   `templates/`), `README.md`, `README.ko.md`, `crew/README.md`, and `crew/README.ko.md`. In a
   maintainer checkout, also update the local specification and English overview.
4. After code changes run `py -3 -m unittest discover -s tests` from `crew/` and report the real result.
   Never claim tests passed unless they ran.
5. Do not commit, push, publish, delete user data, or change files outside this repository
   (including `~/.claude`, `~/.codex`, `~/.gemini`, `~/.crew`) without an explicit request.
6. Never read or print secrets (.env, keys, tokens). Real-CLI smoke tests must run only inside a throwaway project
   under `crew/tests/_work/` (git-ignored); any smoke test against this repository itself must be read-only.
7. On Windows, use `py -3`; `python` may resolve to the Microsoft Store stub. For development, install the tool
   in editable mode with `uv tool install -e crew`.
8. When using the `cli-crew` skill on this repository (or when the user asks to work with a worker or reviewer),
   follow `skill/cli-crew/SKILL.md`: write a cplan (`crew plan new`) before any multi-step task or file edit, and
   run an independent reviewer step on a different model than the author.

## Handoffs
- Handoffs are local records; `handoffs/` is git-ignored and not published.
- On finishing or stopping work, create `handoffs/YYYY-MM-DD/HHmm-<agent>-<task>.md` (real local clock time;
  lowercase letters, digits, hyphens), directly or via `crew handoff`. Do not edit prior handoffs (only fill in
  the `TODO` placeholders of a draft just created by `crew handoff`).
- Include: purpose and status (Done / In progress / Blocked), changed files, verification with results (and linked
  `crew` runs/plans or worker handoffs when used), unfinished items and next steps. Verified facts only; no secrets.
