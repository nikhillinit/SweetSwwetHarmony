# Branch Protection Setup (Repository Ruleset)

Main-branch protection for `nikhillinit/SweetSwwetHarmony` is enforced by an
**active repository ruleset** (id `12778551`, name "Core Regression Suite"),
NOT the legacy branch-protection API. Do not use
`gh api repos/.../branches/main/protection` -- that legacy endpoint is a
separate mechanism and does not reflect or modify the active ruleset.

All commands below are `gh` CLI and require `admin` access to the repo.
Applying or rolling back the ruleset is **operator-gated**: only run the
mutating (`--method PUT`) commands with explicit operator approval.

## Current state

Inspect the live ruleset (read-only):

```bash
# List rulesets (find the id)
gh api repos/nikhillinit/SweetSwwetHarmony/rulesets

# Show the active default-branch ruleset in full
gh api repos/nikhillinit/SweetSwwetHarmony/rulesets/12778551
```

### Parity check (automated)

Diff the live ruleset against this runbook's required-checks table (plus
enforcement and the strict up-to-date policy) with:

```bash
python -m scripts.ci.check_ruleset_parity          # human output
python -m scripts.ci.check_ruleset_parity --json   # machine-readable
```

Exit 0 = parity, 1 = drift, 2 = fetch/parse error. The script parses the
table under "Required status checks" below, so keep that table authoritative
when the check set changes. Contract tests:
`tests/ci/test_ruleset_parity_check.py`.

The exact JSON captured on 2026-07-10 (before the Q1 change) is stored at
`docs/runbooks/evidence/ruleset-prior-20260710.json`. At that point the
ruleset enforced: deletion protection, non-fast-forward protection, and a
single strict required status check (`Core Regression Suite`).

## Target configuration

The proposed ruleset (`docs/runbooks/evidence/ruleset-proposed-20260710.json`)
keeps the ruleset targeted at `~DEFAULT_BRANCH` with `enforcement: active` and
requires:

1. **Pull requests before merging** (`pull_request` rule;
   `required_approving_review_count: 0` -- solo-maintainer repo, so a PR is
   required but an approving review is not, which would otherwise deadlock
   merges since authors cannot approve their own PRs).
2. **Required status checks, strict/up-to-date policy**
   (`strict_required_status_checks_policy: true` -- PR branches must be up to
   date with `main` before merging).
3. **Deletion protection** (`deletion` rule).
4. **Non-fast-forward protection** (`non_fast_forward` rule -- blocks force
   pushes).

### Required status checks (exactly seven)

Check contexts must match the names CI reports on PRs (job `name:` values, as
surfaced by the status API), not workflow file names. Verified against
PR #292 `statusCheckRollup` on 2026-07-10:

| Required check context | Workflow file | Workflow `name:` |
|---|---|---|
| Core Regression Suite | `.github/workflows/regression-gate.yml` | Regression Gate |
| Docker Build & Smoke | `.github/workflows/regression-gate.yml` | Regression Gate |
| PR Evidence Gate | `.github/workflows/pr-evidence.yml` | PR Evidence Gate |
| Thesis Golden Set Gate | `.github/workflows/thesis-golden-gate.yml` | Thesis Golden Set Gate |
| SQLite Durability Smoke | `.github/workflows/sqlite-durability-smoke.yml` | SQLite Durability Smoke |
| Hermes Ledger Audit | `.github/workflows/hermes-ledger-audit.yml` | Hermes Ledger Audit |
| Local Artifact Validation | `.github/workflows/local-artifact-validation.yml` | Local Artifact Validation |

All seven run on every PR to `main` (no trigger-level path filters), so they
are safe to require universally. `integration_id: 15368` is GitHub Actions.

Note: `PR Evidence Gate` is always-on by design -- it decides *in-job* whether
evidence is required for the changed paths, so it always reports a status and
never deadlocks a PR as a required check.

> **Note (path-filtered canaries):** The Dry-Run Immutability Canary is
> path-filtered (only fires on `workflows/`/`storage/` PRs). GitHub required
> checks cannot mark path-filtered checks as required for all PRs -- a
> required-but-skipped check never reports and deadlocks the merge. Keep it
> (and any future path-filtered canary) OUT of the required checks list.
> Instead, enforce it via the label `dry-run-immutability-approved` for any PR
> that modifies those paths without a green canary.

## Applying the ruleset (operator-gated)

From the repo root, with the proposed JSON reviewed and approved:

```bash
gh api repos/nikhillinit/SweetSwwetHarmony/rulesets/12778551 \
  --method PUT \
  --input docs/runbooks/evidence/ruleset-proposed-20260710.json
```

### Verify after applying

```bash
gh api repos/nikhillinit/SweetSwwetHarmony/rulesets/12778551 \
  --jq '[.rules[] | select(.type == "required_status_checks")
         | .parameters.required_status_checks[].context] | .[]'
```

Expected output (one per line, order not significant):

```
Core Regression Suite
Docker Build & Smoke
PR Evidence Gate
Thesis Golden Set Gate
SQLite Durability Smoke
Hermes Ledger Audit
Local Artifact Validation
```

Also confirm the rule types and strict policy:

```bash
gh api repos/nikhillinit/SweetSwwetHarmony/rulesets/12778551 \
  --jq '{enforcement, rules: [.rules[].type],
         strict: [.rules[] | select(.type == "required_status_checks")
                  | .parameters.strict_required_status_checks_policy]}'
```

Expected: `enforcement` = `active`; rules include `deletion`,
`non_fast_forward`, `pull_request`, `required_status_checks`; strict = `true`.

## Rollback

The pre-change state is preserved verbatim in
`docs/runbooks/evidence/ruleset-prior-20260710.json`. That file includes
read-only fields (`id`, `node_id`, `_links`, timestamps, etc.) that must be
stripped before it can be sent back in a PUT:

```bash
jq '{name, target, enforcement, conditions, bypass_actors, rules}' \
  docs/runbooks/evidence/ruleset-prior-20260710.json \
  > /tmp/ruleset-rollback.json

gh api repos/nikhillinit/SweetSwwetHarmony/rulesets/12778551 \
  --method PUT \
  --input /tmp/ruleset-rollback.json
```

Then re-run the verification queries above; the required checks list should
contain only `Core Regression Suite`.

## Evidence files

| File | Purpose |
|---|---|
| `docs/runbooks/evidence/ruleset-prior-20260710.json` | Exact ruleset JSON before the Q1 change (rollback source) |
| `docs/runbooks/evidence/ruleset-proposed-20260710.json` | PUT-ready payload with the seven required checks |
