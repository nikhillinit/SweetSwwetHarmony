# Findings & Decisions - Phase 0A

## Requirements
- RuntimeControls: Centralized env/arg parsing with normalization
- PolicyLoader: YAML loading with permissive/strict modes
- ThesisMatcher wiring: Backward-compatible signature changes
- Zero scoring behavior changes when v2 disabled
- All 11 bug hazards must be mitigated

## Research Findings

### Existing Codebase State (2026-02-01)
- `utils/thesis_matcher.py` exists with 700 lines
- Current `__init__` takes only `custom_keywords` parameter
- No existing v2 policy infrastructure
- `config/negative_keyword_policy.yaml` exists (v2.0, 850 lines)
- `requirements.txt` does NOT have pyyaml (need to add)

### ThesisMatcher Current Signature
```python
def __init__(
    self,
    custom_keywords: Optional[Dict[ConsumerThesis, Dict[str, float]]] = None,
):
```

### Plan Requirements for New Signature
```python
def __init__(
    self,
    custom_keywords=None,
    *,
    enable_v2_policy: bool | None = None,  # legacy
    v2_enablement: str | None = None,
    policy_loader_mode: str | None = None,
    v2_execution_enabled: bool | None = None,
    config_path: str | None = None,
):
```

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| RuntimeControls as separate module | Single responsibility, easier testing |
| PolicyBundle dataclass (not frozen) | Needs mutable fields for aggregation |
| PolicySpec frozen=True | Specs are immutable configuration |
| tuple(specs) at entry | Bug #11 - prevent generator exhaustion |
| Shallow copy for config | Bug #5 - documented limitation |

## Key Constants (from plan)

### Membership Values
- `loader_mode`: {"permissive", "strict"}
- `enablement`: {"disabled", "shadow", "live"}

### Precedence Rules
1. Explicit kwargs (highest)
2. Legacy `enable_v2_policy` mapped
3. Environment variables
4. Defaults (lowest)

### Legacy Mapping
- `enable_v2_policy=True` → enablement="shadow"
- `enable_v2_policy=False` → enablement="disabled"

### Invariants
- shadow/live → loader_mode must be "strict"
- live → v2_execution_enabled must be True

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
- Plan file: C:\Users\nikhi\.claude\plans (original plan location)
- Config dir: C:\dev\Harmonic\config\
- Utils dir: C:\dev\Harmonic\utils\
- Tests dir: C:\dev\Harmonic\tests\utils\

## File Paths to Create
- `utils/runtime_controls.py`
- `utils/policy_loader.py`
- `tests/utils/test_runtime_controls.py`
- `tests/utils/test_policy_loader.py`

## File Paths to Modify
- `requirements.txt` (add pyyaml>=6.0)
- `utils/thesis_matcher.py` (add v2 wiring)

---
*Update this file after every 2 view/browser/search operations*
