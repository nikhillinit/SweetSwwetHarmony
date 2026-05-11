# Thesis Filter Survivor P1 RALPLAN

## Scope

Bounded execution plan for the P1 thesis-filter survivor slice:

- Reconfirm the active production path as `workflows/pipeline.py -> utils.thesis_filter`
- Run a read-only survivor analysis against the live 614-row `signals.db` baseline, with a canonical pending-company lane and an auxiliary whole-baseline census
- Patch only proven over-rejection issues
- Keep production `signals.db` read-only throughout this slice unless a later explicit strategy gate says otherwise

## Evidence Base

- `workflows/pipeline.py` imports `ThesisFilter`, `ThesisFilterConfig`, and `RoutingDecision` from `utils.thesis_filter`, instantiates `ThesisFilter`, and awaits `self._thesis_filter.classify(...)`.
- `workflows/pipeline.py:492-496` constructs `thesis_config = ThesisFilterConfig.from_env()`, then mutates `hold_threshold` when `PipelineConfig.thesis_hold_threshold != 0.3`, then instantiates `ThesisFilter(thesis_config)`.
- Active production selection path groups pending rows by `canonical_key`, consolidates each group, joins `consolidated.descriptions`, and then applies thesis filtering to the consolidated company payload.
- Critic live DB facts for this lane: 614 `signals` rows, 588 distinct `canonical_key` values, and 34 pending rows from `signal_processing.status = 'pending'`.
- Canonical survivor-analysis parity means matching the live call shape from `workflows/pipeline.py`: same joined description text from consolidated pending groups, same `company_name`, no `domain_name` in the canonical pass, real `ThesisFilter.classify(..., skip_llm=True)`, and a captured snapshot of the effective instantiated filter config after pipeline-equivalent construction and mutation.
- `utils/thesis_filter.py` contains the live routing logic, including LLM skip behavior, negative keyword handling, hard vetoes, and consumer rescue.
- `ThesisFilter.classify(skip_llm=True)` still routes through matcher runtime behavior, so the survivor artifact must record matcher `v2_enablement` and `ml_enablement` state to prevent silent rerun drift.
- `docs/plans/harmonic-dev-strategy-2026-05-11.md` records Gate A as passed, declares live `signals.db` at 614 rows, and keeps thesis-filter survivor work read-only until failure modes are proven.
- `storage/signal_store.py` defines `CURRENT_SCHEMA_VERSION = 53`; strategy evidence treats `PRAGMA user_version=0` as header drift only.
- Existing test surface already covers thesis-filter routing and cascade behavior:
  `tests/utils/test_thesis_filter.py`, `tests/utils/test_thesis_filter_cascade.py`,
  `tests/utils/test_thesis_filter_llm_integration.py`, `tests/utils/test_thesis_golden_set.py`,
  `tests/utils/test_cascade_routing.py`, `tests/test_analyze_pipeline_thesis.py`.
- Planned dedicated survivor-analysis surface:
  `scripts/thesis_filter_survivor_analysis.py`,
  artifact `artifacts/thesis_filter_survivor_analysis_614.json`,
  and verification tests in `tests/test_thesis_filter_survivor_analysis.py`.

## RALPLAN-DR Short Mode

### Principles

1. Start from the runtime path that actually executes, not legacy thesis-filter code.
2. Separate diagnosis from behavior changes; no patch lands before the over-rejection mode is reproduced.
3. Keep production `signals.db` read-only for the entire survivor-analysis lane.
4. Favor narrow, test-backed fixes over threshold churn or corpus-wide retuning.
5. Treat rejection-rate improvements as provisional until corpus stability is re-established.

### Top Decision Drivers

1. Runtime truth: the implementation path in `workflows/pipeline.py -> utils.thesis_filter` is already evidenced and must anchor all follow-up work.
2. Data safety: `signals.db` is still a sensitive production artifact, so survivor analysis must use read-only access and avoid persistence-capable pipeline paths.
3. Anti-overfitting: only repeated, explainable false rejections should justify code changes; single-signal anecdotes are insufficient.

### Viable Options

#### Option A: Reuse the existing CSV calibration script only

Pros:
- Fastest path to a report
- Reuses `scripts/analyze_pipeline_thesis.py` and its tests

Cons:
- Does not satisfy the stated 614-row live-baseline requirement
- Risks proxy conclusions from an external CSV instead of the active production corpus
- Cannot prove current survivor behavior on the actual live database slice

#### Option B: Build a dedicated read-only survivor analysis on the live 614-row DB

Pros:
- Directly answers the bounded task
- Preserves production safety if implemented with `sqlite3.connect("file:...?...mode=ro", uri=True)`
- Produces decision-path and false-rejection evidence tied to the real active corpus

Cons:
- Requires a small diagnostic surface instead of pure reuse
- Needs careful separation from any code that opens write-capable stores

#### Option C: Copy the live DB and analyze the copy with normal pipeline code

Pros:
- Easier reuse of existing store/pipeline helpers
- Safer than mutating production directly

Cons:
- Loses the strict read-only guarantee on the production baseline lane
- Encourages accidental drift into broader replay or write-capable workflows
- Adds copy/sandbox complexity before the actual failure mode is proven

### Recommendation

Choose Option B.

It is the only option that matches all four task constraints at once: real active-path evidence, live 614-row baseline, read-only production handling, and focused behavior fixes only after proof. Option A can remain a comparison aid later; Option C is acceptable only for post-diagnosis fixture or temp-copy validation, not for the primary survivor-analysis lane.

## ADR

### Decision

Run the thesis-filter survivor investigation on the live 614-row corpus through a dedicated read-only analysis path rooted in `workflows/pipeline.py -> utils.thesis_filter`, using consolidated pending companies as the canonical evidence unit and a whole-baseline 614-row census as auxiliary context, while preserving exact production call parity for the canonical pass and banning write-capable and LLM/network-capable execution paths during diagnosis, then patch only repeated over-rejection modes with focused tests and fixture-based verification.

### Drivers

- Need to confirm the real production path before fixing behavior
- Need to preserve `signals.db` safety after prior DB incident handling
- Need to avoid tuning the classifier to anecdotal examples

### Alternatives Considered

- CSV-only proxy analysis
- Copy-first replay analysis

### Why Chosen

It provides direct evidence on the active runtime selection path while still accounting for the full 614-row live baseline as denominator and drift context.

### Consequences

- Implementation will add a dedicated diagnostic script/report path:
  `scripts/thesis_filter_survivor_analysis.py` ->
  `artifacts/thesis_filter_survivor_analysis_614.json`
- Verification must avoid `DiscoveryPipeline.initialize()`, `process_pending()`, `SignalStore`, full `Pipeline`, and any workflow that can persist during analysis
- Canonical survivor analysis must use the real `ThesisFilter.classify(..., skip_llm=True)` entrypoint, compute `llm_eligible` analytically, and must not invoke the lazy LLM classifier or any real network call
- Survivor artifacts must pin matcher runtime state, including `v2_enablement` and `ml_enablement`
- Behavior claims remain narrow and provisional

### Follow-ups

- If a failure mode is proven, add focused unit tests before patching
- If evidence points to corpus or liveness issues instead of filter behavior, stop and record that rather than tuning thresholds

## Phased Plan

### Phase 0: Safety And Preconditions

Actions:

- Record current repo head and confirm it is descendant of the user-stated `240a44e` sync point.
- Recheck the live baseline facts in read-only mode: 614 `signals` rows, 588 distinct `canonical_key` values, 34 pending rows via `signals` joined to `signal_processing`, `MAX(schema_migrations.version)=53`, `PRAGMA user_version=0`.
- Declare banned survivor-analysis execution paths up front:
  `DiscoveryPipeline.initialize()`, `process_pending()`, `SignalStore`, full `Pipeline`, any path that can write shadow logs, thesis classifications, run history, WAL side effects, or migrations.
- Freeze scope against the stated exclusions: no runner, keepalive, Indiegogo, live Phase G, corpus calibration, or stash recovery work.

Acceptance criteria:

- Current head is recorded in the execution notes.
- Live DB facts are revalidated without opening a write-capable connection.
- The execution note declares the analysis units explicitly:
  canonical = consolidated pending companies;
  auxiliary = whole-baseline 614-row census for denominator and drift context.
- The execution note explicitly states that canonical survivor analysis cannot use write-capable pipeline/store entrypoints or any path with migration/WAL/logging side effects.
- No plan step requires `stash@{0}` or touches unrelated untracked artifacts.

Verification commands:

```powershell
git rev-parse --short HEAD
git merge-base --is-ancestor 240a44e HEAD
python -c "import sqlite3; p=r'signals.db'; c=sqlite3.connect(f'file:{p}?mode=ro', uri=True); print({'rows': c.execute('select count(*) from signals').fetchone()[0], 'canonical_keys': c.execute('select count(distinct canonical_key) from signals').fetchone()[0], 'pending_rows': c.execute(\"select count(*) from signals s join signal_processing p on s.id = p.signal_id where p.status = 'pending'\").fetchone()[0], 'user_version': c.execute('pragma user_version').fetchone()[0], 'max_schema_migrations_version': c.execute('select max(version) from schema_migrations').fetchone()[0]}); c.close()"
```

### Phase 1: Active-Path Reconfirmation

Actions:

- Reconfirm that `workflows/pipeline.py` is the active caller and that `utils.thesis_filter` owns the routing logic being evaluated.
- Reconfirm the exact canonical survivor-analysis call contract from `workflows/pipeline.py`:
  same joined description text, same `company_name`, no `domain_name`, and real `ThesisFilter.classify(..., skip_llm=True)` in the canonical pass.
- Reconfirm the analysis unit contract:
  canonical evidence uses only current pending companies after grouping by `canonical_key`, consolidating, and joining `consolidated.descriptions`;
  auxiliary context may report the whole 614-row / 588-key baseline but cannot replace the canonical lane.
- Capture the effective filter config snapshot used for analytic parity and artifact provenance after pipeline-equivalent config construction and mutation, not raw `ThesisFilterConfig.from_env()` alone.
- Capture matcher runtime parity fields used during canonical classify calls, including `v2_enablement` and `ml_enablement`.
- Identify any legacy comparison paths only as non-authoritative context.

Acceptance criteria:

- Evidence shows import, instantiation, and `classify(...)` call sites in `workflows/pipeline.py`.
- Evidence shows the pipeline-equivalent config-construction sequence, including the post-construction `hold_threshold` mutation before `ThesisFilter` instantiation.
- Canonical pass inputs are specified in plan notes with exact production parity requirements for pending-group consolidation, joined description text, and `company_name`.
- The plan explicitly distinguishes canonical pending-company evidence from the auxiliary whole-baseline census.
- The plan explicitly forbids passing `domain_name` in the canonical survivor-analysis pass, even if auxiliary comparison lanes later inspect domain effects separately.
- The effective instantiated config snapshot used by routing analysis is captured in the output artifact.
- Matcher runtime metadata includes `v2_enablement` and `ml_enablement`.
- Any direct matcher-plus-routing-helper analysis is labeled auxiliary or counterfactual only and cannot serve as canonical survivor evidence.
- Any `consumer/thesis_filter/*` references are explicitly treated as legacy/comparison-only.

Verification commands:

```powershell
rg -n "from utils\\.thesis_filter import|self\\._thesis_filter = ThesisFilter|await self\\._thesis_filter\\.classify" workflows/pipeline.py
rg -n "ThesisFilterConfig\\.from_env|thesis_hold_threshold|hold_threshold = self\\.config\\.thesis_hold_threshold|self\\._thesis_filter = ThesisFilter" workflows/pipeline.py
rg -n "canonical_key|consolidated|descriptions|company_name|domain_name|skip_llm" workflows/pipeline.py utils/thesis_filter.py
rg -n "class ThesisFilter|skip_llm_if_keyword_below|negative_keyword_penalty|consumer_rescue_threshold|decision_path_code" utils/thesis_filter.py
rg -n "v2_enablement|ml_enablement|RuntimeControls" utils/thesis_matcher.py utils/runtime_controls.py
```

### Phase 2: Read-Only Survivor Analysis

Actions:

- Run `scripts/thesis_filter_survivor_analysis.py` as a dedicated read-only analysis over the live 614-row baseline.
- Preserve exact active-path parity in the canonical pass:
  use only current pending companies after grouping by `canonical_key`, consolidating, and joining `consolidated.descriptions`; pass the same `company_name`, omit `domain_name`, construct the filter via the pipeline-equivalent config path, and call the real `ThesisFilter.classify(..., skip_llm=True)` entrypoint.
- Emit an auxiliary whole-baseline census over the live 614 rows / 588 keys for denominator, drift, and survivorship context, clearly labeled non-canonical.
- Explicitly ban `DiscoveryPipeline.initialize()`, `process_pending()`, `SignalStore`, full `Pipeline`, and any path that can write shadow logs, classifications, run history, WAL side effects, or migrations.
- Explicitly ban real LLM and network calls during survivor analysis.
  Compute `llm_eligible` analytically from the effective config plus keyword score, and use `skip_llm=True` without touching the lazy LLM classifier.
- Direct matcher plus routing helpers may be used only for auxiliary or counterfactual comparison lanes; they are not canonical survivor evidence.
- For each analyzed row, capture at minimum:
  routing outcome, `decision_path_code`, keyword score, negative keywords, consumer-rescue indicators, effective config snapshot, matcher runtime metadata, and whether the row would have been LLM-eligible.
- Produce an artifact summarizing:
  canonical pending-company outcomes, auxiliary whole-baseline counts, repeated rejection motifs, and top suspected false-rejection clusters.
- If useful, compare the live 614-row output against the named 612-row backups read-only, but keep live 614 as canonical.

Acceptance criteria:

- Analysis reads the production DB only through a read-only SQLite URI.
- Canonical survivor-analysis pass matches production input assembly, uses real `ThesisFilter.classify(..., skip_llm=True)`, and does not supply `domain_name`.
- Canonical evidence unit is explicitly limited to consolidated pending companies; the whole 614-row / 588-key output is auxiliary context only.
- The filter used for canonical analysis is instantiated from the pipeline-equivalent config path, including post-construction `hold_threshold` mutation where applicable.
- No code path invoked during survivor analysis can create shadow logs, thesis classifications, run history, migrations, WAL side effects, or network traffic.
- `llm_eligible` is derived analytically from `skip_llm_if_keyword_below` and the keyword score; no real LLM call occurs.
- The artifact records matcher `v2_enablement` and `ml_enablement` state for rerun parity.
- Any matcher-plus-helper output is clearly labeled auxiliary/counterfactual and excluded from canonical evidence claims.
- Output artifact is saved outside `signals.db` and includes repeated evidence, not just anecdotal examples.
- Suspected over-rejection issues are grouped by mechanism, for example:
  LLM skip threshold, soft negative keyword penalty, hard-hold vs hard-reject behavior, or consumer-rescue guard failure.

Verification commands:

```powershell
python -c "import sqlite3; p=r'signals.db'; c=sqlite3.connect(f'file:{p}?mode=ro', uri=True); c.execute('select 1'); print('read-only-open-ok'); c.close()"
python scripts/thesis_filter_survivor_analysis.py --db signals.db --out artifacts/thesis_filter_survivor_analysis_614.json
python -c "import json; r=json.load(open('artifacts/thesis_filter_survivor_analysis_614.json', encoding='utf-8')); print({'baseline_rows': r['baseline']['signal_rows'], 'baseline_keys': r['baseline']['canonical_keys'], 'pending_rows': r['baseline']['pending_rows'], 'canonical_unit': r['metadata'].get('canonical_unit'), 'canonical_entrypoint': r['metadata'].get('canonical_entrypoint'), 'has_effective_config_snapshot': 'effective_config_snapshot' in r.get('metadata', {}), 'matcher_v2_enablement': r['metadata'].get('matcher_runtime', {}).get('v2_enablement'), 'matcher_ml_enablement': r['metadata'].get('matcher_runtime', {}).get('ml_enablement'), 'domain_name_mode': r['metadata'].get('domain_name_mode'), 'llm_calls_made': r['metadata'].get('llm_calls_made')})"
pytest tests/test_thesis_filter_survivor_analysis.py -q
```

Implementation note:

- Prefer a dedicated read-only script for this phase instead of reusing `scripts/analyze_pipeline_thesis.py` unchanged, because the current script is CSV-based rather than DB-baseline-based.
- The script should expose artifact metadata proving parity and safety, for example:
  `canonical_unit="pending_consolidated_company"`,
  `canonical_entrypoint="ThesisFilter.classify(skip_llm=True)"`,
  `effective_config_snapshot`,
  `matcher_runtime={"v2_enablement": ..., "ml_enablement": ...}`,
  `domain_name_mode="omitted_for_canonical_parity"`,
  and `llm_calls_made=0`.
- If the script includes matcher-plus-helper comparisons, those outputs must be separated and labeled as auxiliary or counterfactual, not canonical.
- `tests/test_thesis_filter_survivor_analysis.py` must verify:
  read-only DB open,
  canonical omission of `domain_name`,
  emitted metadata including canonical unit and matcher runtime,
  and non-use of `DiscoveryPipeline` / `SignalStore`.

### Phase 3: Proof Threshold And Patch Selection

Actions:

- Review the survivor artifact and choose only issues that meet the proof bar:
  repeated pattern, clear mechanism, bounded code touchpoint, and a realistic fix that does not expand scope.
- Reject speculative tuning ideas that depend on corpus calibration, live writes, or broad threshold re-optimization.

Acceptance criteria:

- Every chosen patch candidate maps to a specific code path in `utils/thesis_filter.py` or its matcher inputs.
- Each rejected candidate has a stated reason for deferral, such as insufficient evidence, legacy-only path, or calibration dependency.
- At most one to two tightly related over-rejection mechanisms are selected for the first patch slice.
- Any candidate that would require enabling real LLM calls, changing the canonical input assembly, or broad threshold retuning without repeated evidence is deferred.
- Any candidate whose apparent effect depends on unpinned matcher runtime state is deferred until rerun parity is established from artifact metadata.

Verification commands:

```powershell
rg -n "skip_llm_if_keyword_below|negative_keyword_penalty|_resolve_cascade_routing|_route_keyword_only|matched_hard_holds" utils/thesis_filter.py utils/thesis_matcher.py
```

### Phase 4: Focused Tests Before Code Changes

Actions:

- Add or extend unit tests to reproduce the proven survivor failure modes before any behavior patch.
- Use fixtures, stubs, or temp DBs only; do not point tests at production `signals.db`.
- Prefer the smallest existing test file that already covers the target mechanism.

Acceptance criteria:

- New tests fail before the fix and pass after it.
- Tests assert both the intended rescued outcome and a nearby non-target case that must remain unchanged.
- No new test depends on live DB contents or mutable external state.
- If the patch touches `_route_keyword_only()`, `_resolve_cascade_routing()`, cascade flags, routing decisions, or `decision_path_code` values, the regression boundary expands to include golden-set and cascade-routing verification.

Verification commands:

```powershell
pytest tests/utils/test_thesis_filter.py -q
pytest tests/utils/test_thesis_filter_cascade.py -q
pytest tests/utils/test_thesis_golden_set.py -q
pytest tests/utils/test_cascade_routing.py -q
pytest tests/test_analyze_pipeline_thesis.py -q
pytest tests/test_thesis_filter_survivor_analysis.py -q
```

### Phase 5: Narrow Behavior Patch And Regression Verification

Actions:

- Patch only the selected proven over-rejection mechanism.
- Keep changes local to the active thesis-filter path and avoid broad refactors.
- Re-run the focused thesis-filter tests and the survivor analysis artifact generation to confirm the targeted improvement.
- If the patch changes `_route_keyword_only()`, `_resolve_cascade_routing()`, cascade flags, routing decisions, or `decision_path_code` behavior, include golden-set and cascade-routing regression coverage before considering the slice verified.

Acceptance criteria:

- The targeted false-rejection cases improve in tests and in the survivor artifact.
- Non-targeted hard vetoes and hard holds retain expected behavior.
- Routing-code or decision-path changes are covered by `tests/utils/test_thesis_golden_set.py` and `tests/utils/test_cascade_routing.py`, in addition to targeted thesis-filter tests.
- No production DB writes occur during verification.

Verification commands:

```powershell
pytest tests/utils/test_thesis_filter.py tests/utils/test_thesis_filter_cascade.py tests/utils/test_thesis_filter_llm_integration.py -q
pytest tests/utils/test_thesis_golden_set.py tests/utils/test_cascade_routing.py -q
pytest tests/test_analyze_pipeline_thesis.py -q
pytest tests/test_thesis_filter_survivor_analysis.py -q
python scripts/thesis_filter_survivor_analysis.py --db signals.db --out artifacts/thesis_filter_survivor_analysis_614_postfix.json
python -c "import json; a=json.load(open('artifacts/thesis_filter_survivor_analysis_614.json', encoding='utf-8')); b=json.load(open('artifacts/thesis_filter_survivor_analysis_614_postfix.json', encoding='utf-8')); print({'pre_canonical': a['canonical_summary'], 'post_canonical': b['canonical_summary'], 'pre_matcher_runtime': a['metadata'].get('matcher_runtime'), 'post_matcher_runtime': b['metadata'].get('matcher_runtime'), 'post_llm_calls_made': b['metadata'].get('llm_calls_made')})"
```

## Risk Controls

### Read-Only DB Handling

- Use `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` for all production DB inspection.
- Do not use `DiscoveryPipeline.initialize()`, `process_pending()`, `SignalStore`, full `Pipeline`, or any command path that can create shadow logs, thesis classifications, run history, WAL side effects, telemetry persistence, or migrations against live `signals.db`.
- Write artifacts only to repo files such as `artifacts/` or `.omx/context/`; never to SQLite tables.
- If a later step truly needs mutable data for reproduction, use temp fixtures or a disposable DB copy, not the production file.
- Keep canonical and auxiliary outputs separated in the artifact so whole-baseline census data cannot be mistaken for active-path evidence.

### LLM And Network Isolation

- Canonical survivor analysis must not invoke the lazy LLM classifier property or any real network-capable dependency.
- Compute `llm_eligible` analytically from the effective instantiated config snapshot and the keyword score.
- Canonical diagnosis must run through `ThesisFilter.classify(..., skip_llm=True)`; direct matcher plus routing helpers are permitted only for auxiliary or counterfactual comparison.
- Treat any accidental LLM or network invocation during survivor analysis as a stop condition and invalidate the run artifact.

### Matcher Runtime Parity

- Record matcher runtime state in every survivor artifact, at minimum `v2_enablement` and `ml_enablement`.
- Treat reruns with mismatched matcher runtime metadata as non-comparable until reconciled.
- Do not attribute survivor deltas to thesis-filter patches if matcher runtime parity is not pinned.

### Overfitting Controls

- Require repeated examples per suspected issue, not one-off anecdotes.
- Pair every “should rescue” test with at least one adjacent “should still hold/reject” control.
- Avoid retuning global thresholds unless the survivor artifact shows a consistent failure band and the fix can be isolated.
- Treat pre/post survivor-rate deltas as diagnostic, not as promotion evidence.

### Stop Conditions

- Stop if the analysis cannot be performed without write-capable code paths.
- Stop if exact production input parity cannot be preserved for the canonical pass.
- Stop if the canonical lane cannot use the real `ThesisFilter.classify(..., skip_llm=True)` entrypoint with pipeline-equivalent config construction.
- Stop if survivor analysis would require invoking the lazy LLM classifier or any real network call.
- Stop if the observed problem is corpus quality, missing descriptions, or stale evidence rather than thesis-filter routing.
- Stop if the only apparent fixes require runner, keepalive, live Phase G, or corpus-calibration scope.

## Expected Deliverables

1. Read-only survivor-analysis artifact at `artifacts/thesis_filter_survivor_analysis_614.json`, with canonical pending-company results plus auxiliary whole-baseline census.
2. Short evidence note identifying proven over-rejection mechanisms and deferred hypotheses.
3. Focused tests for each approved fix.
4. Narrow patch limited to the active thesis-filter path.
