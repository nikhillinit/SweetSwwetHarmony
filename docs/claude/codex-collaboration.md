# Codex/Kimi Collaboration & Forensic Engineer Workflow

Multi-LLM collaboration patterns for Claude Code + Codex CLI or Kimi API.

## Architecture

```
+------------------+     task      +------------------+
|   Claude Code    |-------------->|  Codex CLI       |
|  (Orchestrator   |               |    (Sandbox)     |
|   + Critic)      |<--------------|       OR         |
+------------------+    proposal   |  Kimi API        |
         |                         |  (256K context)  |
         | critique                +------------------+
         +-------------------------------^
              (iterate until consensus)
```

- **Claude Code** orchestrates all actions
- **Codex CLI** provides sandbox-isolated proposals (read-only mode)
- **Kimi API** provides alternative backend with 256K context window
- **Consensus patterns** reduce hallucinations via iterative critique

## Key Files

| File | Purpose |
|------|---------|
| `integrations/maestro.py` | Iterative consensus orchestrator (collaborate + forensic) |
| `integrations/codex_wrapper.py` | Codex CLI wrapper (sandbox execution) |
| `integrations/kimi_client.py` | Kimi API client (large context, cost-effective) |

## Setup

### Codex (default)
```bash
# Install Codex CLI with ChatGPT Pro
npm install -g @openai/codex
codex login
```

### Kimi (alternative)
```bash
# Add to .env
KIMI_API_KEY=sk-xxx  # Get at https://platform.moonshot.cn/console/api-keys

# Test connection
python -m integrations.kimi_client check
```

## Workflows

### 1. Standard Collaboration (iterate until consensus)

```bash
# Using Codex (default)
python -m integrations.maestro collaborate \
    "Improve thesis matcher false positive rate" \
    --context "Currently at 30% FP, mostly B2B tools" \
    --max-iterations 5

# Using Kimi
python -m integrations.maestro collaborate \
    "Improve thesis matcher false positive rate" \
    --context "Currently at 30% FP, mostly B2B tools" \
    --max-iterations 5 \
    --use-kimi
```

```python
from integrations.maestro import Maestro

# Using Codex (default)
maestro = Maestro(max_iterations=5)
result = await maestro.collaborate(
    task="Reduce false positives",
    context="30% FP rate, B2B tools passing filter",
    context_files=["utils/thesis_matcher.py"]
)

# Using Kimi
maestro = Maestro(max_iterations=5, use_kimi=True)
result = await maestro.collaborate(...)
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
# Using Codex (default)
python -m integrations.maestro forensic \
    "Add rate limiting to GitHub collector" \
    --context "Currently no rate limiting, hitting 403s" \
    --requirements "1. Respect 5000 req/hr limit 2. Exponential backoff 3. Tests pass" \
    --files collectors/github.py \
    --docs docs/forensic-rate-limiting.md

# Using Kimi (256K context for large codebases)
python -m integrations.maestro forensic \
    "Add rate limiting to GitHub collector" \
    --context "Currently no rate limiting, hitting 403s" \
    --requirements "1. Respect 5000 req/hr limit 2. Exponential backoff 3. Tests pass" \
    --files collectors/github.py \
    --docs docs/forensic-rate-limiting.md \
    --use-kimi
```

#### Python Usage

```python
from integrations.maestro import Maestro

# Using Codex (default)
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

# Using Kimi
maestro = Maestro(use_kimi=True)
result = await maestro.forensic_collaborate(...)

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

## Codex vs Kimi: When to Use Each

| Scenario | Backend | Why |
|----------|---------|-----|
| Sandbox execution needed | Codex | Kimi has no sandbox isolation |
| Large codebase analysis | Kimi | 256K context window |
| Cost-sensitive | Kimi | $0.60/M input vs ChatGPT Pro |
| Offline/no ChatGPT Pro | Kimi | API-only, no CLI required |
| Complex reasoning chains | Kimi (kimi-k2-thinking) | Extended reasoning model |
| Default dev/debug | Either | Both work well for forensic workflow |

## Kimi Models

| Model | Best For | Context |
|-------|----------|---------|
| `kimi-k2.5` | General analysis (default) | Standard |
| `kimi-k2-thinking` | Complex reasoning | Extended |
| `moonshot-v1-128k` | Large context analysis | 128K tokens |
| `moonshot-v1-32k` | Balanced tasks | 32K tokens |

## Benefits

### Codex
- **No API costs** - Uses ChatGPT Pro subscription via Codex CLI
- **Sandbox isolation** - Codex runs in read-only mode

### Kimi
- **Large context** - Up to 256K tokens for whole-repo analysis
- **Cost-effective** - $0.60/M input, $2.50/M output
- **No CLI required** - Pure API, works anywhere

### Both
- **Iterative refinement** - Multiple rounds improve quality
- **Critical evaluation** - Claude scrutinizes, doesn't blindly accept
- **Audit trail** - Forensic docs capture the decision process
