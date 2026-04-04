# Deep Interview Spec: temporal-puzzling-dove

## Metadata
- Profile: standard
- Context type: brownfield
- Rounds: 4
- Final ambiguity: 7%
- Threshold: 20%
- Context snapshot: `C:\dev\Harmonic\.omx\context\temporal-puzzling-dove-20260404T001115Z.md`
- Transcript: `C:\dev\Harmonic\.omx\interviews\temporal-puzzling-dove-20260404T001121Z.md`

## Clarity Breakdown
| Dimension | Score |
|---|---:|
| Intent | 0.90 |
| Outcome | 0.86 |
| Scope | 0.93 |
| Constraints | 0.90 |
| Success Criteria | 0.86 |
| Context | 0.90 |

## Intent (why the user wants this)
Turn the existing merged thesis-classifier plan into a stronger decision artifact by actively challenging it against repo reality before any planning or execution handoff. The user wants the plan improved, not merely preserved.

## Desired Outcome
Produce a rewritten plan that:
1. pressure-tests the current merged spec against the actual Harmonic repository,
2. is allowed to cut or defer phases/items when evidence says they are premature,
3. can resequence work if the real bottleneck is evaluation/data quality rather than classifier logic,
4. still preserves clearly beneficial improvements.

## In-Scope
- Challenge the current markdown plan rather than treating it as locked.
- Rewrite structure, priorities, and sequencing before downstream handoff.
- Cut or defer phases/items when justified by codebase evidence.
- Reframe sequencing around root-cause bottlenecks if repo evidence supports that.
- Preserve clear improvements even when resequencing them.

## Out-of-Scope / Non-goals
- Do not preserve the current phase structure just because it already exists.
- Do not treat the existing markdown as the execution source of truth.
- Do not remove clearly beneficial improvements merely because they are not first in sequence.
- Do not implement code directly in deep-interview mode.

## Decision Boundaries (what OMX may decide without confirmation)
OMX may, without further confirmation:
- actively challenge and rewrite the plan,
- cut or defer entire phases/items when supported by repo evidence,
- resequence work toward evaluation/data-quality bottlenecks,
- preserve both repo-grounded improvements and plausible improvements from the merged spec.

OMX should not, without further confirmation:
- remove a clear improvement entirely once it is judged clearly beneficial,
- bypass the rewrite/challenge step and jump straight into implementation from the old plan.

## Constraints
- Brownfield repo: named target files/directories already exist.
- Rewrite decisions should be grounded in repository evidence where possible.
- Clear improvements must survive, even if deferred or resequenced.
- Deep-interview is requirements-only and must hand off rather than implement.

## Testable Acceptance Criteria
A satisfactory rewritten plan must:
1. Explicitly identify which original items are preserved, deferred, or cut.
2. Tie each major rewrite or deferral to repo evidence or an explicit reasoning note.
3. Preserve clear improvements, including both repo-grounded fixes and plausible merged-spec improvements.
4. Make sequencing rationale explicit when root-cause analysis shifts work later/earlier.
5. Be suitable as the source brief for a downstream planning lane such as `$ralplan`.

## Assumptions Exposed + Resolutions
- Assumption: the existing merged plan should be preserved as-is.
  - Resolution: false; it should be actively challenged and rewritten.
- Assumption: major phases must remain intact.
  - Resolution: false; phases/items may be cut or deferred with codebase evidence.
- Assumption: if evaluation/data quality is the real bottleneck, prompt/classifier work should disappear.
  - Resolution: false; it may be sequenced later, but clear improvements should not be removed.
- Assumption: “clear improvements” means only repo-proven fixes.
  - Resolution: false; it includes both repo-grounded improvements and plausible improvements from the merged spec.

## Pressure-Pass Findings
- Revisited the core operating principle under a contrarian lens: if evaluation/data quality is the real bottleneck, can prompt/classifier work be demoted?
- Outcome: reprioritization is allowed, but removal of clear improvements is not.

## Brownfield Evidence vs Inference
### Evidence
- The following plan touchpoints exist in `C:\dev\Harmonic`:
  - `consumer/thesis_filter/llm_classifier.py`
  - `consumer/thesis_filter/hard_disqualifiers.py`
  - `storage/signal_store.py`
  - `utils/thesis_filter.py`
  - `workflows/pipeline.py`
  - `CLAUDE.md`
  - `tests/fixtures`
  - `tests/utils`

### Inference
- The plan is structurally implementable in this repo because the named touchpoints exist.
- A deeper symbol-level feasibility pass is still appropriate in downstream planning because `omx explore` failed in this session and only file-level grounding was collected here.

## Technical Context Findings
- This is brownfield work against an existing thesis-classifier pipeline.
- The current source plan is already fairly specific on phases, files, verification, rollback, and deferrals.
- The remaining value is not basic scoping, but authority boundaries for rewriting and preserving improvements.

## Condensed Transcript
1. The user wants the current document actively challenged and rewritten.
2. OMX may cut or defer entire phases/items when repo evidence supports that.
3. OMX may resequence around evaluation/data quality root causes, but must not remove clear improvements.
4. “Clear improvements” includes both repo-grounded fixes and plausible improvements from the merged spec.
