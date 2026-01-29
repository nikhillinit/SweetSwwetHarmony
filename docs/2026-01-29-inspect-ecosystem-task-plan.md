# Task Plan: Inspect Ecosystem Deep Dive & Discovery Engine Integration

**Created:** 2026-01-29
**Goal:**
1. Deep understanding of Inspect AI, Inspect Flow, and Inspect Scout architecture
2. Identify and design integration with Discovery Engine for LLM evaluation

---

## Current Phase
Phase 5: Proof of Concept Design - IN PROGRESS

## Phases

### Phase 1: Inspect AI Deep Dive `status: complete`
**Objective:** Understand core evaluation framework architecture

- [x] Explore src/ structure and key modules
- [x] Understand Dataset → Solver → Scorer pattern
- [x] Study tool integration (MCP, bash, python execution)
- [x] Review model provider abstraction
- [x] Examine example evaluations

**Key Findings:**
- Task combines Dataset + Solver + Scorer into evaluation unit
- 24 model providers via @modelapi decorator
- Tool system with @tool decorator, MCP integration, sandboxing
- GenerateConfig with 30+ options for model configuration

### Phase 2: Inspect Flow Analysis `status: complete`
**Objective:** Understand workflow orchestration layer

- [x] Study FlowSpec and FlowTask types
- [x] Understand matrix functions for parameter sweeping
- [x] Review launcher and runner modules
- [x] Examine virtual environment management

**Key Findings:**
- FlowSpec = declarative evaluation config
- tasks_matrix(), models_matrix(), configs_matrix() for Cartesian product
- Auto venv creation with dependency resolution
- Integrates with Inspect AI via eval_set()

### Phase 3: Inspect Scout Investigation `status: complete`
**Objective:** Understand transcript analysis capabilities

- [x] Study Scanner architecture (LLM, Grep, Custom)
- [x] Understand Transcript data model
- [x] Review validation workflow
- [x] Examine Scout View UI architecture

**Key Findings:**
- @scanner decorator for registration
- llm_scanner() for LLM-based analysis
- grep_scanner() for pattern matching
- ValidationSet for ground truth comparison
- Loads transcripts from Inspect AI eval logs

### Phase 4: Discovery Engine Integration Mapping `status: complete`
**Objective:** Map integration opportunities

- [x] Identify DE components using LLMs (thesis_matcher, llm_classifier)
- [x] Design evaluation task for thesis classification
- [x] Consider Scout for analyzing DE signal quality
- [x] Map existing labeled data (Notion Funded/Passed)

**Key Findings:**
- LLM Classifier is primary evaluation candidate
- Notion Funded/Passed = ground truth source
- Scout can analyze classification reasoning quality
- Flow enables model comparison sweeps

### Phase 5: Proof of Concept Design `status: pending`
**Objective:** Create concrete integration plan

- [ ] Draft evaluation task for thesis matching
- [ ] Design scanner for DE signal quality
- [ ] Create integration architecture diagram
- [ ] Document implementation roadmap

---

## Previous Research Context

Prior research (2026-01-29) identified evaluation harness as #1 priority gap:
- Discovery Engine has 445+ tests but no thesis classification benchmark
- Recommended: 100+ labeled examples from Notion Funded/Passed
- Chain-of-thought and self-critique patterns recommended

---

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|

## Files
| File | Purpose |
|------|---------|
| task_plan.md | This file - tracking progress |
| findings.md | Research findings + architecture notes |
| progress.md | Session log |
| inspect_ai-main/ | Inspect AI source code |
| inspect_flow-main/ | Inspect Flow source code |
| inspect_scout-main/ | Inspect Scout source code |
