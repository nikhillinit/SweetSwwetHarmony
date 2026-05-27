---
type: runbook
status: active
owner: codex
created_at: 2026-05-27
related_prs: []
related_files:
  - docs/specs
  - docs/incidents
  - docs/evals
  - docs/approvals
---
# Local-First Agent Memory

## Purpose

Use repository-local docs artifacts for durable project knowledge that should survive beyond one agent session. Keep temporary notes, private preferences, and scratch observations out of the repo unless they become part of an approved plan, incident, eval update, approval record, ADR, or runbook.

## Artifact Types

- Specs: `docs/specs/`
- Incidents: `docs/incidents/`
- Eval updates: `docs/evals/`
- Approvals: `docs/approvals/`
- ADRs: `docs/decisions/`
- Runbooks: `docs/runbooks/`

## Procedure

Create artifacts with `python scripts/create_doc_artifact.py <type> "<title>"`. Generated artifacts must keep YAML front matter, use ASCII-safe required keys, and avoid Obsidian-only links or top-level `knowledge/` paths.

## Verification

Run `python scripts/ci/check_doc_artifacts.py docs` before staging artifact changes. CI also runs this validator through the `Local Artifact Validation` workflow.

## Rollback

Revert the artifact-producing commit. If a workflow has already been made required in branch protection, remove that requirement before reverting the workflow file.
