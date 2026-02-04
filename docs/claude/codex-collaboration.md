# Codex Collaboration & Forensic Engineer Workflow

Multi-LLM collaboration patterns for Claude Code + Codex CLI.

## Architecture

```
+------------------+     task      +------------------+
|   Claude Code    |-------------->|    Codex CLI     |
|  (Orchestrator   |               |    (Sandbox)     |
|   + Critic)      |<--------------|                  |
+------------------+    proposal   +------------------+
         |                               ^
         | critique                      |
         +-------------------------------+
              (iterate until consensus)
```

- **Claude Code** orchestrates all actions
- **Codex CLI** provides sandbox-isolated proposals (read-only mode)
- **Consensus patterns** reduce hallucinations via iterative critique

## Key Files

| File | Purpose |
|------|---------|
| `integrations/maestro.py` | Iterative consensus orchestrator (collaborate + forensic) |
| `integrations/codex_wrapper.py` | Codex CLI wrapper (sandbox execution) |

## Setup

```bash
# Install Codex CLI with ChatGPT Pro
npm install -g @openai/codex
codex login
```

## Workflows

### 1. Standard Collaboration (iterate until consensus)

```bash
python -m integrations.maestro collaborate \
    "Improve thesis matcher false positive rate" \
    --context "Currently at 30% FP, mostly B2B tools" \
    --max-iterations 5
```

```python
from integrations.maestro import Maestro

maestro = Maestro(max_iterations=5)
result = await maestro.collaborate(
    task="Reduce false positives",
    context="30% FP rate, B2B tools passing filter",
    context_files=["utils/thesis_matcher.py"]
)
```

### 2. Forensic Engineer Workflow (4-phase structured)

The Forensic Engineer pattern provides structured collaboration through 4 mandatory phases:

```
+------------+     +------------+     +------------+     +------------+
| ANALYZE    |---->|   PLAN     |---->|  EXECUTE   |---->|   VERIFY   |
| (Iteration |     | (Iteration |     | (Iteration |     | (Iteration |
|    0)      |     |    1)      |     |    2)      |     |    3)      |
+------------+     +------------+     +------------+     +------------+
     |                  |                  |                  |
     v                  v                  v                  v
  Validate          Refine plan       Step-by-step      Check requirements
  assumptions       with findings     execution         are met
```

#### Phase Objectives

| Phase | Iteration | Objective | Claude's Critique Focus |
|-------|-----------|-----------|------------------------|
| ANALYZE | 0 | Validate assumptions against codebase | Accuracy, file references |
| PLAN | 1 | Convert to concrete, executable steps | Feasibility, specificity |
| EXECUTE | 2 | Execute steps with verification | Safety, preconditions |
| VERIFY | 3 | Confirm requirements are met | Coverage, regressions |

#### CLI Usage

```bash
python -m integrations.maestro forensic \
    "Add rate limiting to GitHub collector" \
    --context "Currently no rate limiting, hitting 403s" \
    --requirements "1. Respect 5000 req/hr limit 2. Exponential backoff 3. Tests pass" \
    --files collectors/github.py \
    --docs docs/forensic-rate-limiting.md
```

#### Python Usage

```python
from integrations.maestro import Maestro

maestro = Maestro()
result = await maestro.forensic_collaborate(
    task="Add rate limiting to GitHub collector",
    context="Currently no rate limiting, hitting 403 errors",
    requirements="""
    1. Respect GitHub's 5000 requests/hour limit
    2. Implement exponential backoff on 429/403
    3. All existing tests pass
    4. Add rate limit tests
    """,
    context_files=["collectors/github.py"],
    docs_path="docs/forensic-rate-limiting.md",
)

print(f"Final state: {result.final_state}")
for iteration in result.iterations:
    print(f"  {iteration.phase}: {len(iteration.findings)} findings")
```

## Claude's Critique Categories

Each Codex proposal is evaluated on:

| Category | Question | Example Issues |
|----------|----------|----------------|
| **Feasibility** | Will this actually work? | Missing dependencies, invalid APIs |
| **Efficiency** | Is there a simpler way? | N+1 queries, unnecessary complexity |
| **Sophistication** | What edge cases are missed? | No error handling, no tests |
| **Correctness** | Is the logic sound? | Wrong assumptions, flawed reasoning |

## Severity Levels

| Severity | Meaning | Action |
|----------|---------|--------|
| `blocking` | Must fix before proceeding | Iterate until resolved |
| `important` | Should fix, but can proceed after N iterations | Document if not fixed |
| `minor` | Nice to have | Note but don't block |

## Output Types

### ConsensusResult (collaborate)

```python
{
    "state": "agreed",           # agreed, partial, disagreed
    "final_proposal": "...",
    "iterations": 3,
    "history": [...],
    "agreed_points": [...],
    "remaining_disagreements": [...],
    "skills_employed": ["edit", "review"]
}
```

### ForensicResult (forensic_collaborate)

```python
{
    "task": "Add rate limiting...",
    "iterations": [
        {
            "phase": "analyze",
            "iteration_number": 0,
            "objective": "Validate assumptions...",
            "codex_response": "...",
            "claude_critique": {...},
            "findings": ["GitHub uses token bucket...", ...],
            "decisions": ["Use tenacity library...", ...]
        },
        # ... plan, execute, verify
    ],
    "final_state": "agreed",
    "agreed_points": [...],
    "remaining_issues": [...],
    "forensic_docs_path": "docs/forensic-rate-limiting.md"
}
```

## When to Use Each Workflow

| Scenario | Workflow | Why |
|----------|----------|-----|
| Open-ended strategy question | `collaborate` | Flexible iteration count |
| Code review | `collaborate` (or `review`) | Focus on specific file |
| New feature implementation | `forensic` | Structured phases prevent mistakes |
| Bug fix with root cause unknown | `forensic` | ANALYZE phase validates assumptions |
| Refactoring | `forensic` | VERIFY ensures no regressions |

## Benefits

- **No API costs** - Uses ChatGPT Pro subscription via Codex CLI
- **Sandbox isolation** - Codex runs in read-only mode
- **Iterative refinement** - Multiple rounds improve quality
- **Critical evaluation** - Claude scrutinizes, doesn't blindly accept
- **Audit trail** - Forensic docs capture the decision process
