# Hermes Track A Post-PR235 Hardening Strategy

> Date: 2026-06-01
> Base reviewed: `origin/main` at `c1f0a23`
> Status: H0 strategy correction, ready for follow-on implementation PRs
> Scope: Hermes Track A enforcement and audit semantics only

## 1. Purpose

This document is the tracked H0 addendum for the post-PR235 Hermes Track A
hardening program. It turns the forward proposal into implementation-grade
definitions so H1-H5 code PRs do not invent incompatible meanings for plan
hashes, deliberation freshness, drift checks, ledger reconciliation, or bypass
lifecycle records.

The recovery/live-restore work remains separate. This strategy may define gates
that live recovery must satisfy, but it must not run `restore-db`, mutate
`signals.db`, or treat canary approval as approval for the production target.

## 2. Current Repo Contract

Live `origin/main` already has these Hermes facts:

- Registered tasks: `collector-promote`, `config-promote`, `contract-check`,
  `deliberate`, `governance`, `incident`, `ledger-audit`, `outbox-purge`,
  `restore-db`, `shadow-validate`, `suppression-sync`.
- `restore-db` records backup and target fingerprints, refuses uncheckpointed
  backup WAL sidecars, supports canary targets, snapshots the pre-restore
  target, and requires `--ack-risk RESTORE_DB` for execute.
- `deliberation_passed` currently checks record status, blockers, dissent, a
  supplied plan hash, and a TTL bound. It does not yet enforce trusted reviewer
  identity, affected-resource causality, or JSON-only approver compliance.
- `ledger-audit` currently reconciles ledger index rows, run directories, and
  referenced artifacts. It is not yet a subsystem state reconciler.
- `contract-check` is a task-contract smoke check. It is not yet the complete
  source of schema-version truth for all task plans and run records.
- `incident` is load-bearing for incident capsule state and packets. It is not
  a restore authorization mechanism and must not be used as a bypass approval.

## 3. Canonical Plan Hash

### 3.1 Domain

The canonical plan hash is a SHA-256 digest over a typed JSON preimage:

```json
{
  "domain": "hermes.task_plan",
  "contractVersion": 2,
  "task": "<task name>",
  "mode": "<plan-only|preflight-only|dry-run|execute>",
  "riskLevel": "<low|medium|high|critical>",
  "inputPreimage": {},
  "resourcePreimage": {},
  "outputContractPreimage": {}
}
```

The string form is `sha256:<64 lowercase hex characters>`.

### 3.2 Canonical JSON

Canonical JSON means:

- UTF-8 object encoding.
- Lexicographically sorted object keys.
- Compact separators with no insignificant whitespace.
- Arrays preserve semantic order.
- Strings are not case-normalized unless a field-specific rule says so.
- Non-finite numbers are invalid.
- Missing optional fields and explicit `null` are not interchangeable unless the
  field-specific rule says so.

### 3.3 Included Fields

`inputPreimage` includes task identity, CLI arguments that shape task behavior,
actor-declared target environment, risk acknowledgement requirement, lock scope,
configured preflight/postflight gate names, and an allowlisted environment
preimage when environment affects behavior.

`resourcePreimage` includes the planned affected resources and their observed
facts at plan time. Examples: absolute or repo-relative file path, SQLite
database path, table set, row-count threshold, schema version, main-file hash,
WAL/SHM sidecar summary, and backup lineage hash.

`outputContractPreimage` includes planned artifact names and typed output
contracts, not realized outputs. Examples: `task_plan.json`, `run_record.json`,
`dry_run.json`, `execute.json`, `approval_required.json`,
`ledger_audit_report.json`, and task-specific packet names.

### 3.4 Excluded Fields

The hash excludes volatile runtime facts:

- `run_id`, `runId`, `run_dir`, `runDir`, `ledger.run_dir`, and absolute run
  artifact directories.
- `created_at`, `createdAt`, `started_at`, `updated_at`, `generatedAt`,
  durations, process IDs, hostnames, and temporary paths.
- Execution status, check results, realized outputs, exception text, repair
  prompts, and ledger index append position.
- Provider prose, reviewer commentary, and synthesized deliberation text unless
  a task explicitly includes a structured reviewer verdict in its input
  preimage.

Excluding a field from the plan hash does not make it unaudited. Volatile fields
still belong in `run_record.json` or task artifacts.

## 4. Contract Versions

H1 must introduce `contractVersion` in `task_plan.json` and
`contract_version` in `run_record.json`. The dual spelling is intentional:
camelCase for task-plan JSON consumed by gate policies, snake_case for the
existing Python run-record style. Historical records without either field remain
audit-readable and must be interpreted as version 1.

Version 2 is the first version required to carry a canonical plan hash and a
dry-run/execute preimage binding. New mutating task runs must write the current
contract version after H1 lands.

## 5. Causal Deliberation Freshness

Deliberation freshness is causal first and TTL second. A deliberation is fresh
for an execute attempt only when all of these are true:

1. The deliberation input plan hash exactly equals the execute plan hash.
2. Every counted approval is structured JSON that matches the reviewer verdict
   schema.
3. Every counted approver identity is trusted by policy for the requested task,
   target environment, and risk class.
4. The panel satisfies the policy quorum. For critical restore work, Codex prose
   is advisory only unless Codex JSON compliance has been repaired and the
   policy has been updated to count it.
5. No intervening mutating Hermes run touched any affected file, table,
   database, external system, or policy resource in the execute plan after the
   deliberation was created.
6. No relevant bypass or override record expired before execute.
7. The deliberation age is inside the configured TTL.

TTL is therefore a secondary bound. A five-minute-old approval is stale if a
mutating `restore-db`, `governance`, `config-promote`, `collector-promote`,
`suppression-sync`, or `outbox-purge` run changed an affected resource after the
approval. A twenty-hour-old approval may remain fresh only if the resource graph
is unchanged and the TTL allows it.

Malformed reviewer output, unknown reviewer identity, missing plan hash, missing
affected-resource metadata, and unreadable ledger evidence all fail closed.

## 6. Dry-Run Drift

Dry-run drift is the difference between the execute attempt and the exact
execute-shaped dry run that authorized it. H1/H2 code must record enough
preimage data to compare:

- CLI argument hash.
- Task contract version.
- Policy and provider config hashes that affect routing, approval, locks, or
  mutation.
- Allowlisted environment variables that affect the task.
- File digests for affected files and backups.
- SQLite database digest facts for affected targets: schema version, table set,
  row-count thresholds, main-file hash, WAL/SHM sidecar summary, and any
  task-defined per-table digest.
- Planned output artifact contract.

An execute attempt may proceed only when the execute preimage equals the bound
dry-run preimage, except for fields explicitly marked execute-only by the task
contract. Execute-only fields must be named in the task plan and justified in
the contract; silent drift fails closed.

## 7. Ledger-Audit V2 Reconciliation

Ledger-audit v2 extends the current index/run/artifact audit into subsystem
reconciliation. Each subsystem slice must define:

- Subsystem name and owner task.
- Resource inventory.
- Digest algorithm and digest preimage.
- Quiescence or read strategy.
- Genesis baseline for legacy state.
- Legacy baseline handling when old records lack v2 fields.
- Typed drift findings.
- Tests that prove known-good, known-drift, missing-baseline, and unreadable
  evidence behavior.

The first subsystem must be restore/SQLite because it has the highest blast
radius and the strongest existing restore evidence.

Required v2 finding codes:

- `missing_index_entry`
- `missing_run_dir`
- `missing_required_artifact`
- `malformed_json`
- `unsupported_contract_version`
- `plan_hash_mismatch`
- `dry_run_binding_mismatch`
- `resource_digest_mismatch`
- `resource_missing`
- `unledgered_mutation`
- `quiescence_violation`
- `genesis_baseline_missing`
- `schema_version_mismatch`
- `sidecar_policy_violation`
- `bypass_overdue`
- `unknown_subsystem`

Findings must carry severity, subsystem, resource id, observed digest, expected
digest when available, evidence path, and remediation hint.

## 8. Bypass And Override Lifecycle

Bypasses and overrides are not comments, PR-body checkboxes, or environment
variables. They are structured artifacts, ledgered per run or per policy, with
an explicit lifecycle.

Required fields:

- `bypassId`
- `kind`
- `scope`: `run`, `resource`, `task`, or `policy`
- `policyRef`
- `reason`
- `severity`
- `affectedResources`
- `operator`
- `authorizer`
- `createdAt`
- `expiresAt`
- `deadline`
- `expectedRemediation`
- `actualRemediationRunId`
- `status`: `active`, `remediated`, `expired`, or `revoked`
- `planHash`
- `evidence`

Bypass records must be narrow. A canary restore bypass cannot authorize a live
`signals.db` restore. A Kimi+Gemini temporary reviewer variance cannot rewrite
the standing Codex+Kimi policy. `ledger-audit` must detect active bypass records
older than their SLA with no remediation run id and report `bypass_overdue`.

Metrics must distinguish ledgered bypasses from unledgered mutation. A ledgered
emergency path is debt; an unledgered production mutation is a control-plane
failure.

## 9. Threat Model

### In Scope

- Stale plan execution after resource drift.
- Approval reuse across canary and live targets.
- Malformed, prose-only, or untrusted reviewer output counted as approval.
- Dry-run/execute input drift.
- SQLite backup or target sidecar surprises.
- Unledgered mutation by repo-local Hermes tasks and maintenance scripts that
  should go through Hermes.
- Ledger index, run directory, artifact, and resource digest drift.
- Bypass records that outlive their scope or remediation SLA.
- Historical records missing new fields.

### Out Of Scope

- Malicious local administrator tampering with git history, the filesystem, or
  Hermes artifacts after the fact.
- Compromised OS, Python interpreter, shell, GitHub account, cloud account, or
  provider binary.
- Reconstructing data for which no backup or source-of-record evidence exists.
- Proving absence of every possible manual SQLite edit outside the digest and
  quiescence strategies defined for a subsystem.
- Model collusion or semantic correctness of reviewer judgment beyond schema,
  identity, quorum, and blocker/dissent policy.

## 10. Enforcement Status

`contract-check` is load-bearing today only as a registry/base-contract smoke
test. H1 may make it load-bearing for contract-version visibility, but it must
not become the only enforcement point for every task-specific invariant.

`incident` is load-bearing for incident lifecycle state and incident response
packets. It is explicitly exempt from restore authorization, bypass approval,
and reviewer quorum. Those responsibilities belong to deliberation gates and
the bypass lifecycle artifacts.

`deliberate` is not fully load-bearing for critical restore authorization until
H2 adds trusted reviewer identity, structured verdict parsing, provider
availability preflight, and no-intervening-mutation checks on top of the
existing plan-hash binding.

`ledger-audit` is load-bearing today only for index/run/artifact consistency.
It becomes subsystem load-bearing one subsystem PR at a time after the v2
framework lands.

## 11. PR Sequence

H0 must land first so later PRs implement named contracts rather than new
interpretations.

H1 follows with canonical plan-hash helpers, contract-version fields, schema
compatibility, and historical-read behavior.

H2a follows with deliberation freshness and quorum binding. H2b follows with
restore-specific readiness gates and live/canary separation. H2 must land before
any H3 subsystem check depends on trusted deliberation evidence.

H3a lands the ledger-audit v2 framework and the first restore/SQLite subsystem.
H3b+ lands one subsystem per PR: governance/config, collector promotion,
suppression/outbox, and Notion-facing sync if needed. Do not add subsystem
checks before the v2 framework exists.

H4 lands the structured emergency-bypass task or equivalent ledgered artifact
surface plus overdue detection.

H5 lands cross-task rehearsals, failure-event artifacts, health/summary
operator views, lock-order assertions, and CI/nightly-audit behavior.

Each code PR remains TDD-first and scoped to its named slice. Restore/live
recovery must continue to use its own recovery strategy and must not be bundled
into broad Track A hardening.

## 12. Acceptance Checklist For Follow-On PRs

- Focused failing tests are written before implementation.
- New v2 fields are optional for historical read paths and required for new
  mutating writes after the relevant PR lands.
- Malformed or missing policy evidence fails closed.
- Dry-run/execute drift tests include at least one file digest drift, one env or
  config drift, and one SQLite resource drift where relevant.
- Ledger-audit fixtures include clean, drifted, missing-baseline, and unreadable
  evidence cases.
- Verification includes focused tests, `tests/ops/hermes/ -q` when behavior
  touches shared Hermes contracts, provider doctor, and `git diff --check`.
