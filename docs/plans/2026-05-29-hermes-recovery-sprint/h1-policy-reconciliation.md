# H1 Policy Reconciliation - Hermes Recovery Enforcement

> Date: 2026-05-30
> Scope: Hermes recovery sprint H1 policy document and E3 canary evidence boundary.
> Status: Documentation and test-fixture down payment only. No production restore is implemented here.

## Purpose

H1 reconciles the recovery sprint with the current Hermes provider and restore policies after
PRs #237-#240. It records which providers may participate in recovery gates, what evidence is
required before any live restore, and which enforcement gaps remain out of scope for this PR.

The immediate recovery objective remains unchanged: prove the restore path on
`signals.db.canary` before any operator considers a live `signals.db` restore.

## Current PR Baseline

| PR | Status | Policy relevance |
| --- | --- | --- |
| #237 `test: add cli-exclusive llm generation guard` | Merged 2026-05-30 | Codifies that LLM generation must route through approved CLI surfaces rather than direct ad hoc API generation. |
| #238 `feat: route hermes kimi through cli wrapper` | Merged 2026-05-30 | Makes Hermes Kimi execution use the shared `kimi-cli` wrapper and removes Kimi API-key coupling from the Hermes path. |
| #239 `feat: route maestro kimi through cli wrapper` | Merged 2026-05-30 | Aligns Maestro Kimi routing with the same CLI-backed wrapper policy. |
| #240 `docs: add Hermes recovery sprint plan` | Merged 2026-05-30 | Establishes the recovery sprint plan, including Phase 1 canary restore and Phase 6 enforcement harvest. |

## Provider Policy

Codex is the required Hermes provider. It may participate in production-phase routing and
deliberation only through the configured Codex CLI wrapper and Hermes gates. Critical execution
still requires the task-specific acknowledgement token and the relevant task runner.

Kimi is enabled through the shared `kimi-cli` wrapper. For recovery, Kimi is suitable for
deliberation quorum and review work through Hermes. It must not bypass Hermes task gates or write
directly to production state.

Gemini is available as a CLI-backed reviewer and artifact producer. In Hermes v1, Gemini remains
non-mutating for recovery policy purposes: it can review, synthesize, and produce evidence, but it
is not the actor that mutates `signals.db`.

Antigravity is outside the Hermes mutation boundary. The recovery design allows Antigravity to be
launched directly by the operator for Phase 0 offsite backup artifact work, but Hermes treats
Antigravity as deferred and non-executable. It is not a production-state mutator.

## High-Risk Restore Gate Rules

A live restore to `signals.db` is a critical operation. The following gates are required before
any production target mutation:

1. The Harmonic API and all DB writers are confirmed down. `--force` is forbidden on the
   production target.
2. The restore source is the sidecar-free Phase 0 backup
   `backups/signals-20260529-190655.db` with SHA256
   `01ced671a3c1a3800646edad42c2fa9ef2841f587d8255b4049a7c6e3fdd0a26`.
3. Phase 1 canary restore runs against `signals.db.canary`, not `signals.db`.
4. Dry-run and preflight evidence are captured before execute.
5. Execute uses `--ack-risk RESTORE_DB`, `--handle-sidecars`, `--min-row-count 612`, and
   `--expected-schema-version 53`.
6. E3 fixture assertions must cover backup hash, target row count, schema version, integrity,
   sidecar state, Hermes run artifacts, DB ops ledger status, and repair-prompt state.
7. Before any live restore, Phase 2 deliberation must record a Codex plus Kimi quorum and the
   operator must give explicit approval.

## Canary Evidence Recorded By This PR

This PR records the Phase 1 canary rehearsal in
`tests/ops/hermes/fixtures/recovery_sprint_canary_restore/manifest.json`.

The rehearsal verified the logical restore path without mutating `signals.db`:

- Backup SHA256 matched the Phase 0 source.
- API reachability guard was checked before restore work.
- Dry-run passed after the disposable target was normalized to avoid WAL sidecars during the
  non-mutating postflight.
- Preflight passed.
- Execute copied the 612-row, schema 53 backup to `signals.db.canary`.
- The DB ops ledger recorded `success`.
- Independent SQLite verification after execute returned integrity `ok`, 612 rows, and schema 53.

The same execute run also exposed an enforcement gap: the task runner reported
`postflight_failed` because its own postflight integrity check observed transient
`signals.db.canary-wal` and `signals.db.canary-shm` files before process exit. A
`repair_prompt.md` was written for that run. This PR does not hide that result; the manifest and
fixture test record it as the current E3 blocker.

## What H1 Does Not Implement

H1 does not perform a live restore, close Issue #149, reactivate keepalive, or mutate
`signals.db`.

H1 does not implement H2 gate-binding for critical restore execution. The current emergency
bypass still depends on explicit operator approval plus Codex/Kimi deliberation before any live
restore.

H1 does not change Hermes source code. In particular, it does not fix the restore task's
transient WAL-sidecar postflight behavior observed during the canary rehearsal. That fix belongs
in a follow-up code PR because this PR is intentionally limited to policy documentation, a
sanitized fixture, and fixture assertions.
