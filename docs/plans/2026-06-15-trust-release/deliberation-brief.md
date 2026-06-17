# Trust Release Steelman — Adversarial Deliberation Brief
Date: 2026-06-16 | HEAD: de00bb0

You are reviewing the SweetSwwetHarmony Trust Release Completion proposal.
Do NOT read or modify any files. Return ONLY a JSON object with these keys:
  verdict, confidence, concerns, required_changes

Verdict options: "approve" | "needs_changes" | "block"
confidence: float 0.0–1.0

## Proposal Summary

The proposal closes two open issues (signals.db recovery + Issue #148 news_api staleness)
and hardens CI enforcement before resuming product work. Eight milestones run in parallel
tracks.

Core claims:
1. Litestream is live (S3 replication, SQLITE_DB_PATH env var). Restore must pause
   Litestream, validate SQLITE integrity, reset generation, restart.
2. restore_db.py already has DBToolLock (timeout=5s). Proposal extends this to a
   maintenance-scoped lock for the full restore operation (which can take minutes).
3. v52 migration adds rows_returned_this_iter, rows_after_filter_this_iter,
   last_failure_mode columns via ALTER TABLE under live WAL mode.
4. Parity gate requires CLI vs direct-API delta = 0 across 64 golden samples.
5. vcrpy cassettes isolate dry-run tests from live API calls for CI determinism.
6. Circuit breaker on api_shape_changed suspends the offending collector and raises alert.
7. check_pr_evidence.py enforces evidence bundles by parsing PR body text.
8. Trust status CLI (M7) reads collector_health.py v2 report.

## Specific Weaknesses to Probe

For each weakness below, assess severity and whether it requires a blocking change,
a documented exception, or is acceptable as-is.

W1 LITESTREAM PAUSE SEMANTICS
   Standard `litestream replicate` has no pause command. The proposed "pause" likely
   requires SIGTERM + restart. Restoring a file during an active replication window risks:
   (a) the replica overwriting the just-restored file before the generation reset, and
   (b) the pre-restore WAL not being fully complement-synced to S3 before SIGTERM.
   Is the proposed orchestration actually safe, or does it need an explicit
   generation-fencing step before the file copy?

W2 DBTOOLLOCK TIMEOUT INCOMPATIBILITY
   DBToolLock default timeout is 5 seconds. A restore operation (backup copy, integrity
   check, Litestream restart) can take 30–120 seconds on a loaded DB. Extending DBToolLock
   scope without raising the timeout means the lock will expire mid-operation. The
   proposal does not specify what timeout the maintenance lock requires.

W3 DELTA=0 PARITY IS UNTESTABLE
   Gemini CLI and direct API both use sampling; identical prompts can return different
   labels between runs. Delta=0 across 64 samples may never be achievable without
   temperature=0.0 on both paths. If the gate is literally "zero mismatches," it could
   permanently block thesis-sensitive PRs even when both paths agree on accuracy floor.
   Should the gate be "delta=0 at temperature=0.0" or "accuracy delta < 0.02"?

W4 V52 MIGRATION UNDER ACTIVE WRITERS
   SQLite schema changes (ADD COLUMN) require an exclusive write lock. Under WAL mode,
   concurrent readers are allowed but the exclusive lock must wait for all active writers.
   If collector processes are running during migration, the migration can block
   indefinitely or fail. The proposal specifies no coordination mechanism (maintenance
   lock during migration, pre-check for active writers, collector pause/resume).

W5 API_SHAPE_CHANGED CIRCUIT BREAKER DURABILITY
   The proposal says api_shape_changed "automatically suspends the collector and raises
   an alert." The suspension state must be durable. If stored in memory only, it resets
   on process restart. If stored in the DB, a diagnostic run (which is supposed to use a
   scratch DB) would write suspension state to the wrong database and pollute production
   state. Where is the durable suspension flag persisted and how is it reset?

W6 VCRPY CASSETTE STALENESS
   VCR cassettes become stale when upstream APIs change. The proposal mandates cassettes
   for CI determinism but does not specify: (a) how cassettes are regenerated when APIs
   change, (b) whether stale cassettes would mask api_shape_changed events (the exact
   failure mode W5's circuit breaker is supposed to catch), or (c) cassette storage
   policy (checked in vs. generated, size limits, rotation policy).

W7 PR BODY PARSING IS FORGEABLE
   check_pr_evidence.py parsing PR body text to enforce evidence bundles can be bypassed
   by including the trigger phrase (e.g., "test results: see artifact links:") with
   placeholder content. The enforcement is syntactic, not semantic. What prevents a
   contributor from writing "test results: see CI" and satisfying the check with no
   actual evidence linked?

W8 TRACK B DEPENDENCY INVERSION
   The proposal says PR 1A (db_anomaly.py) and PR 1B (restore_db.py hardening) are
   "parallel." But db_anomaly.py produces known_bad_shas.json (created in M1.3), and
   the restore script's --manifest-out references the anomaly checker's output. If 1B
   lands before 1A, restore_db.py will reference a manifest checker that does not exist.
   Is the integration branch strategy explicit enough to prevent a broken intermediate
   state on main?

W9 TRUST STATUS CLI DEPENDENCY
   M7 (trust status CLI) reads collector_health.py REPORT_SCHEMA_VERSION=2. The proposal
   marks M7 as "blocked by B" but does not explicitly state it requires M3's health v2
   schema. If M7 lands before M3, it reads v1 format and the collector health summary
   will be incomplete or silently wrong.

W10 TWO CANONICAL PLAN DOCUMENTS
   After this proposal, two canonical plan files exist: the original 29KB 00-strategy.md
   (which M0 amends in-place) and this revised steelman proposal. The proposal does not
   designate which is authoritative for future sessions. Sessions that only read
   00-strategy.md will miss the steelman's revised scope and sequencing; sessions that
   only read the steelman will miss the original context. Should M0 replace 00-strategy.md
   rather than annotate it?

## Required Output

Return ONLY a JSON object:
{
  "verdict": "approve" | "needs_changes" | "block",
  "confidence": 0.0–1.0,
  "concerns": ["list of specific concerns with severity labels"],
  "required_changes": ["list of concrete changes required before implementation begins"]
}
