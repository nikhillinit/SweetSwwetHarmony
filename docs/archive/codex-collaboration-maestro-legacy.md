# Codex / OpenAI collaboration

Maestro workflow and multi-LLM collaboration guidance.

## OpenAI/Codex Integration

Multi-LLM strategy iteration for thesis refinement using your ChatGPT Pro subscription.

### Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Claude Code   │────▶│  OpenAI/Codex   │
│  (Orchestrator) │◀────│  (Perspectives) │
└─────────────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│    Consensus    │
│   Synthesizer   │
└─────────────────┘
```

- **Claude Code** orchestrates all actions
- **OpenAI/Codex** provides alternative perspectives in sandbox
- **Consensus patterns** reduce hallucinations

### Key Files

| File | Purpose |
|------|---------|
| `integrations/maestro.py` | **Iterative consensus orchestrator** (Claude + Codex) |
| `integrations/codex_wrapper.py` | Codex CLI wrapper (sandbox execution) |
| `integrations/openai_mcp.py` | OpenAI MCP server (prompts + tools) |
| `integrations/strategy_iterator.py` | Legacy multi-LLM consensus |
| `scripts/setup_openai_integration.sh` | Setup and verification script |

### Setup

```bash
# 1. Run setup script
./scripts/setup_openai_integration.sh

# 2. (Required) Install Codex CLI with ChatGPT Pro
npm install -g @openai/codex
codex login
```

### Maestro Workflow (Iterative Consensus)

The Maestro pattern enables iterative collaboration:

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  ┌──────────────┐    task     ┌──────────────┐              │
│  │ Claude Code  │────────────▶│  Codex CLI   │              │
│  │ (Orchestrator│             │  (Sandbox)   │              │
│  │  + Critic)   │◀────────────│              │              │
│  └──────────────┘   proposal  └──────────────┘              │
│         │                            ▲                       │
│         │ critique                   │                       │
│         └────────────────────────────┘                       │
│              (iterate until consensus)                       │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Claude's critique focuses on:**
- **Feasibility**: Will this actually work? What assumptions are fragile?
- **Efficiency**: Is there a simpler/faster approach?
- **Sophistication**: What edge cases are missed? How to make it robust?

**Codex is instructed to:**
- Use existing Codex skills when helpful (`/edit`, `/review`, `/test`)
- Create new skills for reusable patterns
- Propose concrete, implementable solutions

### Usage

```bash
# CLI: Iterative collaboration
python -m integrations.maestro collaborate \
    "Improve thesis matcher false positive rate" \
    --context "Currently at 30% FP, mostly B2B tools slipping through" \
    --max-iterations 5

# CLI: Review with consensus
python -m integrations.maestro review collectors/github.py \
    --focus "rate limiting"

# Python: Direct usage
from integrations import Maestro

maestro = Maestro(max_iterations=5)
result = await maestro.collaborate(
    task="Reduce false positives in GitHub signals",
    context="30% FP rate, B2B tools passing thesis filter",
    context_files=["utils/thesis_matcher.py"]
)

print(f"State: {result.state}")
print(f"Iterations: {result.iterations}")
print(f"Skills used: {result.skills_employed}")
print(f"Final proposal:\n{result.final_proposal}")
```

### When Claude Should Use Maestro

When working on complex tasks, Claude should:
1. Send the task + context to Codex via Maestro
2. Receive Codex's proposal
3. **Critically evaluate** (not blindly accept):
   - What could fail? (feasibility)
   - What's overcomplicated? (efficiency)
   - What's missing? (sophistication)
4. Send critique back to Codex
5. Iterate until consensus or identify remaining disagreements
6. Present final agreed solution to user

### Benefits

- **No API costs** - Uses ChatGPT Pro subscription via Codex CLI
- **Sandbox isolation** - Codex runs in read-only mode
- **Iterative refinement** - Multiple rounds improve quality
- **Skill leverage** - Codex uses/creates skills for efficiency
- **Critical evaluation** - Claude scrutinizes, doesn't blindly accept
