# RALPLAN-DR Draft: SweetSweetHarmony Review for Startup-Team Detection

## Scope

- Task type: planning / consensus draft
- Target outcome: define how to review and improve the SweetSweetHarmony strategy artifact so it better improves the engine's ability to identify credible startup teams worth contacting now
- Primary source artifacts:
  - `.omx/specs/deep-interview-transient-hugging-manatee-review.md`
  - `.omx/interviews/transient-hugging-manatee-review-20260406T074436Z.md`
  - `C:\Users\nikhi\.claude\plans\transient-hugging-manatee.md`
  - `docs/plans/LOB.txt`

## Grounded Assessment

The current scrutiny document only partially achieves its own stated goal.

- It does satisfy the analytical part of the goal: it identifies hardening needs, surfaces strategy tensions, and contains material that still matters for execution.
- It does not cleanly satisfy the decision-making part of the goal: the main body carries multiple competing bottom lines across `§4`, `§7`, `§9`, `§12.9`, and `§13.8`, which weakens decisiveness.
- Some later source changes are not mere drift. They function as still-live branch conditions, sequencing gates, or governance constraints that can materially affect execution order or validity.
- The deep-interview spec makes the true optimization target explicit: improve startup-team identification effectiveness, not defend the scrutiny artifact as rigorous.
- `LOB.txt` is useful provenance and ideation material, but it is not a canonical decision container.

Planning implication: the next artifact must end with one canonical recommendation, but the plan must earn both that recommendation and the container choice fairly from an evidence-table pass instead of assuming them up front.

## RALPLAN-DR

### Principles

1. Optimize for better startup-team detection, not better-looking scrutiny.
2. Earn the container choice from the evidence table; do not pre-lock the recommendation surface.
3. End with one canonical recommendation in the main body, while preserving still-live gates, branch conditions, and validity constraints at the right visibility level.
4. Define review-stage quality as contact-worthiness at human review, not general interestingness.
5. Preserve broad intake and prune later; early precision only needs to clear the stated human-review floor.

### Decision Drivers

1. Decision clarity: a reader should reach a canonical go / conditional-go / no-go view quickly.
2. Detection effectiveness: recommendations must improve the engine's ability to surface credible startup teams under broad intake.
3. Execution validity: still-live sequencing, branch, and governance constraints must remain visible when they materially affect order or legitimacy of action.

### Viable Options

#### Option A: Tighten the existing long scrutiny artifact in place

Pros:
- Reuses the existing analysis and section structure.
- Keeps provenance close to the claims.
- Lowest rewrite cost.

Cons:
- High risk of preserving competing recommendation layers.
- Still biases the output toward artifact coherence over detection effectiveness.
- Harder for a reader to identify the canonical recommendation and next three actions within five minutes.

#### Option B: Keep the scrutiny artifact as the analytic source-of-record and add an authoritative executive decision layer

Pros:
- Fairly preserves the long scrutiny artifact as the analytic source-of-record.
- Creates a clear front-door decision surface without discarding still-live sequencing or governance constraints.
- Fits the synthesis path suggested by the architect: executive recommendation surface plus retained analytic trace.

Cons:
- Risks contradiction if the executive layer and source-of-record diverge.
- Requires explicit discipline over what remains live in the front layer versus the analytic source.
- Slightly more complex to maintain than a pure memo replacement.

#### Option C: Produce a short canonical decision memo with appendix/trace

Pros:
- Best fit for the spec's requirement of one main recommendation.
- Cleanly separates decision, evidence, and provenance.
- Easiest way to focus on startup-team detection quality and operational thresholds.

Cons:
- Requires explicit curation of what moves into appendix versus what must remain visible.
- Risks burying still-live sequencing or governance constraints if executed carelessly.
- Needs discipline to avoid quietly becoming a second long notebook.

#### Option D: Replace the scrutiny output with an execution scorecard only

Pros:
- Very fast to scan.
- Forces metric-oriented thinking.

Cons:
- Too thin for this task because it does not adequately evaluate whether the current document achieves its stated goal.
- Loses the argument needed to justify replacing or reshaping the current artifact.
- Weak support for preserving useful evidence from the scrutiny and `LOB.txt`.

### Container Decision Rule

Do not choose the final container before the evidence-table pass.

Decision rule after evidence triage:

- Choose the option that best preserves one canonical recommendation while keeping still-live execution gates, branch conditions, and urgent governance or sequencing constraints visible at the main recommendation surface.
- If the evidence table shows that live constraints are too dense or too execution-critical to safely demote, prefer **Option B**.
- If the evidence table shows that most non-canonical material is historical trace or appendix support rather than live execution logic, prefer **Option C**.
- Reject any option that can only achieve clarity by hiding live execution-validity constraints in appendix material.

### Requirements Summary

- Evaluate whether the current scrutiny document achieves its stated purpose: determine whether the strategy is sound enough to execute and where it needs hardening.
- State whether the current artifact is the right decision container.
- If not, recommend the best replacement or synthesis container.
- Keep one canonical recommendation in the main body as the desired end state.
- Optimize for startup-team detection effectiveness under broad intake then prune.
- Define review-stage noise as candidates that reach review but are not credible startup teams worth contacting now.
- Use the initial operating thresholds: `200-500` reviewable signals and `~10%` contact-worthy floor at human review.
- Treat internal rigor of the existing document as a non-goal except where it affects decision quality or execution validity.
- Treat `LOB.txt` as provenance / input material, not the canonical plan.

### Testable Acceptance Criteria

1. The resulting review artifact has exactly one canonical recommendation in the main body.
2. Within five minutes, a reader can answer:
   - whether the current strategy is go / conditional go / no-go,
   - whether the current scrutiny document achieves its stated goal,
   - whether the current container should be kept, layered, or replaced,
   - the next three actions in order.
3. The main recommendation explicitly optimizes for startup-team identification effectiveness.
4. The artifact defines review quality using contact-worthiness at review.
5. The artifact is explicitly compatible with broad-intake-then-prune and the stated early tolerance for review noise.
6. All earlier recommendation revisions are either removed from the main body or clearly demoted into trace/support roles that do not compete with the canonical recommendation.
7. Any still-live sequencing or governance constraints that materially affect execution order or recommendation validity remain visible in the main recommendation surface.
8. `LOB.txt` content is cited only as support, counterexample, or provenance, not treated as the controlling decision artifact.

### Implementation Steps

1. Build an evidence table from the current scrutiny document.
   - Extract the stated goal, each recommendation layer, each branch condition, each sequencing gate, and each claim that materially affects startup-team detection quality.
   - Mark each item as one of: `main recommendation`, `active gate`, `branch condition`, `historical trace`, `appendix support`, `discard`.
   - Use `historical trace` for superseded reasoning kept for auditability, and `appendix support` for non-controlling evidence that still supports the chosen recommendation.

2. Complete evidence-led container comparison.
   - Compare Option A, Option B, and Option C against the completed evidence table and the acceptance criteria.
   - Treat Option A as a live disqualification check; if it still fails the clarity criteria, the real final choice narrows to Option B versus Option C.
   - Explicitly test whether still-live sequencing, branch, or governance constraints can remain visible without creating competing bottom lines.

3. Choose the final recommendation surface only after Step 2.
   - Lock the output structure only after the evidence table justifies the container choice.

4. Draft the canonical recommendation surface.
   - If Option B wins: draft the authoritative executive layer plus an explicit live-gates / sequence box, with the long scrutiny retained as analytic source-of-record.
   - If Option C wins: draft the short canonical memo plus an explicit live-gates / sequence box, with appendix/trace support.

5. Reframe the substance around startup-team detection.
   - Re-express claims in terms of whether they improve credible-startup-team surfacing, review-stage contact-worthiness, and pruning quality.
   - Remove or demote content whose main value is only scrutiny completeness or historical debate.
   - Keep urgent governance or sequencing items in the recommendation surface when they materially affect execution validity.

6. Build the appendix / trace package.
   - Move superseded recommendation layers, provenance, and long-form challenges into appendix or historical trace.
   - Include `LOB.txt` only where it contributes source ideas, constraints, or counterpoints.

7. Run a final decision-quality review.
   - Check that the main body has one recommendation, one rationale line, one next-step sequence, and no competing bottom lines.
   - Check that still-live sequencing, branch conditions, and urgent governance items remain visible in the recommendation surface.
   - Check that the artifact would still make sense to a reader who never opens the appendix.

### Risks and Mitigations

- Risk: the review stays artifact-centric and fails to improve engine direction.
  - Mitigation: require every major recommendation to answer "how does this improve startup-team detection?"

- Risk: the plan quietly pre-selects a memo path and treats alternatives unfairly.
  - Mitigation: defer the container decision until after the evidence table and require explicit option comparison against completed triage.

- Risk: multiple recommendation layers survive under new headings.
  - Mitigation: enforce a single canonical recommendation section and assign all other material to explicit non-competing evidence categories.

- Risk: still-live sequencing gates or governance constraints get demoted as if they were historical drift.
  - Mitigation: require explicit `active gate` and `branch condition` evidence categories and force those items into the main recommendation surface when they materially affect validity or order.

- Risk: broad-intake strategy gets unintentionally narrowed by over-optimizing early precision.
  - Mitigation: anchor the artifact to the `200-500` review capacity and `~10%` contact-worthy floor.

- Risk: `LOB.txt` ideas distort the decision because they are broad, raw, and partly orthogonal.
  - Mitigation: cite `LOB.txt` only as provenance or optional supporting evidence, never as the main decision frame.

### Verification Steps

1. Confirm the final artifact answers the current-document-purpose question directly.
2. Confirm the main body contains exactly one canonical recommendation.
3. Confirm the artifact explicitly defines review-stage noise and useful review outcomes.
4. Confirm the next three actions are ordered and tied to startup-team detection impact.
5. Confirm active date-bound constraints remain visible in the main recommendation surface when they materially affect timing, order, or validity.
6. Confirm branch conditions from the source analysis remain preserved and are not collapsed away improperly.
7. Confirm urgent governance items that materially affect execution validity are not demoted to appendix/support-only status.
8. Confirm the chosen container is justified against the completed evidence table rather than against prior stylistic preference.
9. Confirm any retained metrics align with broad intake and the `~10%` contact-worthy floor.

## ADR

### Decision

Do not pre-commit to a container. Select the final recommendation surface only after the evidence-table pass, choosing between:

- an authoritative executive decision layer with the long scrutiny retained as analytic source-of-record, or
- a short canonical decision memo with appendix/trace.

### Drivers

- The current scrutiny document has multiple competing recommendation layers in the main body.
- The spec explicitly prioritizes startup-team detection effectiveness over artifact rigor.
- The output must be quickly scannable and execution-ready.
- Still-live sequencing and governance constraints must remain visible when they affect execution order or validity.

### Alternatives Considered

- Keep and tighten the long scrutiny artifact in place without an authoritative executive layer.
  - Rejected because it is too likely to preserve recommendation drift and weak decision visibility.

- Replace the output with a bare scorecard / checklist.
  - Rejected because it is too thin to evaluate whether the current document achieves its stated goal and too weak to justify a container change.

### Why Chosen

This decision rule best reconciles four needs at once:

- preserve the analytical value already present,
- collapse to one recommendation,
- keep still-live branch and sequencing constraints visible when execution depends on them,
- focus the decision on startup-team detection outcomes rather than document self-consistency.

### Consequences

- The existing scrutiny document may remain the analytic source-of-record if Option B wins, or become supporting evidence if Option C wins.
- Some sections that are currently prominent will move to appendix, active-gate boxes, or be cut based on the evidence table.
- The resulting artifact should become easier to act on, but it will require deliberate curation to avoid either hidden live constraints or renewed recommendation drift.

### Follow-ups

1. Produce the evidence table and section triage from the current scrutiny artifact using the expanded evidence categories.
2. Choose the final container after comparing Options B and C against the completed evidence table.
3. Draft the canonical recommendation surface with visible live-gates / sequence handling.
4. Append the trace / provenance map for superseded recommendations and `LOB.txt` references.
5. Run a final review against the acceptance criteria before handoff.

## Follow-up Staffing Guidance

### Available Agent Types Roster

- `planner`
- `architect`
- `analyst`
- `critic`
- `writer`
- `researcher`
- `verifier`
- `team-executor`

### If execution later uses `$ralph`

Recommended lane shape:

- Single-owner leader: `planner` or `architect`
- Verification/sign-off: `architect` plus `verifier`
- Supporting spot lane: `writer` only after the evidence-table and container decision are complete

Suggested sequence:

1. `architect` validates the current scrutiny artifact against the evidence categories and flags which items are live gates versus historical trace.
2. `critic` challenges the fairness of the Option B versus Option C comparison after the evidence table is complete.
3. `planner` or `architect` selects the container and produces the canonical recommendation surface outline.
4. `writer` compresses the approved structure into the final artifact.
5. `verifier` checks one-recommendation integrity, preservation of still-live constraints, and threshold alignment.

Reasoning levels:

- `architect`: high
- `critic`: high
- `planner`: medium
- `writer`: high
- `verifier`: high

Ralph launch hint if requested later:

- `$ralph produce the canonical SweetSweetHarmony recommendation surface from the approved RALPLAN draft; first build the evidence table, then choose fairly between executive-layer plus source-of-record or short memo plus appendix`

### If execution later uses `$team`

Recommended staffing:

- 4 workers total
- 1 `architect` lane: evidence-table audit and live-constraint classification
- 1 `critic` lane: option fairness review and risk challenge
- 1 `writer` lane: recommendation-surface drafting
- 1 `verifier` lane: acceptance-criteria audit and preservation checks

Why this split:

- The work has four natural lanes: evidence classification, option challenge, artifact construction, and independent validation.
- The critic lane is warranted because the main failure mode is unfairly collapsing to a memo preference before the evidence justifies it.

Suggested reasoning levels:

- `architect`: high
- `critic`: high
- `writer`: high
- `verifier`: high

Concrete launch hint if requested later:

- `$team 4 "Build the SweetSweetHarmony evidence table first, compare executive-layer-plus-source-of-record vs short canonical memo fairly, draft the chosen recommendation surface, and validate that live gates and urgent governance constraints remain visible"`

Team verification path:

1. `architect` confirms the evidence table and identifies live gates, branch conditions, and historical trace.
2. `critic` confirms the chosen container is justified by the evidence table rather than prior preference.
3. `writer` delivers the recommendation surface plus appendix / source-of-record mapping.
4. `verifier` checks against the nine verification steps above.
5. If major drift remains, hand off to `$ralph` for a single-owner tighten-and-verify pass.

## Consensus Changelog

- Added the hybrid source-of-record plus executive-layer option as a fair alternative to pure memo replacement.
- Deferred the container decision until after the evidence-table pass.
- Expanded evidence triage to preserve active gates, branch conditions, historical trace, and appendix support distinctly.
- Added acceptance and verification checks that keep still-live sequencing and governance constraints visible in the main recommendation surface.
- Clarified that Option A is a disqualification check and the final choice typically narrows to Option B versus Option C after the evidence pass.
