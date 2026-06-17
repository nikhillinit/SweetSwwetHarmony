# Branch Protection Setup

Run these `gh` CLI commands once to lock in required status checks. Requires `admin` scope on the repo.

```bash
# List current required status checks (verify before changing)
gh api repos/nikhillinit/SweetSwwetHarmony/branches/main/protection \
  --jq '.required_status_checks.contexts'

# Enable required status checks — run once from a machine with gh auth login (admin)
gh api repos/nikhillinit/SweetSwwetHarmony/branches/main/protection \
  --method PUT \
  --field required_status_checks='{"strict":true,"contexts":["Core Regression Suite","Docker Build & Smoke","Thesis Golden Set Gate","SQLite Durability Smoke","Hermes Ledger Audit","Local Artifact Validation"]}' \
  --field enforce_admins=false \
  --field required_pull_request_reviews=null \
  --field restrictions=null
```

> **Note:** The Dry-Run Immutability Canary is path-filtered (only fires on `workflows/`/`storage/` PRs). GitHub branch protection cannot mark path-filtered checks as required for all PRs. Instead, enforce it via the label `dry-run-immutability-approved` for any PR that modifies those paths without a green canary.

## Verifying required checks are set

```bash
gh api repos/nikhillinit/SweetSwwetHarmony/branches/main/protection \
  --jq '.required_status_checks.contexts[]'
```

Expected output (one per line):
```
Core Regression Suite
Docker Build & Smoke
Thesis Golden Set Gate
SQLite Durability Smoke
Hermes Ledger Audit
Local Artifact Validation
```
