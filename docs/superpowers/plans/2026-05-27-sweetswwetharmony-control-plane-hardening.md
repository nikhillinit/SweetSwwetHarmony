# SweetSwwetHarmony Control Plane Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the SweetSwwetHarmony control-plane hardening stack as small, auditable PRs that protect database state, thesis behavior, entity truth, private relationship data, CRM status sync, and parallel-agent scope.

**Architecture:** Extend the live Harmonic/Discovery Engine surfaces instead of creating parallel green-field directories. Database work stays on SQLite and the existing restore/db-guard path, thesis work composes with the existing golden-set/eval scripts, entity work extends `storage/entity_identity_store.py` and merge-review APIs, relationship work extends `storage/relationship_store.py`, CRM work extends `workflows/notion_pusher.py` and `connectors/notion_connector_v2.py`, and local-first memory lives under the existing `docs/` hierarchy.

**Tech Stack:** Python 3.11, SQLite/aiosqlite, GitHub Actions, pytest, Notion API connector, existing Harmonic `ops`/`scripts`/`storage` packages, optional Litestream for off-host SQLite replication.

---

## Inputs Reviewed

- Source plan: `C:\Users\nikhi\Downloads\SweetSwwetHarmony_integrated_implementation_procedure.md`
- Review plan: `C:\Users\nikhi\.claude\plans\review-c-users-nikhi-downloads-sweetswwe-polished-wozniak.md`
- Live repo: `C:\dev\Harmonic`
- Remote: `https://github.com/nikhillinit/SweetSwwetHarmony.git`

## Repo Reality Confirmed Before Writing

- `git status --short --branch` showed `main...origin/main [ahead 4]`, modified `docs/claude/active-sprint.md`, modified `state/collectors.json`, and untracked keepalive/artifact files. Do not sweep these into implementation commits.
- `gh pr list --repo nikhillinit/SweetSwwetHarmony --limit 20` returned `[]` at review time. Recheck before implementation because `docs/claude/active-sprint.md` currently claims older PRs are open.
- The proposed green-field directories do not exist: `consumer/entity_resolution`, `crm`, `private_graph`, `knowledge`, and `scripts/ops`.
- Existing relevant surfaces:
  - SQLite restore and guard: `scripts/restore_db.py`, `run_pipeline.py`, `utils/db_guard.py`
  - Thesis eval: `scripts/run_thesis_llm_eval_gate.py`, `scripts/thesis_diagnostic_runner.py`, `utils/thesis_benchmark.py`, `tests/fixtures/thesis_llm_golden_set.*`, `.github/workflows/thesis-eval.yml`
  - Merge/entity truth: `storage/entity_identity_store.py`, `storage/merge_cascade.py`, `api/routers/merge_review.py`, `api/models/merge.py`
  - Relationship graph: `storage/relationship_store.py`, `tests/storage/test_relationship_store.py`
  - CRM/Notion: `workflows/notion_pusher.py`, `workflows/suppression_sync.py`, `connectors/notion_connector_v2.py`
  - Enrichment: `enrichment/`, `storage/consumer_enrichment.py`, `storage/saas_enrichment.py`, `storage/health_enrichment.py`
  - Docs: `docs/runbooks/`, `docs/decisions/`, `docs/plans/`
- `storage.signal_store.CURRENT_SCHEMA_VERSION` is currently `53`; the next schema migration number must be computed from the live constant during implementation, not from the last standalone migration filename.
- Direct script execution of `scripts/run_thesis_llm_eval_gate.py` fails import resolution; use module form: `python -m scripts.run_thesis_llm_eval_gate --help`.
- Notion status spelling is canonical as `Dilligence` with two Ls in `connectors/notion_connector_v2.py`.

## Finalized Stack

The original strategy is directionally sound, but the implementation must be resequenced and rewired:

1. PR 0: Execution scaffolding and docs artifact layer
2. PR 1: SQLite durability and restore verification
3. PR 2: Thesis golden-set gate enforcement
4. PR 3: PR scope guard, advisory first
5. PR 4a: Merge API direction validation
6. PR 4b: Entity survivorship and audit
7. PR 5a: Enrichment cache, TTL, and rate budget
8. PR 5b: Privacy-safe relationship pathfinding
9. PR 5c: CRM monotonic stage sync
10. PR 6: Required-guard promotion and final verification

This keeps state protection before new automated writes, lands guardrails before the broad entity/CRM work, and avoids duplicating existing systems.

## Global Rules For Every PR

- [ ] Start from a fresh branch or worktree with prefix `codex/`.
- [ ] Run `git status --short --branch` before editing and before staging.
- [ ] Recheck open PRs with `gh pr list --repo nikhillinit/SweetSwwetHarmony --limit 20 --json number,title,headRefName,baseRefName,state,url`.
- [ ] Add failing tests before implementation changes.
- [ ] Keep commits scoped; do not include `.omx/`, keepalive artifacts, local state drift, or unrelated docs changes.
- [ ] Use GitHub labels as maintainer approval signals; do not use PR-body flags or author-controlled environment variables as approval.
- [ ] If a label is applied after a CI run starts, rerun the workflow or make the job fetch current labels through GitHub API.
- [ ] No required workflow should use top-level path filters. Required checks must appear on every PR and no-op inside the job when irrelevant.
- [ ] Every new persistent schema change must update the live migration registry and schema-version tests.
- [ ] Every high-impact mutation must have a runbook, rollback section, and test evidence.

## Manual Setup Before PR 0

- [ ] Create approval labels if missing.

```powershell
gh label create db-restore-approved --repo nikhillinit/SweetSwwetHarmony --color B60205 --description "Maintainer approval for production DB restore" 2>$null
gh label create thesis-label-drift-approved --repo nikhillinit/SweetSwwetHarmony --color D93F0B --description "Maintainer approval for golden-set relabeling" 2>$null
gh label create baseline-promotion-approved --repo nikhillinit/SweetSwwetHarmony --color FBCA04 --description "Maintainer approval for eval baseline promotion" 2>$null
gh label create scope-override-approved --repo nikhillinit/SweetSwwetHarmony --color 5319E7 --description "Maintainer approval for PR scope override" 2>$null
gh label create external-enrichment-write-approved --repo nikhillinit/SweetSwwetHarmony --color 006B75 --description "Maintainer approval for bulk enrichment writes" 2>$null
gh label create entity-merge-approved --repo nikhillinit/SweetSwwetHarmony --color 0E8A16 --description "Maintainer approval for entity merge apply" 2>$null
gh label create notion-stage-downgrade-approved --repo nikhillinit/SweetSwwetHarmony --color C5DEF5 --description "Maintainer approval for automated Notion stage downgrade" 2>$null
```

- [ ] Confirm branch protection can add these checks as they land:
  - `SQLite Durability Smoke`
  - `Thesis Golden Set Gate`
  - `Local Artifact Validation`
  - `PR Scope Guard`
  - `Entity Merge Safety Tests`
  - `Relationship Pathfinding Tests`
  - `CRM Sync Safety Tests`

---

### Task 0: Execution Scaffolding And Local Artifact Layer

**Files:**
- Create: `.github/pull_request_template.md`
- Create: `.github/CODEOWNERS`
- Create: `docs/specs/README.md`
- Create: `docs/incidents/README.md`
- Create: `docs/evals/README.md`
- Create: `docs/approvals/README.md`
- Create: `docs/templates/spec.md`
- Create: `docs/templates/incident.md`
- Create: `docs/templates/eval_update.md`
- Create: `docs/templates/approval.md`
- Create: `scripts/create_doc_artifact.py`
- Create: `scripts/ci/check_doc_artifacts.py`
- Create: `tests/scripts/test_create_doc_artifact.py`
- Create: `tests/ci/test_check_doc_artifacts.py`
- Create: `.github/workflows/local-artifact-validation.yml`
- Modify: `docs/runbooks/` only by adding `docs/runbooks/local-first-agent-memory.md`

- [ ] **Step 0.1: Write failing tests for artifact generation**

Add tests that call `scripts/create_doc_artifact.py` for `spec`, `incident`, `eval_update`, and `approval`. Assert generated Markdown has YAML front matter, ASCII-only required keys, ISO `created_at`, and no Obsidian dependency.

Run:

```powershell
python -m pytest tests/scripts/test_create_doc_artifact.py -q
```

Expected before implementation: fail because the script does not exist.

- [ ] **Step 0.2: Write failing tests for artifact validation**

Add tests for `scripts/ci/check_doc_artifacts.py` that reject missing `type`, invalid `status`, invalid `owner`, invalid approval front matter, and malformed Markdown.

Run:

```powershell
python -m pytest tests/ci/test_check_doc_artifacts.py -q
```

Expected before implementation: fail because the validator does not exist.

- [ ] **Step 0.3: Implement templates and generator**

Use existing `docs/` hierarchy instead of top-level `knowledge/`. Valid artifact types:

```python
TARGET_DIR_BY_TYPE = {
    "spec": "docs/specs",
    "incident": "docs/incidents",
    "eval_update": "docs/evals",
    "approval": "docs/approvals",
    "adr": "docs/decisions",
    "runbook": "docs/runbooks",
}
```

Required front matter keys:

```yaml
type: spec
status: draft
owner: codex
created_at: 2026-05-27
related_prs: []
related_files: []
```

Approval artifacts must also include:

```yaml
approval_required_for: []
approval_log: []
```

- [ ] **Step 0.4: Add PR template and CODEOWNERS**

Create `.github/pull_request_template.md` with these checkboxes:

```markdown
## Scope
- [ ] Migration added or explicitly not needed
- [ ] Rollback documented or explicitly not needed
- [ ] Runbook updated or explicitly not needed
- [ ] CI check added or explicitly not needed
- [ ] Approval labels required or explicitly not needed
- [ ] Related docs artifact linked or explicitly not needed

## Verification
- [ ] Tests run locally
- [ ] `git diff --check` run
- [ ] Security-sensitive changes reviewed for secrets and unsafe writes
```

Create `.github/CODEOWNERS` with `@nikhillinit` as initial owner for:

```text
.github/workflows/** @nikhillinit
scripts/** @nikhillinit
ops/** @nikhillinit
storage/** @nikhillinit
api/routers/merge_review.py @nikhillinit
api/models/merge.py @nikhillinit
workflows/notion_pusher.py @nikhillinit
workflows/suppression_sync.py @nikhillinit
connectors/notion_connector_v2.py @nikhillinit
enrichment/** @nikhillinit
docs/runbooks/** @nikhillinit
docs/decisions/** @nikhillinit
docs/specs/** @nikhillinit
docs/incidents/** @nikhillinit
docs/evals/** @nikhillinit
docs/approvals/** @nikhillinit
```

- [ ] **Step 0.5: Add workflow**

Create `.github/workflows/local-artifact-validation.yml` with a required check name `Local Artifact Validation`. It must trigger on `pull_request` and `workflow_dispatch`, install Python deps, and run:

```bash
python scripts/ci/check_doc_artifacts.py docs
python -m pytest tests/scripts/test_create_doc_artifact.py tests/ci/test_check_doc_artifacts.py -q
```

- [ ] **Step 0.6: Verify and commit**

```powershell
python -m pytest tests/scripts/test_create_doc_artifact.py tests/ci/test_check_doc_artifacts.py -q
python scripts/ci/check_doc_artifacts.py docs
git diff --check
git status --short
```

Commit:

```powershell
git add .github/pull_request_template.md .github/CODEOWNERS .github/workflows/local-artifact-validation.yml docs scripts tests
git commit -m "docs: add local artifact workflow"
```

**Acceptance:** PR template exists, CODEOWNERS points to live paths only, artifact generator and validator pass, and no top-level `knowledge/` directory is introduced.

**Rollback:** Remove the workflow from branch protection first, then revert the PR. Existing docs artifacts can remain.

---

### Task 1: SQLite Durability And Restore Verification

**Files:**
- Create: `.litestream.yml`
- Create: `scripts/sqlite_snapshot.py`
- Create: `scripts/litestream_restore_verify.py`
- Create: `.github/workflows/sqlite-durability-smoke.yml`
- Create: `.github/workflows/litestream-restore-verify-nightly.yml`
- Create: `docs/runbooks/sqlite-durability.md`
- Create: `docs/incidents/db-reversion-control-plane.md`
- Create: `tests/scripts/test_sqlite_snapshot.py`
- Create: `tests/scripts/test_litestream_restore_verify.py`
- Create or modify: `tests/utils/test_db_guard.py`
- Modify: `run_pipeline.py`
- Modify: `utils/db_guard.py`
- Reference without replacing: `scripts/restore_db.py`

- [ ] **Step 1.1: Write failing snapshot tests**

Cover deterministic `VACUUM INTO` snapshot creation:

- identical SQLite input produces identical gzip hash
- manifest includes source DB hash, compressed hash, row-count summary, schema version, and created_at
- temp files are cleaned
- no production credentials are required

Run:

```powershell
python -m pytest tests/scripts/test_sqlite_snapshot.py -q
```

Expected before implementation: fail because `scripts/sqlite_snapshot.py` does not exist.

- [ ] **Step 1.2: Write failing restore-verification tests**

Cover restore verification without invoking production credentials by mocking the Litestream command runner. Assert the script runs `PRAGMA integrity_check`, checks `schema_migrations`, checks signal lower bounds, and rejects `litestream verify`.

Run:

```powershell
python -m pytest tests/scripts/test_litestream_restore_verify.py -q
```

Expected before implementation: fail because `scripts/litestream_restore_verify.py` does not exist.

- [ ] **Step 1.3: Extend the existing guard instead of creating a parallel guard**

`sqlite_durability_check` behavior should live in or delegate through `utils/db_guard.py` and `run_pipeline.py` because the repo already enforces external signal-count watermarks there. Keep the operator bootstrap command:

```powershell
python run_pipeline.py init-watermark
```

Do not weaken missing-watermark fail-closed behavior.

- [ ] **Step 1.4: Add deterministic snapshot script**

Implement `scripts/sqlite_snapshot.py` with module-safe imports and CLI flags:

```text
python -m scripts.sqlite_snapshot --db-path signals.db --out-dir artifacts/sqlite-snapshots/daily --manifest-out artifacts/sqlite-snapshots/latest.manifest.json
```

The script must:

- open the DB read-only where possible
- run `PRAGMA wal_checkpoint(PASSIVE)` only when safe for the selected DB
- use `VACUUM INTO` to a temp DB
- hash uncompressed bytes
- write deterministic gzip with stable metadata
- hash compressed bytes
- emit `snapshot.db.gz`, `snapshot.db.sha256`, `snapshot.db.gz.sha256`, and `snapshot.manifest.json`

- [ ] **Step 1.5: Add Litestream config and workflows**

Use `.litestream.yml`, not `ops/litestream.yml`. Configure env vars for bucket URL, credentials, region, and DB path. Separate paths:

```text
s3://$SQLITE_BACKUP_BUCKET/sweetswwetharmony/litestream/signals.db/
s3://$SQLITE_BACKUP_BUCKET/sweetswwetharmony/snapshots/daily/
s3://$SQLITE_BACKUP_BUCKET/sweetswwetharmony/snapshots/monthly/
```

`sqlite-durability-smoke.yml`:

- check name: `SQLite Durability Smoke`
- triggers: `pull_request`, `workflow_dispatch`
- no production secrets
- runs snapshot tests, restore-verify unit tests, and local integrity checks against a temp DB

`litestream-restore-verify-nightly.yml`:

- triggers: `schedule`, `workflow_dispatch`
- uses a protected GitHub environment for production backup secrets
- restores only to temp DB path
- uploads restore summary artifact

- [ ] **Step 1.6: Write runbook**

`docs/runbooks/sqlite-durability.md` must document:

- current DB reversion incident context
- how `run_pipeline.py init-watermark` interacts with restore
- how to snapshot
- how to restore to a temp DB
- how to restore into production through `scripts/restore_db.py`
- required `db-restore-approved` label and live-writer check
- no generic lifecycle pruning of Litestream WAL paths
- no `litestream verify` command

- [ ] **Step 1.7: Verify and commit**

```powershell
python -m pytest tests/scripts/test_sqlite_snapshot.py tests/scripts/test_litestream_restore_verify.py tests/utils/test_db_guard.py -q
python -m scripts.sqlite_snapshot --db-path ":memory:" --help
python -m scripts.litestream_restore_verify --help
git diff --check
```

Commit:

```powershell
git add .litestream.yml .github/workflows/sqlite-durability-smoke.yml .github/workflows/litestream-restore-verify-nightly.yml docs scripts tests run_pipeline.py utils/db_guard.py
git commit -m "feat: add sqlite durability verification"
```

**Acceptance:** PR smoke passes without secrets; nightly restore can be manually run on `main`; `scripts/restore_db.py` remains the production restore path; no docs or scripts mention `litestream verify`.

**Rollback:** Disable workflows and Litestream service. Keep existing replica/snapshot data. Do not delete backup objects during rollback.

---

### Task 2: Thesis Golden-Set Gate Enforcement

**Files:**
- Create: `.github/workflows/thesis-golden-gate.yml`
- Create: `scripts/ci/detect_thesis_sensitive_changes.py`
- Create: `scripts/ci/check_thesis_gate_artifact.py`
- Modify: `scripts/run_thesis_llm_eval_gate.py`
- Modify: `utils/thesis_eval_gate.py`
- Modify: `tests/scripts/test_run_thesis_llm_eval_gate.py`
- Create: `tests/ci/test_detect_thesis_sensitive_changes.py`
- Create: `tests/ci/test_check_thesis_gate_artifact.py`
- Create: `docs/runbooks/thesis-golden-gate.md`
- Create: `docs/evals/thesis-golden-gate-baseline.md`
- Reuse: `tests/fixtures/thesis_llm_golden_set.jsonl`
- Reuse: `tests/fixtures/thesis_llm_golden_set.manifest.json`
- Reuse: `artifacts/thesis_diagnostics/candidate_v3.jsonl`
- Reuse: `artifacts/thesis_diagnostics/candidate_v3.summary.json`

- [ ] **Step 2.1: Write failing sensitive-change detector tests**

Sensitive paths include:

```python
THESIS_SENSITIVE_PATTERNS = [
    "consumer/thesis_filter/**",
    "utils/thesis_*.py",
    "scripts/*thesis*.py",
    "scripts/ci/*thesis*.py",
    "tests/fixtures/thesis_llm_golden_set*",
    "artifacts/thesis_diagnostics/candidate_v3*",
    ".github/workflows/thesis-golden-gate.yml",
    ".github/workflows/thesis-eval.yml",
]
```

Run:

```powershell
python -m pytest tests/ci/test_detect_thesis_sensitive_changes.py -q
```

- [ ] **Step 2.2: Write failing artifact-check tests**

Assert the checker fails when:

- manifest `dataset_fingerprint` does not match fixture
- candidate sample IDs differ from fixture IDs without label approval
- candidate has errors
- accuracy falls below configured threshold
- relabeling changes fixture but no `thesis-label-drift-approved` label is present

Run:

```powershell
python -m pytest tests/ci/test_check_thesis_gate_artifact.py -q
```

- [ ] **Step 2.3: Implement detector and artifact checker**

Use `tests/fixtures/thesis_llm_golden_set.manifest.json` as the fixture manifest. Do not invent a separate `candidate_v3.sha256` file unless the PR explicitly migrates to that format.

The check must read labels from current GitHub state in CI, not from PR body:

```bash
gh api repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/labels --jq '.[].name'
```

If label state changes after CI starts, the operator must rerun the check.

- [ ] **Step 2.4: Add always-visible workflow**

`thesis-golden-gate.yml`:

- check name: `Thesis Golden Set Gate`
- triggers: `pull_request`, `workflow_dispatch`
- no top-level path filters
- if no sensitive paths changed on PR, emit a success summary and exit 0
- if sensitive paths changed and no LLM key exists, run fixture/manifest/hash/dry-run checks, emit blocked-live-eval summary, and require maintainer `workflow_dispatch` before merge
- if LLM key exists or workflow is manual, run:

```bash
python -m scripts.run_thesis_llm_eval_gate \
  --dataset tests/fixtures/thesis_llm_golden_set.jsonl \
  --output artifacts/thesis_diagnostics/pr-gate.json \
  --rebaseline-output artifacts/thesis_diagnostics/pr-rebaseline.json \
  --baseline-summary artifacts/thesis_diagnostics/candidate_v3.summary.json
python scripts/ci/check_thesis_gate_artifact.py artifacts/thesis_diagnostics/pr-gate.json
```

- [ ] **Step 2.5: Document baseline promotion**

Promotion flow:

```powershell
python scripts/thesis_diagnostic_runner.py `
  --dataset tests/fixtures/thesis_llm_golden_set.jsonl `
  --output-dir artifacts/thesis_diagnostics `
  --run-id candidate_v4 `
  --compare-against artifacts/thesis_diagnostics/candidate_v3.jsonl `
  --temperature 0

python -m scripts.run_thesis_llm_eval_gate `
  --dataset tests/fixtures/thesis_llm_golden_set.jsonl `
  --output artifacts/thesis_diagnostics/candidate_v4.gate.json `
  --baseline-summary artifacts/thesis_diagnostics/candidate_v3.summary.json
```

Promotion requires CODEOWNER review and `baseline-promotion-approved`.

- [ ] **Step 2.6: Verify and commit**

```powershell
python -m pytest tests/scripts/test_run_thesis_llm_eval_gate.py tests/ci/test_detect_thesis_sensitive_changes.py tests/ci/test_check_thesis_gate_artifact.py tests/utils/test_thesis_benchmark.py tests/utils/test_thesis_llm_golden_set.py -q
python -m scripts.run_thesis_llm_eval_gate --help
git diff --check
```

Commit:

```powershell
git add .github/workflows/thesis-golden-gate.yml docs scripts tests utils
git commit -m "ci: enforce thesis golden set gate"
```

**Acceptance:** Required check appears on every PR; non-thesis PRs pass cheaply; sensitive PRs run the gate; fixture relabeling without maintainer approval fails; workflow dispatch can run the full live gate.

**Rollback:** Remove the check from branch protection or mark advisory first. Preserve latest baseline artifacts.

---

### Task 3: PR Scope Guard, Advisory First

**Files:**
- Create: `.pr-scope.yaml`
- Create: `scripts/ci/check_pr_scope.py`
- Create: `scripts/ci/detect_cross_pr_conflicts.py`
- Create: `.github/workflows/pr-scope.yml`
- Create: `.github/workflows/pr-conflict-detector.yml`
- Create: `docs/runbooks/split-and-merge.md`
- Create: `docs/specs/agent-operating-model.md`
- Create: `docs/decisions/approval-gates.md`
- Create: `tests/ci/test_check_pr_scope.py`
- Create: `tests/ci/test_detect_cross_pr_conflicts.py`
- Reference: `.a5c/quality-gates.json`

- [ ] **Step 3.1: Write failing PR-scope tests**

Test that:

- changed files under `owned` pass
- changed files under `read_only` fail
- `scope-override-approved` permits intentional read-only mutation
- missing `.pr-scope.yaml` exits with an advisory success, not failure
- `.a5c/quality-gates.json` remains separate and lower precedence for path ownership

- [ ] **Step 3.2: Implement `.pr-scope.yaml`**

Initial root file:

```yaml
agent: repo-guardrails-agent
owned:
  - .pr-scope.yaml
  - scripts/ci/check_pr_scope.py
  - scripts/ci/detect_cross_pr_conflicts.py
  - .github/workflows/pr-scope.yml
  - .github/workflows/pr-conflict-detector.yml
  - docs/runbooks/split-and-merge.md
  - docs/specs/agent-operating-model.md
read_only:
  - storage/**
  - workflows/**
  - connectors/**
requires:
  - Local Artifact Validation
override_label: scope-override-approved
```

- [ ] **Step 3.3: Add advisory workflows**

`pr-scope.yml`:

- check name: `PR Scope Guard`
- triggers: `pull_request`, `workflow_dispatch`
- advisory for the first two stack PRs by setting `continue-on-error: true`
- after PR 4b lands, remove `continue-on-error` and add it to branch protection

`pr-conflict-detector.yml`:

- advisory only
- uses `gh pr list` to find overlapping changed files
- writes summary, does not block

- [ ] **Step 3.4: Verify and commit**

```powershell
python -m pytest tests/ci/test_check_pr_scope.py tests/ci/test_detect_cross_pr_conflicts.py -q
python scripts/ci/check_pr_scope.py --base origin/main --head HEAD --labels ""
git diff --check
```

Commit:

```powershell
git add .pr-scope.yaml .github/workflows/pr-scope.yml .github/workflows/pr-conflict-detector.yml docs scripts tests
git commit -m "ci: add advisory pr scope guard"
```

**Acceptance:** Out-of-scope edits are detected; override label is honored only from current GitHub label state; cross-PR conflict warnings are visible but non-blocking.

**Rollback:** Keep `.pr-scope.yaml` and make workflow advisory if false positives block urgent fixes.

---

### Task 4a: Merge API Direction Validation

**Files:**
- Modify: `api/models/merge.py`
- Modify: `api/routers/merge_review.py`
- Modify: `tests/api/test_merge_write_endpoints.py`
- Modify or extend: `tests/api/test_merge_review_router.py`
- Reference: `storage/merge_cascade.py`

- [ ] **Step 4a.1: Write failing API validation tests**

Add tests that:

- reject same winner and loser
- reject winner/loser not matching the `merge_suggestions` pair
- reject reversed or unrelated IDs unless explicitly approved by the merge proposal workflow
- preserve idempotency for duplicate proposals
- prove `MERGE_WRITES_ENABLED=active` still gates apply behavior

Run:

```powershell
python -m pytest tests/api/test_merge_write_endpoints.py tests/api/test_merge_review_router.py -q
```

- [ ] **Step 4a.2: Add request model validation**

In `api/models/merge.py`, add a Pydantic model validator to `ProposeRequest`:

```python
@model_validator(mode="after")
def validate_distinct_direction(self) -> "ProposeRequest":
    if self.winner_company_id == self.loser_company_id:
        raise ValueError("winner_company_id and loser_company_id must differ")
    return self
```

Then enforce suggestion-pair membership in `api/routers/merge_review.py` after loading the suggestion row, because the model does not know the DB pair.

- [ ] **Step 4a.3: Route apply through a single merge policy seam**

Keep `cascade_merge()` as the table-update workhorse, but do not let `apply_merge_proposal` blindly trust unvalidated DB direction. Add a small internal helper in `api/routers/merge_review.py`:

```python
def _validated_merge_direction(proposal: dict, suggestion: dict | None = None) -> tuple[str, str]:
    winner_id = proposal["winner_company_id"]
    loser_id = proposal["loser_company_id"]
    if winner_id == loser_id:
        raise HTTPException(status_code=400, detail="Invalid merge direction")
    return winner_id, loser_id
```

This helper is intentionally small in PR 4a. PR 4b will replace or extend it with survivorship-aware policy.

- [ ] **Step 4a.4: Verify and commit**

```powershell
python -m pytest tests/api/test_merge_write_endpoints.py tests/api/test_merge_review_router.py tests/storage/test_merge_cascade.py -q
git diff --check
```

Commit:

```powershell
git add api/models/merge.py api/routers/merge_review.py tests/api/test_merge_write_endpoints.py tests/api/test_merge_review_router.py
git commit -m "fix: validate entity merge direction"
```

**Acceptance:** The API cannot persist or apply invalid winner/loser direction; existing cascade tests still pass.

**Rollback:** Revert this PR only if merge writes are disabled or demoted first.

---

### Task 4b: Entity Survivorship And Audit

**Files:**
- Create: `storage/entity_survivorship.py`
- Create: `tests/storage/test_entity_survivorship.py`
- Modify: `storage/entity_identity_store.py`
- Modify: `storage/signal_store.py`
- Create: `storage/migrations/v54_survivorship_audit.py` unless `CURRENT_SCHEMA_VERSION` changed; use next live number
- Create: `storage/tests/test_v54_survivorship_audit.py` unless migration number changed
- Modify: `tests/integration/test_phase_g_lifecycle.py`
- Modify: `tests/workflows/test_pipeline_wiring.py`
- Modify: `tests/api/test_merge_write_endpoints.py`
- Create: `docs/runbooks/entity-survivorship.md`
- Create: `docs/decisions/entity-survivorship-rules.md`

- [ ] **Step 4b.1: Re-verify migration number**

Run:

```powershell
python - <<'PY'
from storage.signal_store import CURRENT_SCHEMA_VERSION
print(CURRENT_SCHEMA_VERSION)
PY
```

If it prints `53`, create `v54_survivorship_audit.py` and bump `CURRENT_SCHEMA_VERSION` to `54`. If not, use the next number.

- [ ] **Step 4b.2: Write failing survivorship tests**

Cover:

- manual legal/company names are sticky
- website can update under freshness rules
- funding stage can update under short freshness rules
- social proof score can update under short freshness rules
- raw values are not written to audit by default
- every field decision writes an audit row

Use the config:

```python
FIELD_SURVIVORSHIP = {
    "legal_name": {"manual_sticky": True, "freshness_override": False},
    "company_name": {"manual_sticky": True, "freshness_override": False},
    "website": {"manual_sticky": True, "freshness_override": True, "freshness_window_days": 180},
    "description": {"manual_sticky": False, "freshness_override": True, "freshness_window_days": 90},
    "funding_stage": {"manual_sticky": False, "freshness_override": True, "freshness_window_days": 30},
    "social_proof_score": {"manual_sticky": False, "freshness_override": True, "freshness_window_days": 7},
}
```

- [ ] **Step 4b.3: Write failing lexmin-removal tests**

Existing hardcoded lexmin sites to remove or quarantine:

- `storage/entity_identity_store.py`: `winner = min(existing_root, new_root)` in `upsert_strong_key_bindings`
- `storage/entity_identity_store.py`: `winner = min(existing_root, new_root)` in `upsert_alias_bindings`
- `storage/entity_identity_store.py`: `winner = min(root_a, root_b)` in `_merge_entities_internal`

Update tests that currently assert lexmin behavior so they assert explicit survivorship policy behavior instead.

- [ ] **Step 4b.4: Write failing alias side-effect tests**

Add tests proving alias-only conflicts do not archive or overwrite active alias bindings unless a merge proposal has been approved or the path is an explicitly allowed strong-key collision. This protects the no-collision archive side effect in `upsert_alias_bindings`.

- [ ] **Step 4b.5: Implement audit migration**

The migration DDL should create:

```sql
CREATE TABLE IF NOT EXISTS survivorship_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merge_proposal_id INTEGER,
    field_name TEXT NOT NULL,
    winner_source TEXT NOT NULL,
    winner_value_hash TEXT,
    loser_sources_json TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(json_valid(loser_sources_json)),
    CHECK(json_valid(reason_json))
);
```

Do this as a Python migration in `storage/migrations/`, not a standalone `.sql` file.

- [ ] **Step 4b.6: Implement survivorship module**

Create frozen decision objects:

```python
@dataclass(frozen=True)
class SurvivorshipDecision:
    field_name: str
    winner_source: str
    winner_value_hash: str | None
    loser_sources: list[str]
    reason_code: str
    reason_detail: dict[str, object]
```

Hash values before audit:

```python
def hash_audit_value(value: object) -> str | None:
    if value is None:
        return None
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4b.7: Integrate policy into merge paths**

Rules:

- approved merge proposal direction is respected after validation
- field-level values are selected by survivorship policy
- strong-key collisions may still auto-merge only through the policy path and must audit
- alias-only collisions must not silently merge or archive live bindings
- `_merge_entities_internal` must no longer choose lexmin as its default policy

- [ ] **Step 4b.8: Document governance effect**

`docs/runbooks/entity-survivorship.md` must state how this interacts with `MERGE_WRITES_ENABLED=active` and whether the merge-write regret-check observation window resets after PR 4b.

- [ ] **Step 4b.9: Verify and commit**

```powershell
python -m pytest tests/storage/test_entity_survivorship.py storage/tests/test_v54_survivorship_audit.py tests/integration/test_phase_g_lifecycle.py tests/workflows/test_pipeline_wiring.py tests/api/test_merge_write_endpoints.py -q
python -m pytest tests/storage/test_schema_version_parity.py tests/api/test_health_schema_version.py -q
python scripts/lint_identity_patterns.py --check --baseline scripts/identity_lint_baseline.json --root .
git diff --check
```

Commit:

```powershell
git add storage api tests docs scripts
git commit -m "feat: add audited entity survivorship"
```

**Acceptance:** Lexmin winner behavior is gone from production merge selection; every field-level decision is auditable; alias-only side effects are blocked; merge API applies only validated directions.

**Rollback:** Demote or disable merge writes first, keep audit tables, preserve affected proposal snapshots, then revert.

---

### Task 5a: Enrichment Cache, TTL, And Rate Budget

**Files:**
- Create: `enrichment/cache.py`
- Create: `enrichment/rate_budget.py`
- Modify: `enrichment/orchestrator.py`
- Modify: `enrichment/consumer_orchestrator.py`
- Modify: `enrichment/saas_orchestrator.py`
- Modify: `storage/signal_store.py`
- Create: `storage/migrations/v55_company_enrichments.py` unless schema number changed
- Create: `tests/enrichment/test_enrichment_cache.py`
- Create: `tests/enrichment/test_rate_budget.py`
- Create: `docs/runbooks/enrichment-cache.md`
- Create: `docs/specs/enrichment-cache-ttl.md`

- [ ] **Step 5a.1: Write failing TTL/cache tests**

Cover:

- provider cache hit avoids a second provider call inside TTL
- field TTLs override provider TTLs where configured
- raw payload is not stored unless `raw_payload_ref` is explicitly provided
- cache key uses canonical keys from `utils/canonical_keys.py`
- bulk external writes require `external-enrichment-write-approved`

- [ ] **Step 5a.2: Implement shared cache**

Use existing `enrichment/` package, not `crm/enrichment/`. Store normalized payloads with provider metadata:

```sql
CREATE TABLE IF NOT EXISTS company_enrichments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_entity_id TEXT,
    normalized_json TEXT NOT NULL,
    raw_payload_ref TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    fetched_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(canonical_key, provider),
    CHECK(json_valid(normalized_json))
);
```

Default TTL config:

```python
DEFAULT_PROVIDER_TTL_HOURS = {
    "crunchbase": 72,
    "pitchbook": 168,
    "dealroom": 168,
}

FIELD_TTL_HOURS = {
    "funding_stage": 24,
    "latest_funding_round": 24,
    "headcount": 72,
    "description": 720,
    "website": 720,
}
```

- [ ] **Step 5a.3: Verify and commit**

```powershell
python -m pytest tests/enrichment/test_enrichment_cache.py tests/enrichment/test_rate_budget.py tests/workflows/test_enrichment_integration.py -q
python -m pytest tests/storage/test_schema_version_parity.py tests/api/test_health_schema_version.py -q
git diff --check
```

Commit:

```powershell
git add enrichment storage tests docs
git commit -m "feat: add enrichment cache controls"
```

**Acceptance:** Existing enrichment orchestrators use the shared cache; provider calls are budgeted; raw payload storage is off by default.

**Rollback:** Disable cache writes and keep read-only cache reads until stale rows expire.

---

### Task 5b: Privacy-Safe Relationship Pathfinding

**Files:**
- Create: `storage/relationship_pathfinding.py`
- Create: `storage/relationship_path_cache.py`
- Create: `utils/warm_intro_privacy.py`
- Modify: `storage/relationship_store.py`
- Modify: `tests/storage/test_relationship_store.py`
- Create: `tests/storage/test_relationship_pathfinding.py`
- Create: `tests/storage/test_relationship_path_cache.py`
- Create: `tests/utils/test_warm_intro_privacy.py`
- Create: `docs/runbooks/relationship-pathfinding.md`
- Create: `docs/specs/relationship-pathfinding.md`

- [ ] **Step 5b.1: Write failing pathfinding tests**

Use the live `domain_relationships` model, not hypothetical `relationship_edges`.

Test:

- directed traversal from a hashed operator node to target domains
- optional reverse traversal only when a relationship is marked bidirectional
- cycle safety
- max depth default `3`
- minimum edge strength default `0.3`
- sorted results by score, freshness, and length
- no raw email hash, private node ID, or contact name appears in product output

- [ ] **Step 5b.2: Add graph revision and cache**

Extend `storage/relationship_store.py` schema with:

```sql
CREATE TABLE IF NOT EXISTS relationship_graph_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationship_path_cache (
    cache_key TEXT PRIMARY KEY,
    start_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    graph_revision INTEGER NOT NULL,
    max_depth INTEGER NOT NULL,
    min_edge_strength REAL NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    CHECK(json_valid(result_json))
);
```

Prefer app-code revision bumps in every writer in `RelationshipStore` because the store creates its private DB schema directly. Tests must prove `upsert_domain_edge()` and `upsert_lp_relationship()` invalidate cached misses.

- [ ] **Step 5b.3: Add privacy wrapper**

Product-facing output may include only:

```python
@dataclass(frozen=True)
class WarmIntroBadge:
    path_exists: bool
    path_length_bucket: str
    confidence_bucket: str
    strongest_intermediary_type: str | None
    recommended_action: str
```

No raw hashes or names.

- [ ] **Step 5b.4: Verify and commit**

```powershell
python -m pytest tests/storage/test_relationship_store.py tests/storage/test_relationship_pathfinding.py tests/storage/test_relationship_path_cache.py tests/utils/test_warm_intro_privacy.py -q
git diff --check
```

Commit:

```powershell
git add storage utils tests docs
git commit -m "feat: add privacy-safe relationship pathfinding"
```

**Acceptance:** Pathfinding extends private graph storage; cached no-path results invalidate on graph revision change; product output contains no private identifiers.

**Rollback:** Disable badge rendering and clear `relationship_path_cache` if cache format changes.

---

### Task 5c: CRM Monotonic Stage Sync

**Files:**
- Create: `workflows/notion_stage_policy.py`
- Modify: `workflows/notion_pusher.py`
- Modify: `workflows/suppression_sync.py`
- Modify: `connectors/notion_connector_v2.py`
- Create: `tests/workflows/test_notion_stage_policy.py`
- Modify: `workflows/test_notion_pusher.py`
- Create or modify: `tests/connectors/test_notion_connector_v2.py`
- Create: `docs/runbooks/crm-stage-sync.md`
- Create: `docs/specs/crm-enrichment-stage-sync.md`

- [ ] **Step 5c.1: Write failing stage-policy tests**

Canonical Notion statuses:

```python
CANONICAL_NOTION_STATUSES = [
    "Source",
    "Initial Meeting / Call",
    "Dilligence",
    "Tracking",
    "Committed",
    "Funded",
    "Passed",
    "Lost",
]
```

Required behavior:

- `Diligence` normalizes to `Dilligence`
- `Dilligence` stays `Dilligence`
- `Review` is internal only and never written to Notion
- `IC` is internal only and maps before Notion write if it exists in local code
- terminal statuses `Funded`, `Passed`, and `Lost` do not reopen automatically
- automated downgrade requires `notion-stage-downgrade-approved`
- human Notion edits can advance or terminally close deals
- `Review` must not export then re-import as `Initial Meeting / Call`

- [ ] **Step 5c.2: Implement canonical alias map correctly**

Correct map:

```python
NOTION_STATUS_ALIASES = {
    "Diligence": "Dilligence",
    "Dilligence": "Dilligence",
}
CANONICAL_NOTION_DILIGENCE = "Dilligence"
```

- [ ] **Step 5c.3: Place monotonicity at both boundaries**

`workflows/notion_pusher.py` owns local-to-Notion write blocking.

`workflows/suppression_sync.py` owns Notion-to-local reconciliation and terminal-state preservation.

`connectors/notion_connector_v2.py` remains the exact-string boundary for Notion payloads.

- [ ] **Step 5c.4: Verify and commit**

```powershell
python -m pytest tests/workflows/test_notion_stage_policy.py workflows/test_notion_pusher.py tests/connectors/test_notion_connector_v2.py tests/workflows/test_confidence_routing.py -q
python run_pipeline.py publish create --help
python run_pipeline.py publish preview --help
python run_pipeline.py publish commit --help
git diff --check
```

Commit:

```powershell
git add workflows connectors tests docs
git commit -m "fix: enforce monotonic notion stage sync"
```

**Acceptance:** Notion writes always use exact canonical strings; automated downgrade loops are impossible under tests; internal stage names cannot leak to Notion.

**Rollback:** Disable writeback first, keep suppression sync read-only, and retain latest CRM sync snapshots.

---

### Task 6: Required Guard Promotion And Final Verification

**Files:**
- Modify: `.github/workflows/pr-scope.yml`
- Modify: `.github/workflows/thesis-golden-gate.yml`
- Modify: `.github/workflows/local-artifact-validation.yml`
- Modify: `docs/runbooks/split-and-merge.md`
- Create: `docs/approvals/control-plane-guard-promotion.md`

- [ ] **Step 6.1: Promote scope guard from advisory to required**

Remove `continue-on-error: true` from `PR Scope Guard` after at least one advisory run has passed on a representative PR.

- [ ] **Step 6.2: Confirm branch protection**

Make these checks required:

- `Local Artifact Validation`
- `SQLite Durability Smoke`
- `Thesis Golden Set Gate`
- `PR Scope Guard`
- `Core Regression Suite`

Add entity, relationship, and CRM specific checks if they are separate workflows; otherwise their tests remain inside `Core Regression Suite`.

- [ ] **Step 6.3: Run final verification sweep**

```powershell
python -m pytest tests/scripts/test_sqlite_snapshot.py tests/scripts/test_litestream_restore_verify.py tests/utils/test_db_guard.py -q
python -m pytest tests/scripts/test_run_thesis_llm_eval_gate.py tests/ci/test_detect_thesis_sensitive_changes.py tests/ci/test_check_thesis_gate_artifact.py -q
python -m pytest tests/ci/test_check_pr_scope.py tests/ci/test_detect_cross_pr_conflicts.py -q
python -m pytest tests/storage/test_entity_survivorship.py tests/api/test_merge_write_endpoints.py -q
python -m pytest tests/storage/test_relationship_store.py tests/storage/test_relationship_pathfinding.py tests/storage/test_relationship_path_cache.py -q
python -m pytest tests/enrichment/test_enrichment_cache.py tests/enrichment/test_rate_budget.py tests/workflows/test_notion_stage_policy.py -q
python scripts/ci/check_doc_artifacts.py docs
python scripts/lint_identity_patterns.py --check --baseline scripts/identity_lint_baseline.json --root .
git diff --check
```

- [ ] **Step 6.4: Create one intentional failing test PR**

Open a throwaway branch that edits a `read_only` path under `.pr-scope.yaml`. Confirm `PR Scope Guard` blocks it. Close the PR without merge.

- [ ] **Step 6.5: Commit guard promotion docs**

```powershell
git add .github/workflows/pr-scope.yml docs
git commit -m "ci: promote control-plane guardrails"
```

**Acceptance:** Required checks are visible on every PR; scope guard blocks known violations; documentation names rollback procedures and approval labels.

**Rollback:** Remove the newly required checks from branch protection before reverting workflow code.

---

## Approval Gate Matrix

| Action | Required approval | Enforcement point |
| --- | --- | --- |
| Restore backup into production | `db-restore-approved` plus runbook checklist | `docs/runbooks/sqlite-durability.md`, protected environment |
| Golden-set relabel | `thesis-label-drift-approved` | `scripts/ci/check_thesis_gate_artifact.py` |
| Baseline promotion | `baseline-promotion-approved` plus CODEOWNER review | `thesis-golden-gate.yml` |
| Entity merge apply | validated proposal, plus `entity-merge-approved` where policy requires it | `api/routers/merge_review.py`, `storage/entity_survivorship.py` |
| Notion stage downgrade | human edit or `notion-stage-downgrade-approved` | `workflows/notion_stage_policy.py` |
| Scope override | `scope-override-approved` | `scripts/ci/check_pr_scope.py` |
| Bulk external enrichment write | provider enabled, rate budget, preview, and `external-enrichment-write-approved` | `enrichment/rate_budget.py`, workflow label check |

## Definition Of Done For The Whole Stack

- SQLite restore is tested through temp restore and existing `scripts/restore_db.py` remains canonical.
- Thesis behavior is protected by a required golden-set gate that reuses the live fixture and manifest.
- Local-first artifacts live under `docs/`, not a parallel top-level `knowledge/` tree.
- PR scope guard is advisory before broad work and required after it proves stable.
- Merge API direction is validated before survivorship semantics land.
- Lexmin entity winner behavior is removed or quarantined behind explicit non-production tests.
- Survivorship decisions are field-level, auditable, and raw-value safe by default.
- Relationship pathfinding uses private graph storage and emits only privacy-safe badges.
- Enrichment uses shared TTL/cache/rate-budget controls in the existing `enrichment/` package.
- CRM sync is monotonic and preserves exact Notion status strings, especially `Dilligence`.
- Every PR includes tests, docs/runbook updates, rollback, and clean `git diff --check`.

## Self-Review

- Spec coverage: original risk areas are covered by Tasks 1, 2, 4b, 5b, 5c, and 3.
- Review findings integrated: green-field paths removed, Notion spelling fixed, SQL migration replaced with Python migration, thesis gate reuses existing assets, PR 6 moved earlier as Task 3.
- Remaining execution caveat: migration numbers must be rechecked immediately before implementation because `CURRENT_SCHEMA_VERSION` can move.
- Placeholder scan: no unresolved placeholder markers remain.
