# agy-DIRECTOR-WORKER (`agydw`) Plugin Rules (3.0.0-agy.7)

You are working inside a governed **agy-DIRECTOR-WORKER (`agydw`)** workspace.

## Core Layout (3.0.0-agy.7)
- **In-project Director folder**: `DIRECTOR/` inside the repository root (git-ignored via `.git/info/exclude` + `DIRECTOR/.gitignore`).
- **Workspace rules file**: `.agent/rules/director-work.md`.
- **Entry index for handoffs**: `DIRECTOR/INDEX.md` (read this first when taking over or resuming).
- **Brief Gate (R6)**: Before `DIRECTOR/BRIEF.md` passes `DIRECTOR/tools/spec_gate.py check`, do **not** edit product code, create feature branches/worktrees, or dispatch coding subagents. Only read-only exploration, `research` / `dw-blind-reviewer` subagents, and updates to `DIRECTOR/` planning/state files are permitted.

## Roles & Communication
- **Director**: Owns requirements (`BRIEF.md`), decisions (`DECISION_REGISTER.md`), `DIRECTOR/orders/`, dispatch logs (`DIRECTOR_LOG.md`), and final gate verification. For pending user decisions, prefer `ask_question` and persist entries in `DIRECTOR/PO_PENDING.md`.
- **Workers / Subagents**: Receive explicit orders (`DIRECTOR/orders/WO-*.md`), report ONLY to the Director via `send_message` or task return (`TASK_REPORT`), never self-approve orders (`spec_gate.py approve`, `gate_check.py --snapshot`) and never modify protected `DIRECTOR/` governance files, hooks, or rules.
- **Blind Review**: Use `dw-blind-reviewer` (`tools: [read_file]`, isolated MCP/customizations) for independent specification or code checks.

## Verification & Hygiene
- Always invoke Python tools with `-B -I -X utf8` (never leave `__pycache__` or `.pyc` files).
- Every gate check requires an explicit observed exit code `0`; missing/ambiguous exit status is `INVALID`, never `PASS`.
- Record test/command evidence cleanly (`evidence_record.py`) and verify orders/reports/verdicts via `DIRECTOR/tools/gate_check.py` and `DIRECTOR/tools/scope_check.py`.
