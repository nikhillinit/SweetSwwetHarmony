# DB Hardening Remaining Delta: RALPLAN Delta Spec

Date: 2026-04-05
Status: approved consensus plan

## Final Consensus

The next DB hardening task is a minimal-diff remaining-delta pass, not a replay of the April 4 tranche-1 plan.

Approved shape:
1. create a new delta-only PRD/test-spec pair as the active execution source of truth
2. supersede the stale April 4 execution artifacts for execution purposes
3. freeze already-landed tranche-1 mechanics as closed inventory unless reconciliation finds a concrete bug
4. limit remaining work to:
   - restore CLI-contract normalization already validated in sandbox
   - the targeted restore-contract guardrail already validated in sandbox
   - narrow docs/tests finish-up

## Why This Plan, Not A Bigger One

Current repo reality no longer supports a full tranche-1 hardening plan. Most of that work already landed. The real risk is stale execution guidance plus a small remaining interface/guardrail delta.

## Key Changes From The April 4 Plan Family

1. Replaces tranche-1-as-open framing with a closed-inventory table.
2. Treats restore sidecar logic as landed and closed unless a defect is found.
3. Narrows the open restore question to CLI contract only.
4. Uses a targeted restore-contract guardrail instead of broad CI widening.
5. Makes the new PRD/test-spec pair the only active execution artifacts.

## Active Artifacts

- `.omx/plans/prd-db-hardening-remaining-delta.md`
- `.omx/plans/test-spec-db-hardening-remaining-delta.md`

## Superseded For Execution

- `.omx/plans/prd-db-hardening-followup.md`
- `.omx/plans/test-spec-db-hardening-followup.md`
- `.omx/plans/db-hardening-followup-delta-spec-ralplan.md`

These remain historical context only.

## Execution Handoff

Recommended:
- `$ralph` for one bounded owner and a minimal-diff verification loop

Alternative:
- `$team` only if the user wants parallel lanes despite the small delta

## Verification Focus

1. new artifacts clearly supersede stale execution guidance
2. restore behavior matches the sandbox-validated shared-helper contract
3. CI scope stays bounded to the existing priority-script guardrail plus the targeted restore-contract test
4. no closed tranche-1 inventory is reopened without a concrete defect
