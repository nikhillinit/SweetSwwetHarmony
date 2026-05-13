# ADR 0003: Architecture Lint Ratchet

Status: Proposed
Date: 2026-05-11

## Context

The refactor roadmap has known hazards that should not expand while cleanup is
sequenced:

- async middleware uses a synchronous `threading.Lock`;
- body `actor` can be used as authority in actions;
- empty service modules can appear as completed architecture;
- placeholder model perspectives can look like real consensus;
- direct `HTTPException` use should stay near API boundaries.

## Decision

Add a static architecture lint in ratchet mode. Existing violations are recorded
in a baseline. New violations fail in changed files, and touched files should
shrink baseline entries when they are fixed.

## Consequences

- The first lint branch can land without rewriting runtime code.
- Cleanup can proceed incrementally without allowing backslide.
- The baseline file becomes an explicit debt register for refactor hazards.

## Acceptance Criteria

- Ratchet rules cover the Sprint 0 static-check list.
- The baseline records current file-level violations.
- CI fails any new violation outside the baseline.
- Baseline updates require a short rationale in the PR or commit message.
