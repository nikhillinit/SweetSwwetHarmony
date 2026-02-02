# Task Plan: Phase 0A - Negative Keyword Policy v2

## Goal
Implement runtime controls and policy loading scaffolding for v2 negative keyword policy, with zero scoring behavior changes when disabled.

## Current Phase
Phase 1

## Phases

### Phase 1: PyYAML Dependency + RuntimeControls (Stage 0A-1)
- [ ] Verify/add `pyyaml>=6.0` in requirements.txt
- [ ] Create `utils/runtime_controls.py` with RuntimeControls dataclass
- [ ] Implement `from_env()` factory with normalization rules
- [ ] Implement membership validation + invariant enforcement
- [ ] Implement boolean parsing for V2_EXECUTION_ENABLED
- [ ] Handle legacy `enable_v2_policy` mapping
- [ ] Write 10-12 tests in `tests/utils/test_runtime_controls.py`
- **Status:** pending

### Phase 2: PolicyLoader (Stage 0A-2)
- [ ] Create `utils/policy_loader.py` with PolicySpec and PolicyBundle dataclasses
- [ ] Implement `resolve_policy_dir()` with explicit path validation
- [ ] Implement `load_policy_bundle()` with permissive/strict modes
- [ ] Handle YAML parsing with safe_load + mapping validation
- [ ] Implement marker-based directory discovery
- [ ] Write 10-12 tests in `tests/utils/test_policy_loader.py`
- **Status:** pending

### Phase 3: ThesisMatcher Wiring (Stage 0A-3)
- [ ] Update `utils/thesis_matcher.py` __init__ signature
- [ ] Wire RuntimeControls into ThesisMatcher
- [ ] Implement validate-before-I/O pattern
- [ ] Implement zero-cost when disabled (no I/O)
- [ ] Implement shallow-copy contract for config
- [ ] Write 5-6 wiring tests
- **Status:** pending

### Phase 4: Integration Testing + Verification
- [ ] Run full test suite
- [ ] Verify all 11 bug hazards are addressed
- [ ] Verify sign-off conditions (SO-1 through SO-7)
- [ ] Run pytest with all new test files
- **Status:** pending

### Phase 5: Commit + Documentation
- [ ] Review all changes
- [ ] Create commit with proper message
- [ ] Update memory-keeper context
- **Status:** pending

## Key Questions
1. Is PyYAML already in requirements.txt? → Need to verify
2. What is the repo root relative to utils/? → parents[1] per plan
3. Does config/v2 directory exist for marker? → Need to check

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use frozen=True for PolicySpec | Immutable specs prevent accidental modification |
| Separate missing_required vs load_errors | Bug #2 mitigation - different failure modes |
| Empty env → unset (not empty string) | Bug #11 mitigation - normalize edge cases |

## Bug Hazards to Address (from plan)
| # | Hazard | Mitigation |
|---|--------|------------|
| 1 | permissive mode crashes on YAML parse errors | try/except + load_errors |
| 2 | missing files conflated with load/parse errors | separate fields |
| 3 | misleading security claims | no claims |
| 4 | env var pitfalls | centralized normalization |
| 5 | config mutation | shallow copy |
| 6 | membership validation | explicit checks |
| 7 | strict early exit | aggregate + raise once |
| 8 | config_path validation | validate before I/O |
| 9 | silent fallback | marker required |
| 10 | env path symmetry | same validation |
| 11 | empty env + generator | tuple(specs) |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|

## Notes
- Update phase status as you progress: pending → in_progress → complete
- Re-read this plan before major decisions
- Log ALL errors - they help avoid repetition
- Sign-off conditions (SO-1 through SO-7) must all pass
