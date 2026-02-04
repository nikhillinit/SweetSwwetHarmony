# Task Plan: Complete Forensic Engineer Alignment for Maestro/Codex

## Goal
Complete the Forensic Engineer workflow integration in Maestro/Codex to enable structured 4-phase collaboration (analyze -> plan -> execute -> verify) with Claude acting as orchestrator/critic and Codex providing sandbox-isolated proposals.

## Current Phase
Phase 5

## Phases

### Phase 1: Audit Current State
- [x] Read codex_wrapper.py - ForensicPhase enum and methods present
- [x] Read maestro.py - dataclasses present, forensic_collaborate() missing
- [x] Check docs/claude/codex-collaboration.md - does not exist
- [x] Check archive for legacy content - found in docs/archive/
- **Status:** complete

### Phase 2: Implement forensic_collaborate() Method
- [x] Add forensic_collaborate() method to Maestro class
- [x] Wire up to codex_wrapper.py forensic methods (analyze, plan, execute, verify)
- [x] Implement Claude critique between phases
- [x] Handle iteration state and phase transitions
- **Status:** complete

### Phase 3: Create Documentation
- [x] Create docs/claude/codex-collaboration.md with Forensic Engineer workflow
- [x] Include architecture diagram
- [x] Document CLI usage examples
- [x] Reference the 4-phase workflow pattern
- **Status:** complete

### Phase 4: Update CLI
- [x] Add `forensic` subcommand to maestro.py CLI
- [x] Support --context, --requirements, --files, --docs arguments
- [x] Add progress output for each phase
- **Status:** complete

### Phase 5: Testing & Verification
- [x] Verify Python syntax (py_compile passes)
- [x] Verify imports work (Maestro, ForensicResult, etc.)
- [x] Verify CLI help shows forensic command
- [x] Verify forensic subcommand --help shows all options
- **Status:** complete

## Key Questions
1. Should forensic_collaborate() be async like collaborate()? (Yes - uses async codex methods)
2. Should each phase require explicit Claude approval? (Yes - that's the point of the forensic pattern)
3. How to persist findings between phases? (ForensicIteration dataclass captures this)

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use existing dataclasses | ForensicPhase, ForensicIteration, ForensicResult already defined in maestro.py |
| Keep read-only sandbox | Codex proposes, Claude critiques and executes |
| 4 fixed phases | Matches Forensic Engineer pattern (analyze, plan, execute, verify) |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | - | - |

## Notes
- codex_wrapper.py already has analyze(), plan(), execute(), verify() methods (lines 382-607)
- maestro.py has ForensicPhase, ForensicIteration, ForensicResult dataclasses (lines 129-188)
- Legacy docs archived at docs/archive/codex-collaboration-maestro-legacy.md
