# Family Mode Runbook

Date: 2026-04-08
Status: pre-freeze operator runbook for refined Move 1 family controls

## Purpose

Run the refined Move 1 channel families in a controlled sequence without violating the Move 0 protected-path freeze or the Move 0.5 activation gate.

## First-Wave Families

- Required first new family: `ct_dns`
- Conditional first-wave family: `founder_aux`
- Reserved family: `pattern_search`

## Pre-Freeze Work Only

Before 2026-04-19, only do:

1. packet contract updates
2. packet JSON fixtures
3. Track E readiness artifacts
4. family-mode contract and validation prep

Do not do:

1. collector implementation
2. migration work
3. packet runtime owner changes
4. any protected-path edits

## Post-Freeze Activation Sequence

### Step 1: Confirm liveness gate

Required evidence:

```powershell
python scripts/red-team-hybrid/freshness_watchdog.py --json
```

Pass:
- exit code `0`
- all operational collectors report `FRESH`

Also require:
- 7 consecutive digest emissions
- at least one empty-but-fresh emission
- daily `analyst_inbox_engagement_7d` publication

### Step 2: Confirm packet/runtime readiness

Required evidence:

```powershell
pytest tests/workflows/test_cross_channel_packet_contract.py -q
pytest tests/workflows/test_packet_review_projection.py -q
```

Pass:
- packet contract validates
- `review_items.evidence_bundle` is the runtime owner
- digest/dashboard/Why Now are derived projections

### Step 3: Confirm family-mode controls

Required evidence:

```powershell
pytest tests/workflows/test_channel_family_modes.py -q
```

Pass:
- `CHANNEL_FAMILY_*_MODE` semantics hold for `disabled|shadow|active`

### Step 4: Activate `ct_dns`

`ct_dns` is the first required new family.

### Step 5: Evaluate `founder_aux`

Check:
- `data/shadow/founder_watchlist.csv`
- `data/shadow/cross-channel-signal-surface/track-e-readiness.json`

Pass:
- readiness artifact reports `first_wave_eligible=true`

Fail:
- keep `founder_aux` auxiliary
- do not delay `ct_dns`

## Rollback / Disable Rules

- If liveness gate fails, do not activate any protected-path family.
- If packet contract validation fails, keep all new families out of active mode.
- If Track E readiness fails, keep `founder_aux` auxiliary and continue with `ct_dns`.

## Validation Checklist

- [ ] liveness gate green
- [ ] packet contract validated
- [ ] runtime owner validated
- [ ] family-mode controls validated
- [ ] Track E readiness explicitly passed or failed
- [ ] no outreach / CRM behavior in the refined Move 1 first wave

## Failure Modes

### Track E below threshold

Expected outcome:
- `founder_aux` remains auxiliary
- `ct_dns` remains the first required new family

### Packet contract drift

Expected outcome:
- stop rollout
- update `evidence-packet-contract.md`
- regenerate fixtures

### Family-mode ambiguity

Expected outcome:
- do not improvise with existing write-feature flags
- finish explicit family-mode implementation first

---

*This runbook is for the refined Move 1 first wave only.*
