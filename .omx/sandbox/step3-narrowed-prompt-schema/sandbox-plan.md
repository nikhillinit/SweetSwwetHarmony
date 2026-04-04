# Sandbox Implementation Plan — Step 3 Narrowed Prompt/Schema Rollout

Purpose: prototype the eval-gated Step 3 rollout in a sandbox lane before integrating validated improvements into the main workspace.

## Guardrails
- Only implement gate-authorized changes from `.omx/specs/thesis-llm-eval-gate.json`.
- Do not ship all original v1.5 decomposition/shadow fields.
- Preserve Step 1 truthfulness/routing behavior.
- Additive schema changes only.

## Candidate minimum structured fields
- `primary_end_user`
- `paying_customer`
- `sells_to_or_operates_in`

Rationale: these directly support the B2B-in-disguise question without pulling in the full original field set.

## Sandbox validation goals
1. Prompt guidance cleanly distinguishes sells-tools-to-industry vs operates-in-industry.
2. Minimum fields parse cleanly with defaults.
3. Storage/pipeline persistence remains additive and aligned.
4. Focused tests prove routing/reporting usefulness without broad schema sprawl.
5. Any improvement surfaced in sandbox is written back into the integration brief before main-branch edits.
