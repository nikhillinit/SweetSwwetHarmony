# RALPLAN-DR: Hermes H3c Collector Promotion Ledger Audit V2

## Live Refresh

- `git fetch origin main --prune` completed.
- `origin/main` is `f4b1140`, the PR #252 merge commit.
- PR #252 is merged with successful checks and no open PRs were reported.
- Fresh lane: `codex/hermes-track-a-h3c-ledger-audit-collector-promotion-v2`.
- Provider doctor succeeds in the fresh lane; optional antigravity is disabled because the binary is missing.

## Plan

1. Add failing collector-promotion ledger-audit tests for known-good evidence, missing and malformed artifacts, unsupported versions, binding mismatches, digest drift, dry-run drift evidence, and legacy baseline signaling.
2. Add a distinct `collector_promotion` ledger-audit subsystem module and wire it into `ledger_audit.py`.
3. Add `artifactVersion: 1` narrowly to collector-promote output artifacts if needed for v2 auditability.
4. Extend `ledger_audit_report.schema.json` for the new subsystem while keeping restore and governance-config behavior green.
5. Verify with py_compile, ruff on touched files, focused pytest, broader Hermes pytest, provider doctor, and `git diff --check`.

## Exclusions

- No live/canary restore command.
- No mutation of `signals.db`, `signals.db.canary`, or live `state/collectors.json`.
- No suppression/outbox, Notion sync, H4/H5, recovery-ops, or merged-worktree cleanup.
