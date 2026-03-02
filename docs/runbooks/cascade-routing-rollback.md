# Cascade Routing Rollback Runbook

## 1. Overview

Cascade routing adds a **consumer rescue** path to the thesis filter. Signals that
score low on sector keywords but have strong consumer indicators (anchor keywords,
consumer-signal dominance over B2B soft penalties) can be rescued to QUALIFIED
instead of falling through to HELD.

**Modes:**

| Mode | Behavior |
|------|----------|
| `disabled` (default) | Legacy routing; no cascade logic runs |
| `shadow` | Both paths run; cascade result logged as counterfactual; legacy result returned |
| `live` | Cascade result returned; exception falls back to inline legacy |

**Kill switch:** `CASCADE_ROUTING_ENABLEMENT=disabled` — takes effect on next
pipeline run (no restart needed).

**Zero-change default:** Merging with `disabled` has zero behavior change.

---

## 2. Prerequisites

Before enabling cascade routing:

- [ ] Thesis regression suite green (`tests/utils/test_thesis_matcher.py`,
  `test_thesis_filter.py`, `test_thesis_filter_cascade.py`,
  `test_thesis_cascade_phase0.py`, `test_thesis_golden_set.py`,
  `test_negative_keyword_policy.py`, `test_cascade_routing.py`,
  `test_analyze_pipeline_thesis.py`)
- [ ] Golden set passing: `pytest tests/utils/test_thesis_golden_set.py -v`
- [ ] `config/phase_gates.yaml` present and parseable (runtime_controls.py
  enforces this — missing/corrupt file → cascade forced to `disabled`)
- [ ] Monitoring commands tested:
  - Shadow log query: `grep cascade_counterfactual logs/` returns results
  - SQL queries: `SELECT decision_path_code, COUNT(*) FROM thesis_classifications GROUP BY 1`
- [ ] Rollback tested in staging: set `CASCADE_ROUTING_ENABLEMENT=disabled`,
  run pipeline, verify zero `QUALIFY_CONSUMER_RESCUE` or `HOLD_B2B_GUARD_BLOCK`
  in output

---

## 3. Phase 1: Shadow Mode (72h minimum)

### Activation

```bash
CASCADE_ROUTING_ENABLEMENT=shadow
```

### What happens

Both legacy and cascade routing paths execute for every signal. The cascade
result is logged as a counterfactual; the legacy result is returned.

Log format (from `thesis_filter.py:430-440`):

```
cascade_counterfactual: legacy=<decision>/<code>, cascade=<decision>/<code>,
  consumer_signal=<float>, anchors=<int>, b2b_soft=<float>
```

### Shadow exit criteria (ALL required)

| Criterion | Threshold | How to check |
|-----------|-----------|--------------|
| Volume | >= 50 unique signals with shadow logs (3+ pipeline cycles) | `grep -c cascade_counterfactual logs/` |
| Divergence | >= 10 cases where cascade != legacy | Parse logs: compare legacy decision vs cascade decision |
| Source diversity | >= 3 distinct collectors represented | Cross-reference signal IDs with `signals.source_api` |
| Veto invariant | Zero cases where cascade qualifies a signal legacy vetoed via VETO_WEB3 or VETO_HARD_REJECT | Parse logs: no rows where legacy=rejected/veto_web3 AND cascade=qualified |
| cascade_exception | 0 | `grep -c cascade_exception logs/` |

### Rollback

```bash
CASCADE_ROUTING_ENABLEMENT=disabled
```

---

## 4. Phase 2: Gate Validation

After shadow exit criteria are met:

### Tests

```bash
# Golden set regression
pytest tests/utils/test_thesis_golden_set.py -v

# BtC/B2C collision tests
pytest tests/utils/test_cascade_routing.py -v

# Full cascade test suite
pytest tests/utils/test_thesis_filter_cascade.py -v
```

### Web3 gate analysis

Analyze shadow logs for `VETO_WEB3` paths. Confirm no false vetoes on
legitimate consumer companies. Specifically:

- Extract all signals where legacy path was `veto_web3`
- Verify cascade did NOT override the veto (veto invariant from Phase 1)
- Check for any consumer companies incorrectly flagged as web3

### Mark gate passed

Update `config/phase_gates.yaml`:

```yaml
web3_ambiguity_gate:
  status: passed
  passed_at: "YYYY-MM-DDTHH:MM:SSZ"  # actual timestamp
```

Without this, `runtime_controls.py` will force cascade back to `disabled`
even if the env var says `live`.

---

## 5. Phase 3: Live Activation

### Step 1: Dry-run first

```bash
CASCADE_ROUTING_ENABLEMENT=live python run_pipeline.py full --dry-run
```

Verify:
- [ ] `cascade_exception` count == 0
- [ ] `decision_path_code` distribution within +/-20% of shadow baseline
- [ ] Qualified count not exceeding 2x shadow-predicted

### Step 2: Go live

```bash
CASCADE_ROUTING_ENABLEMENT=live
```

Run pipeline without `--dry-run`.

### Operational guardrails (real-time, no labels needed)

| Metric | Warning | Critical / Action |
|--------|---------|-------------------|
| Queue growth (qualified count) | 1.5x baseline | 2.0x baseline -> disable cascade |
| LLM call volume | — | 1.2x baseline -> restrict to anchor-only LLM eligibility |
| Hard-veto invariant | — | Any crypto/web3 in qualified -> immediate disable |
| decision_path_code distribution | — | Any code > +/-20% of baseline -> investigate |

### Label-based guardrails (delayed)

| Metric | Threshold | Notes |
|--------|-----------|-------|
| Precision floor | `max(0.60, baseline_precision - 0.03)` on rolling N=200 | Minimum 50 samples before deciding |
| Breach trigger | 2 consecutive windows below floor | Hold ramp level if insufficient samples |

### cascade_exception trigger

If `cascade_exception` rate exceeds 0.1% for 1 hour -> auto-disable
(set `CASCADE_ROUTING_ENABLEMENT=disabled`).

---

## 6. Recovery Protocol

If cascade is disabled due to a guardrail breach:

1. **Log root cause** — which metric or invariant triggered the disable
2. **Fix the issue** — code fix, threshold adjustment, or config correction
3. **Verify stability** — run 2 consecutive clean pipeline cycles at `disabled`
4. **Re-enable shadow** — set `CASCADE_ROUTING_ENABLEMENT=shadow` for 3 pipeline
   cycles (canary-level re-ramp)
5. **Verify shadow logs clean** — all exit criteria still met, no new exceptions
6. **Re-enable live** — set `CASCADE_ROUTING_ENABLEMENT=live`
7. **3+ failure cycles** — require incident review before re-attempt. Document
   root cause, fix, and validation in `docs/plans/` before trying again.

---

## 7. Kill Switch

```bash
CASCADE_ROUTING_ENABLEMENT=disabled
```

- Takes effect on the **next pipeline run** (no restart needed)
- No data loss — signals already routed are unaffected
- Phase gate enforcement (`runtime_controls.py`) also forces disabled if
  `web3_ambiguity_gate` is not `passed`

### Verification

After setting disabled, run 1 pipeline cycle and confirm:

```bash
# Should return zero results
grep -E "QUALIFY_CONSUMER_RESCUE|HOLD_B2B_GUARD_BLOCK" logs/latest.log
```

Also verify via kill-switch propagation: 3 consecutive pipeline events show
zero cascade decision codes.

---

## 8. ADJACENT Lifecycle (ADR-5)

### What is ADJACENT?

The `ADJ` (ADJACENT) label in `signal_quality_metrics.human_label` and
`quality_feedback.label` identifies signals that are near-thesis but not
a direct fit. These are distinct from `OFF_THESIS`.

### Invariant

**ADJACENT remains semantically distinct from OFF_THESIS.** Operational cleanup
(archival, batch rejection, etc.) must never silently relabel ADJ signals.

### Monitoring

```sql
-- Adjacent rate (last 30 days)
SELECT
  COUNT(*) FILTER (WHERE human_label = 'ADJ') * 100.0 / COUNT(*) AS adjacent_rate
FROM signal_quality_metrics
WHERE labeled_at >= date('now', '-30 days');

-- Adjacent backlog age p95
SELECT human_label, MAX(julianday('now') - julianday(labeled_at)) AS age_days
FROM signal_quality_metrics
WHERE human_label = 'ADJ'
GROUP BY human_label;
```

### Auto-archival

Auto-archival of stale ADJACENT signals (`ARCHIVED_HELD`) is deferred to
Phase 6. Until then, ADJACENT signals remain in the review queue indefinitely.

---

## 9. Troubleshooting

### Shadow logs not appearing

- Verify env var: `echo $CASCADE_ROUTING_ENABLEMENT` should be `shadow`
- Verify log level: must be INFO or lower (not WARNING)
- Check that pipeline is actually classifying signals (not all deduped)

### cascade_exception in logs

- Extract the error message from `event=cascade_exception, error=<msg>`
- Reproduce with golden set: `pytest tests/utils/test_thesis_golden_set.py -v`
- Disable cascade until root cause is identified and fixed

### Queue depth growing unexpectedly

- Disable cascade: `CASCADE_ROUTING_ENABLEMENT=disabled`
- Review rescue threshold — consider raising from 0.25 to 0.30+
- Check if a specific collector is producing many borderline signals
- Verify `consumer_dominance_margin` (default 0.10) is not too permissive

### Gate file missing or corrupt

- Phase gate enforcement falls back to `disabled` (fail-closed behavior in
  `runtime_controls.py:_enforce_phase_gates`)
- Check YAML syntax: `python -c "import yaml; yaml.safe_load(open('config/phase_gates.yaml'))"`
- Verify `web3_ambiguity_gate.status` is `passed` (not `pending` or `failed`)

### Malformed scores (NaN/Inf)

- `max(b2b_soft_score, 0.01)` in dominance calculation prevents division by zero
- If NaN propagates from upstream (e.g., corrupt ThesisFit), the cascade path
  will produce a non-deterministic result → caught by exception fallback in
  `_resolve_cascade_routing`
- Disable cascade, file bug, add regression test before re-enabling

### Post-shadow golden set regression

- Review failing golden set cases individually
- Determine if the failure is from an intentional config change (threshold
  adjustment) or an actual bug
- If intentional: update golden set expectations
- If bug: fix and re-run shadow before proceeding

---

## 10. Reference

### Environment variables

| Variable | Values | Default | Source |
|----------|--------|---------|--------|
| `CASCADE_ROUTING_ENABLEMENT` | disabled / shadow / live | disabled | `ThesisFilterConfig.from_env()` |
| `THESIS_HOLD_THRESHOLD` | float | 0.3 | Sector score cutoff |
| `THESIS_SKIP_LLM_BELOW` | float | 0.2 | Skip LLM if obvious non-fit |
| `THESIS_CONSUMER_RESCUE_THRESHOLD` | float | 0.25 | Min consumer score for rescue |
| `THESIS_CONSUMER_ANCHOR_MIN` | int | 1 | Min anchor keyword matches |
| `THESIS_CONSUMER_DOMINANCE_MARGIN` | float | 0.10 | Consumer - B2B margin |
| `THESIS_SIGNAL_RATIO_MIN` | float | 2.0 | Consumer / B2B ratio alternative |

### Decision path codes

| Code | Routing | Description |
|------|---------|-------------|
| `VETO_WEB3` | REJECTED | Web3/crypto pre-check fired |
| `VETO_DOMAIN_BLACKLIST` | REJECTED | Domain on blacklist |
| `VETO_HARD_REJECT` | REJECTED | Hard reject keyword matched |
| `HOLD_HARD_HOLD` | HELD | Hard hold keyword (never auto-qualify) |
| `QUALIFY_SECTOR` | QUALIFIED | Sector score above threshold |
| `QUALIFY_CONSUMER_RESCUE` | QUALIFIED | Cascade: consumer signal rescued |
| `HOLD_B2B_GUARD_BLOCK` | HELD | Cascade: consumer signal present but B2B dominant |
| `HOLD_DEFAULT` | HELD | Low score, no rescue path available |

### Key files

| File | Purpose |
|------|---------|
| `utils/thesis_filter.py` | ThesisFilter, routing logic, cascade wiring |
| `utils/thesis_matcher.py` | Keyword matching, consumer/B2B scoring |
| `utils/runtime_controls.py` | Phase gate enforcement, config resolution |
| `config/phase_gates.yaml` | Gate status for live activation |
| `tests/utils/test_thesis_golden_set.py` | 56-case golden set regression |
| `tests/utils/test_thesis_filter_cascade.py` | Cascade routing unit tests |
| `tests/utils/test_cascade_routing.py` | BtC/B2C collision + Phase 3 calibration |

### Related runbooks

- [Feature Activation](feature-activation.md) — Progressive feature flag activation
- [Phase G Activation](phase-g-activation.md) — Entity identity resolution rollout
