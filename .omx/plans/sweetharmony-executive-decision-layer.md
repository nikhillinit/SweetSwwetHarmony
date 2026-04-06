# SweetSweetHarmony Executive Decision Layer

This document is the controlling front door for the current SweetSweetHarmony strategy decision. The long scrutiny at [transient-hugging-manatee.md](C:\Users\nikhi\.claude\plans\transient-hugging-manatee.md) remains the analytic source-of-record. The evidence table for this executive layer is [sweetharmony-evidence-table.md](C:\dev\Harmonic\.omx\plans\sweetharmony-evidence-table.md).

## Verdict

`Conditional go`.

The current scrutiny document only partially achieves its stated goal. It is strong at finding hardening needs and surfacing strategy tensions, but weak as a front-door decision artifact because the main body accumulates multiple revised bottom lines over time. Keep it as the analytic source-of-record, and use this executive layer as the canonical recommendation surface.

Current as of `2026-04-06`. The current implementation refresh found that Gate A repair is already complete in repo state and that the active branch is `No routing problem detected`. Revalidate this front door after the `2026-04-18` regret check resolves or if a later diagnostic materially changes.

## Canonical Recommendation

Use SweetSweetHarmony to improve startup-team identification under a broad-intake-then-prune model, but only through a guarded sequence:

1. Fix the governance/calendar prerequisites first.
2. Use the current `2026-04-06` diagnostic summary, anchored to the `2026-02-28` baseline framing, to select the branch of the three-state tree.
3. Only if the refreshed diagnostic supports an expansion branch, loosen pre-review stages to increase learning volume.
4. Keep post-review thresholds and CRM delivery strict.

Pursue it only as a gated pre-review expansion program, not as blanket downstream loosening.

## Why This Container

Choose an executive decision layer plus source-of-record, not a short memo plus appendix:

- The live decision surface still contains date-bound sequencing and governance constraints that materially affect execution order.
- Several later sections replace earlier recommendations for substantive reasons, not for cosmetic ones.
- A pure memo-plus-appendix shape would bury active logic and recreate the "appendix graveyard" problem the source now identifies.

## Governing Rules

### 1. Optimize for detection quality, with enough rigor to protect it

The target is better surfacing of credible startup teams worth contacting, not a more elegant analytical artifact. But the rigor that preserves sequence, branch logic, and gating still matters because it protects detection quality. `LOB.txt` remains idea/provenance support only; it does not control the decision surface. See [LOB.txt:1](C:\dev\Harmonic\docs\plans\LOB.txt:1) through [LOB.txt:30](C:\dev\Harmonic\docs\plans\LOB.txt:30).

### 2. Broad intake, then prune

- Intake can be broad.
- Human review is the noise sink.
- Review quality is measured by contact-worthiness, not general interestingness.

Current working calibration to validate:

- `K = 200-500` reviewable signals per cycle
- `~10%` contact-worthy floor after review, treated as a working assumption rather than a proved baseline
- `50/session` labeling cap
- `>=10` surfaced candidates by `2026-05-03` as the first deal-quality milestone

These numbers are good enough to govern the current branch, but they should be validated as the queue runs rather than treated as settled truth.

`>=10` surfaced candidates by `2026-05-03` is derived from the current working queue calibration, not independent of it. If `K` or the observed post-review yield shifts materially, recalculate the milestone instead of treating it as fixed truth.

### 3. Loosen only pre-review

This is the most important operational rule.

- Loosen stages `1-6` freely enough to improve learning and candidate surfacing.
- Keep stage `7` strict: `HIGH_CONFIDENCE_THRESHOLD = 0.7`.
- Keep stage `8` strict: Notion/CRM remains a clean downstream surface, not the place where broad-intake noise lands.

The correct interpretation of broad intake is "more volume into the human-filtered review queue," not "more volume into the human-bypassed CRM." See sections `14.10.2` through `14.10.6` in the source-of-record scrutiny.

#### 3.1 Loosening Guardrails

These near-misses sound like loosening, but they are actually wrong because they bypass or contaminate the GP review queue:

| Near-miss | Why it fails |
|---|---|
| Lower `HIGH_CONFIDENCE_THRESHOLD` to `0.50` | Pushes more noise directly into Notion without GP review |
| Auto-push `0.5-0.7` scored items to Notion as `Tracking` | Same problem: Notion is downstream of the GP filter |
| Disable the strict AUTO_PUSH gate and rely on multi-source instead | Changes the bypass rule, but still bypasses GP review |
| Allow LLM-rejected candidates to AUTO_PUSH if multi-source | Routes pre-GP-reviewed items into Notion under a different rule |
| Lower suppression-cache strictness so resurfaced items get a second chance in Notion | Re-surfacing should land in the review queue, not the CRM |

These are true loosening moves because they increase volume before review while keeping the downstream CRM boundary intact:

| Loosening action | Why it qualifies |
|---|---|
| `THESIS_REJECTED_TO_THIN_FILES=true` | Stage-2 change; increases queue candidates without touching Notion |
| Per-source thesis loosening for `HN` / `arxiv` / `rss_feeds` | Increases review volume, not AUTO_PUSH |
| Widen the `NEEDS_REVIEW` upper edge from `0.7` to `0.75` | More candidates reach the GP review queue, not AUTO_PUSH |
| Lower the `NEEDS_REVIEW` lower edge from `0.4` to `0.3` | Same: broader review queue without downstream bypass |
| Reduce thin-file promotion strictness so HELD items can corroborate into the queue | Increases what reaches review without bypassing it |

## Active Gates

These items stay in the main recommendation surface because they materially affect execution validity.

### Gate A: MERGE_WRITES regret window after completed repair

- The original env-only promotion was governance debt, but the repo now contains a repaired `feature_promote` row with `effective_at=2026-04-04T00:00:00Z` and `regret_due_at=2026-04-18`.
- Treat the repaired audit row as current repo truth.
- The regret check on `2026-04-18` is a hard sequence gate.
- No new governance experiments or loosening changes should ship before that passes.
- If the regret check fails or cannot run credibly, freeze loosening work and treat governance repair as the only active lane until the gate is restored.

### Gate B: Diagnostic summary decides scope

- Use the current `2026-04-06` diagnostic summary as the active branch selector.
- For future reruns, verify schema and join compatibility before comparing results to the same baseline framing. If the comparison path is not credible, downgrade to the `Diagnostic cannot be computed` branch.
- Do not run the strategy off a fresh, context-free reproduction when the baseline already exists.
- Use the refreshed diagnostic to decide which branch of the three-state recommendation tree should ship.
- Preserve the branch criteria in the front door:
  - `Score collapse confirmed` = mean separation `< 0.05` or `AUC < 0.65`
  - `Threshold ceiling only` = acceptable separation but `max < 0.7`
  - `No routing problem detected` = good separation and threshold reachable
- If the diagnostic cannot be computed because labels or joins are insufficient, do not guess a branch; use the learning-loop work as the next move and diagnose later.

### Gate C: Architecture decisions before code

Before code lands, explicitly lock:

- LLM-in-routing stance
- evidence storage location
- evidence-family mapping
- K and yield calibration
- the pre-review/post-review boundary

### Gate D: Hardening core stays live

Across branches, keep the load-bearing hardening core visible:

- resolve the LLM-routing choice explicitly
- pick one evidence storage location
- define the evidence-family mapping
- write per-phase kill criteria before tickets start
- quantify K and review-yield assumptions before using them as operating thresholds
- do not add v52 fields wholesale when they lack a current consumer
- do not under-frame the USPTO trademark work as a refactor when the current file is still a placeholder

## Three-State Recommendation Tree

This remains the governing decision structure, with the diagnostic framed as a refresh:

1. `Score collapse confirmed`
   - Trigger condition: mean separation `< 0.05` or `AUC < 0.65`.
   - Execute the full hardened strategy.
   - Keep the hardening core and sequencing gates active.

2. `Threshold ceiling only`
   - Trigger condition: acceptable separation, but `max < 0.7`.
   - Execute the Phase 0 hotfix, learning loop, and trademark scaffolding.
   - Keep the same hardening core, gating, and sequencing rules.

3. `No routing problem detected`
   - Trigger condition: good separation and threshold reachable.
   - Execute only the learning-loop work.
   - Defer pre-review loosening and broader expansion work.

What changes from the earlier source is not the shape of this tree, but the corrected sequence around MERGE_WRITES, the existing baseline refresh, and the pre-review-only loosening rule.

Fallback if the diagnostic cannot be computed credibly:

- Execute only the learning-loop work needed to generate labels first.
- Do not guess a branch until the diagnostic becomes computable.

## Next Three Actions

| Action | Owner | Artifact Path | Success Criterion | Depends On |
|---|---|---|---|---|
| Document the MERGE_WRITES repaired state, confirm GP availability, and prepare the supervised regret check | Governance owner + GP | `docs/plans/2026-04-06-merge-writes-governance-bypass.md` | Current repaired state is documented, GP availability for `2026-04-16` to `2026-04-20` is confirmed, and the supervised regret-check path is explicit | Existing repair artifacts in `artifacts/regret-check/step4b-repair-2026-04-05/` |
| Use the `2026-04-06` diagnostic summary as the current branch selector and preserve its query shape for future reruns | Analysis owner | `artifacts/router-diagnostic/2026-04-06/` | Current branch is selected from live DB evidence, and future reruns have an explicit comparison path | None beyond DB/query access |
| Convert the selected branch into an executable hold-state or rollout plan | Strategy owner | `docs/plans/2026-04-06-pre-review-loosening-plan.md` | Branch plan matches the selected diagnostic outcome and keeps loosening strictly pre-review | Diagnostic branch selected |

## What Changes in Practice

- If the refreshed diagnostic lands on `score collapse confirmed` or `threshold ceiling only`, the strategy should become more aggressive about pre-review intake because the real review capacity is much larger than the earlier `K=20` assumption.
- The refreshed diagnostic currently lands on `no routing problem detected`, so expansion stays out of the immediate branch and the near-term work stays on the learning loop.
- If the refreshed diagnostic cannot be computed credibly, do not branch-pick. Generate labels first, then re-run the diagnostic.
- The strategy should not become more aggressive about post-review push or CRM contamination.
- Governance/calendar work is not side noise. It is the first execution gate.

## Source-of-Record Notes

The long scrutiny remains necessary because it preserves why the recommendation changed:

- Section `4` still matters as supporting hardening logic.
- The hardening core from section `4` remains live even though not every original section `4` recommendation survives unchanged.
- Section `7` preserves the three-state decision structure.
- Sections `12.9` and `13.8` keep the MERGE_WRITES and regret-window sequencing corrections live.
- Sections `14.4` through `14.10.6` update the operating calibration and correct the earlier stage-conflation error.
- The `2026-04-06` implementation artifacts add the current repo-state corrections: repaired Gate A and the current diagnostic branch.

Use [sweetharmony-evidence-table.md](C:\dev\Harmonic\.omx\plans\sweetharmony-evidence-table.md) when a reader needs to know what remains live versus what is only historical or supporting trace.

## Source-Sync Protocol

- Last synced against the source-of-record revision observed on `2026-04-06`.
- Treat the evidence-table line refs as snapshot references, not permanent anchors.
- Re-run the evidence pass and update this executive layer whenever any of these change:
  - the diagnostic refresh result
  - the `2026-04-18` regret check outcome
  - any change to the source-of-record sections corresponding to `7`, `12.9`, `13.8`, or `14.10`
  - any material change to `K`, post-review yield, labeling cap, or milestone calibration
- Minimal refresh command for the controlling sections:
```powershell
Select-String -Path 'C:\Users\nikhi\.claude\plans\transient-hugging-manatee.md' -Pattern '^## 7\.|^### 12\.9|^### 13\.8|^### 14\.10'
```
- If the source changes and this front door is not updated, trust the source-of-record scrutiny over the executive layer until sync is restored.
