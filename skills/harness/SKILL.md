---
name: harness
description: Plan or execute this repository's phase-and-step development workflow with Codex. Use when the user requests a harness plan, phase, or execution.
---

Read `agent.md` and `docs/HARNESS.md` from the repository root. Follow the user's requested scope: planning produces phase files; execution uses `scripts/execute.py` for the selected phase. Reuse authorization already given in the conversation.

Read relevant product documents and existing code before designing steps. Each step must be independently understandable, identify the files it needs, and contain executable acceptance checks in its index entry. Keep each step focused on one coherent change. Refer to function signatures and invariants rather than prescribing every implementation detail.

For a planning-only request, produce the plan without launching model workers. For an execution request, first validate with `--dry-run`, then run the selected phase when prerequisites are satisfied. Existing user changes must be reviewed and committed before execution; do not silently include them in the harness commits.

The executor owns state updates, retries, acceptance verification, and Git commits. A worker returns the schema-defined result. It must not invoke another executor or change phase metadata. Use `blocked` for credentials, permissions or manual setup that prevent progress.

Report the actual verification result, changed files and actionable blockers. Only pass `--push` when the user requested pushing the branch.
