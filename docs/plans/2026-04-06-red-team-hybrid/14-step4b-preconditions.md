# Step 4B Preconditions and Governance Gate Freshness Contract

**Date:** 2026-04-08
**Status:** Spec — implemented in code in Phase 2 (after 2026-04-19 unfreeze)
**REQ:** LIV-03 (regret check precondition) + GOV-03 (gate contract for all governance gates)
**Resolves:** R19 root cause + R20 interim mitigation surface for the governance lane

---

## 1. The contract

Every governance gate (Step 4B regret check, canary runs, drift alerts,
future state-promotion gates) requires `max(detected_at) < 5 days` for
**at least 3 of the 4 operational collectors** over the prior 7 days,
BEFORE the gate evaluates its primary condition.

The 4 operational collectors (locked in `scripts/red-team-hybrid/freshness_watchdog.py`
`DEFAULT_OPERATIONAL_COLLECTORS`) are:

- `hacker_news`
- `arxiv`
- `rss_feeds`
- `news_api`

If fewer than three of these four collectors are fresh within the 5-day window,
the governance gate MUST NOT evaluate its primary condition. See §4 for
blocking vs advisory behavior per gate.

---

## 2. Verification command

The freshness precondition is verified by `scripts/red-team-hybrid/freshness_watchdog.py`,
which is the LIV-02 deliverable already shipped in commit `4efe8cf`:

```bash
python scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 120
```

The `--threshold-hours 120` flag sets the 5-day window (120 hours = 5 days)
required by the gate contract. The default threshold of 36 hours is the
operational-monitoring threshold, not the gate-precondition threshold —
the two thresholds intentionally differ because the gate tolerates more
latency than the day-to-day freshness alert.

### Exit code semantics

| Exit code | Meaning | Gate action |
|-----------|---------|-------------|
| `0` | All 4 operational collectors fresh within the 120h window (≥3 minimum satisfied by 4/4) | **Proceed** — gate evaluates its primary condition |
| `1` | At least one operational collector stale (check `failures[]` array for which ones) — if fewer than 3 remain fresh, precondition fails | **Postpone** per LIV-03 escalation; see §5 |
| `2` | Operational error (DB unreadable, schema mismatch, freshness_watchdog.py internal failure) | **Escalate** — do NOT silently pass; this is not a fail-open case |

Gate evaluators MUST treat exit code `2` as a hard escalation, not a
fail-open default. A fail-open on operational errors would reintroduce
R19's silent-failure mode at the gate layer.

---

## 3. Required precondition input format

Every gate evaluator MUST capture the freshness watchdog JSON output as
part of the gate's `audit_events` row. The schema matches
`scripts/red-team-hybrid/freshness_watchdog.py` `render_json()`:

```json
{
  "checked_at": "<ISO 8601 timestamp>",
  "threshold_hours": 120,
  "exit_code": 0,
  "status": "OK",
  "collectors": [
    {
      "source_api": "hacker_news",
      "max_created_at": "<ISO 8601>",
      "age_hours": 0.12,
      "state": "FRESH"
    }
  ],
  "failures": []
}
```

The `status` field is one of `OK`, `STALE`, or `ERROR`. The `failures`
array lists the source_api strings that failed the threshold, empty if
all pass. Phase 2 implementation stores this blob alongside the gate's
pass/fail decision so that post-hoc analysis can always see "what freshness
state was the gate evaluated against".

---

## 4. Blocking vs advisory behavior

| Gate | Phase 1 (this doc) | Phase 2 (after unfreeze) |
|------|--------------------|----------------------------|
| Step 4B regret check (2026-04-18) | **BLOCKING** — postpones if freshness fails per LIV-03 escalation | **BLOCKING** — same |
| Daily canary runs | ADVISORY — log warning, do not block | **BLOCKING** after 30 days zero false-positives |
| SPC drift alerts | ADVISORY — alerts gate on freshness state | **BLOCKING** after Phase 2 ships the precondition-wrapper |
| Future state-promotion gates | DOCS-ONLY in Phase 1 | **BLOCKING** in Phase 2 |

The Step 4B regret check is the gate this contract exists for. The other
gates inherit the contract by reference — the blocking semantics are
phased in over Phase 2 to avoid breaking existing monitoring cadence
during the Move 0.5 liveness ship.

---

## 5. Failure escalation path

1. Freshness watchdog returns exit code `1` → **automated postpone** of
   the gate (no human action required for the postpone itself).
2. Postpone event written to `audit_events` with reason
   `freshness_failure`, referencing the watchdog JSON output.
3. `.planning/STATE.md` updated with the postpone event on the same day
   per D-34's day-by-day gate evaluation requirement.
4. Next regret check date computed as `now + 5 days` (the freshness
   window — giving enough time for the pipeline to catch up).
5. If freshness still fails after **3 consecutive postpone cycles**,
   escalate to human review for root-cause investigation. Do NOT
   auto-postpone indefinitely — that would silently convert R19 back
   into a permanent freeze of governance.

### Error path (exit code 2)

1. Freshness watchdog returns exit code `2` → immediate human escalation.
2. Error detail logged to `artifacts/governance-gate-errors/<date>.json`.
3. Gate does NOT evaluate; gate does NOT postpone on a timer. It waits
   for human intervention.
4. Phase 1 has no automation for this path — if exit code 2 fires between
   2026-04-08 and 2026-04-19, the human-in-the-loop path is the only
   remediation until Phase 2 ships better tooling.

---

## 6. Gates this contract applies to

- **Step 4B regret check** (governance event #21, due 2026-04-18) —
  THE PRIMARY GATE THIS CONTRACT EXISTS FOR. This is the load-bearing
  use case. Phase 1 ships this contract specifically to prevent the
  2026-04-18 check from evaluating against silently-frozen data.
- **Daily canary runs** (Phase 2+) — ADVISORY in Phase 1, BLOCKING in
  Phase 2. Canary runs benefit from freshness context even when not
  blocked by it.
- **SPC drift alerts** (Phase 2+) — ADVISORY in Phase 1, BLOCKING in
  Phase 2 once the precondition-wrapper ships.
- **Future state-promotion gates** (Phase 2+) — any new gate added after
  2026-04-19 MUST inherit this contract as a BLOCKING precondition by
  default.

---

## 7. Phase 2 implementation hand-off

Phase 2 implements this contract in the `governance/` package, which is
currently a protected path (Move 0 protected-paths list, unfreezes
2026-04-19). Specifically:

- **`governance/cli.py`** — adds a `--require-freshness` flag (or equivalent
  subcommand wrapping) that runs the freshness watchdog before evaluating
  any gate. Default: `True` for Step 4B regret check, `False` (advisory)
  for canaries and drift alerts until the 30-day zero-FP soak completes.
- **`governance/state_policies.py`** — adds a `freshness_precondition`
  field to the policy schema. Default `True` for gates listed in §6;
  explicit `False` required (with audit rationale) to disable.
- **`audit_events`** schema — gains a `precondition_audit_json` column,
  OR a new `gate_preconditions` child table keyed on `audit_event_id`.
  Decision deferred to Phase 2 day 1 — the schema change touches
  `storage/migrations/` (also protected until 2026-04-19).

**These code changes are OUT OF SCOPE for Phase 1** per CONTEXT.md D-20
and D-21. Phase 1 ships THIS DOCUMENT and nothing else in governance.
Phase 2 ships the code. Any Phase 1 plan proposing an edit to
`governance/*.py` or `storage/migrations/` is rejected by
`scripts/red-team-hybrid/check_protected_paths.sh`.

---

## 8. Link back to R19 and R20

This contract exists because of R19 and R20 in
`docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md`:

- **R19 (frozen pipeline)** — this contract **closes R19** by ensuring
  no governance gate can pass against silently-frozen data. R19 is
  root-caused by "no scheduled collection + no freshness verification
  at gate-time". LIV-01 restarted the pipeline; LIV-02 shipped the
  watchdog; the keep-alive installer (CONTEXT.md D-22) makes collection
  scheduled; THIS CONTRACT adds the gate-time verification.
- **R20 (analyst abandonment)** — this contract is part of the Phase 1
  **interim mitigation suite** per CONTEXT.md D-29 by removing the
  analyst from the freshness critical path for governance gates. The
  analyst does not have to remember to check freshness before the
  2026-04-18 regret check — the gate itself checks, postpones on
  failure, and logs the decision to `audit_events` + `STATE.md` without
  analyst involvement.

See `10-risk-register.md` R19 and R20 rows for full context. See
`.planning/REQUIREMENTS.md` LIV-03 and GOV-03 for the originating REQ
definitions.

---

*Phase: 01-move-0-prep-liveness-prep*
*REQ: LIV-03 + GOV-03*
*Status: Phase 1 docs-only spec; Phase 2 implements the code*
