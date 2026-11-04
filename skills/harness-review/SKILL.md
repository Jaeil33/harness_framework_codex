---
name: harness-review
description: Review changes in a project using this Codex harness against its phase acceptance criteria and project guidance. Use for a requested review, not for starting implementation.
---

Read `agent.md`, the relevant phase/Step specification, and the applicable product architecture and decision documents. Inspect the actual diff and related code.

Check behavior against the Step's acceptance commands, existing tests, architectural rules, and scope. For executor changes, examine false success, retries, blocked/interrupted runs, Git ownership, UTF-8 I/O and Windows command launching. Run relevant checks when possible; report checks that could not run as unverified.

Lead with concrete defects in severity order, linking each finding to its file and line and explaining a reproducible trigger. Separate verified behavior from inference. If no defects were found, say so and identify remaining validation limits. Do not edit files unless the user requested fixes.
