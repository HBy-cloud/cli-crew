# cli-crew — Agent Instructions

## Project
`cli-crew` lets a main AI CLI delegate bounded tasks to non-interactive worker CLIs (codex, agy, claude)
and verify the results. It has two parts:
- `crew/` — Python CLI `crew` (mechanics: detection, contracts, runs, checks, records).
- `skill/cli-crew/` — the skill that tells the main agent how and when to use `crew`.

## Source of truth
- `docs/SPEC.md` — current spec (v1.0). Decisions D-xx, R2..R5 are binding.
The following private records may be present in a maintainer checkout:
- `docs/original-requirements.md` — the user's original requirements (do not edit).
- `docs/SPEC_DRAFT.md`, `docs/DEV_PLAN.md` — decision history only (superseded).
- `docs/research/` — experiment and review evidence. Do not edit past records.

## Language
- Communicate with the user in Korean.
- Write this file, code comments, and handoff records in English. `docs/SPEC.md` is in Korean.

## Rules
1. Before work, read this file, `docs/SPEC.md`, the latest available handoff (if any) under `handoffs/` (latest date folder,
   then latest time), and check `git status`. Preserve existing changes.
2. Stay within the requested scope. Do not invent requirements; ask the user when the spec is silent.
3. Behaviour changes to `crew` or the skill must be reflected in `docs/SPEC.md`.
4. After code changes run `py -3 -m unittest discover -s tests` from `crew/` and report the real result.
   Never claim tests passed unless they ran.
5. Do not commit, push, publish, delete user data, or change files outside this repository
   (including `~/.claude`, `~/.codex`, `~/.gemini`, `~/.crew`) without an explicit request.
6. Never read or print secrets (.env, keys, tokens). Real-CLI smoke tests must be read-only and use a
   throwaway project under `crew/tests/_work/` (git-ignored).
7. `python` on this machine is a Store stub; use `py -3`. The tool is installed with
   `uv tool install -e crew` (editable).

## Handoffs
- On finishing or stopping work, create `handoffs/YYYY-MM-DD/HHmm-<agent>-<task>.md` (Asia/Seoul, real clock
  time; lowercase letters, digits, hyphens). Never edit existing handoffs.
- Include: purpose and status (Done / In progress / Blocked), changed files, verification with results,
  unfinished items and next steps. Verified facts only; no secrets.
