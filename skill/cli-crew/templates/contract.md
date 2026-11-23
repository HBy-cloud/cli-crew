---
task_id: T-EXAMPLE
agent: codex
mode: read
task_kind: investigate
write_paths: []
add_dirs: []
allow_subagents: false
effort: low
model: null
timeout_min: 20
max_retries: 1
retry_of: null
ephemeral: false
worker_handoff: false
plan: null
plan_step: null
---
## Objective
One or two sentences describing the goal.

## Context
- Relevant file paths (not contents), decisions, constraints.

## Done when
- Observable conditions that mean the task is complete.

## Notes

For GitHub Copilot CLI, use `agent: copilot`, `mode: write`, and `effort: low`. List one existing non-secret file in `write_paths`.
When the contract is linked to a cplan, `crew trace view` groups its run under that plan and streams state changes while the view is open. Select the run to retrieve its check and result from the Verification and Output tabs.
The Kanban view can filter by cplan and opens a run preview from an unrun contract card.
Use the viewer's Run controls or `crew run` for this contract.

The viewer can preview a saved contract before launch. Review the contract and use the CLI workflow for any required approvals.
- Optional.
