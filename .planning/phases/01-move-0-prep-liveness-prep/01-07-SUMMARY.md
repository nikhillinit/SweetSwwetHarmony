---
plan: 01-07
phase: 01-move-0-prep-liveness-prep
status: complete
requirements: [REC-03]
commit: 0f1c266
---

# Plan 01-07 Summary — Track D CT-log + DNS shadow design doc

## Closes

- **REC-03** — Track D (CT-log + DNS shadow collector) design decision record

## What shipped

New file: `docs/plans/2026-04-06-red-team-hybrid/13-track-d-design.md` (238 lines, 6 sections).

The doc answers all 5 D-19 questions with concrete decisions:

1. **CT-log source**: `crt.sh` primary (free, aggregates all major public CT logs), `certspotter` fallback if rate-limited.
2. **DNS data source**: hybrid — CT SAN extraction + Python stdlib `socket.gethostbyname_ex()` for local resolution + Censys community tier (manual spot-check only, capped at 250 queries/month).
3. **Canonical key strategy**: two-tier with `domain:<root>` primary (when DNS resolves) and `ct_cert:<serial>` fallback (when DNS is not yet live), with `canonical_key_aliases` bridge to promote `ct_cert:*` → `domain:*` when DNS lights up.
4. **Anti-fingerprinting posture**: defensive — no direct Censys queries (their query logs are readable by targets), local DNS resolver only (no DoH), generic User-Agent, no correlation queries, no LinkedIn (per D-11).
5. **Cost envelope**: $0/month target, $50/month hard cap.

§6 explicitly enumerates the protected-paths blocked by Phase 1 freeze (`collectors/`, `storage/migrations/`, `workflows/`, `monitoring/`) and lists the Phase 3 implementation deliverables.

## Acceptance criteria

| Check | Expected | Actual |
|-------|----------|--------|
| `test -f 13-track-d-design.md` | exit 0 | exit 0 |
| `wc -l` | ≥120 | 238 |
| `grep -c "^## 1..6\."` (each header) | 1 each | 1 each (6 total) |
| `grep -ci "TBD"` | 0 | 0 |
| `grep -c "crt.sh"` | ≥3 | 16 |
| `grep -c "ct_cert:"` | ≥2 | 7 |
| `grep -c "2026-04-19"` | ≥2 | 5 |
| `grep -c "REC-03"` | ≥1 | 2 |
| `grep -ci "design only\|DOCS ONLY"` | ≥1 | 4 |
| `bash scripts/red-team-hybrid/check_protected_paths.sh` | rc=0 | rc=0 |
| Zero protected-path edits | yes | yes (only docs/) |

## Soft rubric gate 11 (Track D design doc): **GREEN**

Phase 3 implementer can now open this doc and start coding from a decided position, not a research position.

## Commits

- `0f1c266` — `feat(01-07): Track D CT-log + DNS shadow design doc (REC-03)`

## Execution note

The original gsd-executor agent (agent-a009ac6e) reported a checkpoint blocker: Bash was denied in its environment AND the worktree directory was apparently unmaterialized (missing `docs/plans/`, `.planning/`, `scripts/red-team-hybrid/` subtrees). The agent could not perform the worktree-base recovery (`git reset --hard`) that the other Wave 2 agents needed, nor run `git commit --no-verify`.

Recovery: orchestrator applied the literal plan template (lines 85-322 of `01-07-PLAN.md`, indented inside an action block) inline, ran the full acceptance-criteria battery in main worktree, and committed atomically. The empty `worktree-agent-a009ac6e` (which never had a working tree on disk) is removed as part of wave cleanup.

## Deviations from plan

None. The doc content was copied verbatim from `01-07-PLAN.md` lines 85-322 with the leading 4-space indent removed (the indent is the markdown code-fence indentation in the plan file, not file content).
