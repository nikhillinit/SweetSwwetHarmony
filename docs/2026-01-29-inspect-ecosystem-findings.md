# Findings: Inspect Ecosystem & Discovery Engine Integration

## Purpose
Deep analysis of Inspect AI, Inspect Flow, and Inspect Scout for integration with Discovery Engine.

---

## INSPECT AI - Core Evaluation Framework

### Architecture Overview

**Core Pattern: Dataset → Solver → Scorer**

```
eval(tasks, model)
    ↓
Task(dataset, solver, scorer)
    ↓
For each Sample:
    TaskState → Solver(state, generate) → Scorer(state, target) → Score
    ↓
Aggregate metrics → EvalLog
```

### Key Components

| Component | File | Interface |
|-----------|------|-----------|
| **Task** | `_eval/task/task.py` | Combines dataset + solver + scorer |
| **Dataset** | `dataset/_dataset.py` | `Sequence[Sample]` with filter/sort |
| **Sample** | `dataset/_dataset.py` | input, target, metadata, sandbox |
| **Solver** | `solver/_solver.py` | `async(TaskState, Generate) -> TaskState` |
| **Scorer** | `scorer/_scorer.py` | `async(TaskState, Target) -> Score` |
| **TaskState** | `solver/_task_state.py` | Mutable container: messages, output, tools |

### Model Provider Abstraction

**24 providers supported** via `@modelapi` decorator:
- Proprietary: openai, anthropic, google, mistral, groq, xai
- OpenAI-compatible: ollama, vllm, together, openrouter
- Local: huggingface, transformer_lens
- Testing: mockllm

**GenerateConfig** - 30+ options:
```python
GenerateConfig(
    temperature=0.7,
    max_tokens=1000,
    reasoning_effort="medium",  # For reasoning models
    cache_prompt="auto",        # Anthropic caching
    response_schema=MySchema,   # Structured output
)
```

### Tool System

**Definition via `@tool` decorator:**
```python
@tool
def my_tool() -> Tool:
    async def execute(param: str) -> str:
        """Docstring → JSON Schema."""
        return result
    return execute
```

**Built-in tools:**
- Execution: `bash()`, `python()`, `bash_session()`
- Information: `web_search()`, `web_browser()`
- Editing: `text_editor()`
- Reasoning: `think()`, `update_plan()`

**MCP Integration:**
```python
server = mcp_server_stdio(command="python", args=["-m", "mcp_server"])
tools = mcp_tools(server, tools=["tool_*"])  # Glob patterns
```

### Sandboxing

**SandboxEnvironment ABC:**
- `exec()` - Run commands with timeout
- `read_file()` / `write_file()` - Filesystem access
- Resource limits: 10 MiB output, 100 MiB file read

**Implementations:** Local (testing), Docker (production)

---

## INSPECT FLOW - Workflow Orchestration

### Core Concept

**FlowSpec** - Declarative evaluation configuration:
```python
FlowSpec(
    log_dir="logs",
    tasks=[
        FlowTask(name="inspect_evals/gpqa", model="openai/gpt-4o"),
        FlowTask(name="my_eval", model="anthropic/claude-3-5-sonnet"),
    ],
)
```

### Matrix Functions (Parameter Sweeping)

```python
FlowSpec(
    tasks=tasks_matrix(
        task=["eval1", "eval2"],
        model=models_matrix(
            model=["gpt-4o", "claude-3-5"],
            config=configs_matrix(
                reasoning_effort=["low", "medium", "high"],
            ),
        ),
    ),
)
# Generates: 2 tasks × 2 models × 3 configs = 12 evaluations
```

### Key Modules

| Module | Purpose |
|--------|---------|
| `_types` | FlowSpec, FlowTask, FlowModel definitions |
| `_config` | Config loading and validation |
| `_launcher` | Venv creation, dependency resolution |
| `_runner` | Flow execution (inproc or subprocess) |

### Dependency Management

Flow automatically:
1. Creates isolated virtual environment per spec
2. Infers dependencies from task/model names
3. Installs packages (e.g., `inspect-evals`, `openai`)
4. Runs evaluations in isolated context

---

## INSPECT SCOUT - Transcript Analysis

### Core Concept

**Scanners** analyze transcripts for patterns:
```python
@scanner(messages="all")
def detect_refusal() -> Scanner[Transcript]:
    return llm_scanner(
        question="Does the model refuse to answer?",
        answer="boolean"
    )
```

### Scanner Types

| Type | Use Case |
|------|----------|
| `llm_scanner()` | LLM-powered analysis (nuanced judgments) |
| `grep_scanner()` | Pattern matching (fast, exact) |
| Custom | Any async function returning Result |

### Transcript Data Model

```python
Transcript:
    - messages: list[ChatMessage]
    - events: list[Event]
    - metadata: dict
    - task_set: str
    - sample_id: str
```

### Validation Workflow

```
Human Labels → Validation Set → Compare with Scanner Results → Metrics
```

- Create validation sets in Scout View UI
- Apply during scanning: `scan(..., validation="eval-awareness.csv")`
- Measures scanner accuracy against ground truth

### Parallel Processing

- Batch scanning with configurable parallelism
- Fault tolerance: resume interrupted scans
- S3 support for distributed storage

---

## INTEGRATION OPPORTUNITIES WITH DISCOVERY ENGINE

### Discovery Engine LLM Components

| Component | File | LLM Usage | Evaluation Candidate |
|-----------|------|-----------|---------------------|
| **Thesis Matcher** | `utils/thesis_matcher.py` | Keyword scoring | Baseline (no LLM) |
| **LLM Classifier** | `consumer/thesis_filter/llm_classifier.py` | Gemini classification | **HIGH PRIORITY** |
| **Signal Consolidator** | `utils/signal_consolidator.py` | Conflict resolution | Medium |
| **Exit Predictor** | `utils/exit_predictor.py` | Heuristic scoring | Low (no LLM yet) |

### Integration Architecture

```
Discovery Engine                    Inspect Ecosystem
─────────────────                   ─────────────────

┌─────────────────┐                 ┌─────────────────┐
│ Notion Database │◄────────────────│  Inspect AI     │
│ (Funded/Passed) │  Ground Truth   │  Evaluation     │
└────────┬────────┘                 │  Framework      │
         │                          └────────┬────────┘
         ▼                                   │
┌─────────────────┐                          │
│ LLM Classifier  │◄─────────────────────────┘
│ (thesis_filter) │   Evaluate classification
└────────┬────────┘   accuracy (F1, precision)
         │
         ▼                          ┌─────────────────┐
┌─────────────────┐                 │  Inspect Flow   │
│ Signal Pipeline │◄────────────────│  Orchestration  │
│ (run_pipeline)  │   Parameter     └─────────────────┘
└────────┬────────┘   sweeping:
         │            - models (Gemini vs GPT)
         ▼            - thresholds (0.25-0.40)
┌─────────────────┐   - prompts (CoT, self-critique)
│ Classification  │
│ Decisions       │                 ┌─────────────────┐
└────────┬────────┘                 │  Inspect Scout  │
         │                          │  Transcript     │
         ▼                          │  Analysis       │
┌─────────────────┐                 └────────┬────────┘
│ Reasoning Logs  │◄────────────────────────┘
│ (thesis_class.) │   Scan for:
└─────────────────┘   - False positive patterns
                      - B2B misclassification
                      - Reasoning quality
```

### Proposed Evaluations

#### 1. Thesis Classification Benchmark (Inspect AI)

**Dataset:** Extract from Notion
- Funded companies → `target: "QUALIFIED"`
- Passed companies → `target: "REJECTED"`
- Tracking companies → `target: "HELD"`

**Task Definition:**
```python
from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.solver import chain, chain_of_thought, generate
from inspect_ai.scorer import match

@task
def thesis_classification():
    return Task(
        dataset=json_dataset("datasets/thesis_ground_truth.jsonl"),
        solver=chain(chain_of_thought(), generate()),
        scorer=match(),
    )
```

**Metrics:**
- Accuracy (overall)
- Precision/Recall per class
- F1 score
- False positive rate (B2B slipping through)

#### 2. Model Comparison (Inspect Flow)

```python
from inspect_flow import FlowSpec, tasks_matrix, models_matrix

FlowSpec(
    log_dir="logs/thesis_eval",
    tasks=tasks_matrix(
        task="thesis_classification",
        model=models_matrix(
            model=[
                "google/gemini-1.5-flash",
                "google/gemini-1.5-pro",
                "openai/gpt-4o-mini",
                "openai/gpt-4o",
            ],
            config=configs_matrix(
                temperature=[0.0, 0.3, 0.7],
            ),
        ),
    ),
)
```

#### 3. Classification Transcript Analysis (Inspect Scout)

**Scanner for B2B False Positives:**
```python
from inspect_scout import scanner, llm_scanner, Transcript

@scanner(messages="all")
def b2b_misclassification() -> Scanner[Transcript]:
    return llm_scanner(
        question="""Analyze this thesis classification decision.

        Is there evidence the model incorrectly classified a B2B/enterprise
        company as consumer? Look for:
        - "API", "developer", "platform", "enterprise" in description
        - Model reasoning that missed B2B signals
        """,
        answer="boolean"
    )
```

**Scanner for Reasoning Quality:**
```python
@scanner(messages="all")
def reasoning_quality() -> Scanner[Transcript]:
    return llm_scanner(
        question="""Rate the quality of the thesis classification reasoning:

        - Did it identify the correct industry category?
        - Did it correctly assess consumer vs B2B focus?
        - Was the thesis fit score justified?
        """,
        answer=["excellent", "good", "fair", "poor"]
    )
```

### Implementation Roadmap

| Phase | Task | Effort | Dependency |
|-------|------|--------|------------|
| **P1** | Export Notion ground truth to JSONL | 2-3 hrs | Notion API |
| **P2** | Create thesis_classification task | 2-3 hrs | P1 |
| **P3** | Run baseline eval with current prompt | 1 hr | P2 |
| **P4** | Create Flow spec for model comparison | 2 hrs | P3 |
| **P5** | Run parameter sweep (models × temps) | 4 hrs | P4 |
| **P6** | Create Scout scanners for error analysis | 3 hrs | P5 |
| **P7** | Integrate winning config into llm_classifier | 2 hrs | P6 |

### Expected Outcomes

1. **Quantified accuracy** - Know exact F1 score for thesis classification
2. **Best model selection** - Data-driven choice between Gemini/GPT variants
3. **Optimal temperature** - Find balance between consistency and nuance
4. **Error patterns** - Understand systematic false positive causes
5. **Improved prompts** - Chain-of-thought and self-critique tested

---

## Previous Research Summary

---

## Key Takeaways for Discovery Engine

### High Priority (Implement This Sprint)

| # | Takeaway | Rationale | Component Affected |
|---|----------|-----------|-------------------|
| 1 | **Build evaluation harness for thesis classification** | Meridian's core insight: systematic eval beats ad-hoc tuning. Extract 100+ labeled examples from Notion Funded/Passed. | `tests/evaluation/`, new |
| 2 | **Add chain-of-thought reasoning to LLM classifier** | Current single-call approach lacks auditability. Store reasoning traces in thesis_classifications table. | `consumer/thesis_filter/llm_classifier.py` |
| 3 | **Implement self-critique loop for borderline cases** | When thesis_fit 0.25-0.35, add second LLM pass to reduce false negatives. | `consumer/thesis_filter/llm_classifier.py` |
| 4 | **Add similar company discovery** | Sourcescrub charges 5 credits (highest after Source). Embed company descriptions → vector DB → cosine similarity. | New `utils/similarity_search.py` |
| 5 | **Build human review queue for low-confidence signals** | Both APIs make research requests FREE. Route confidence < 0.4 to human queue instead of dropping. | `storage/signal_store.py`, new CLI command |
| 6 | **Implement confidence scoring per field** | Grata shows per-field confidence, not just signal-level. Enables better conflict resolution. | `utils/signal_consolidator.py` |

### Medium Priority (Next Sprint)

| # | Takeaway | Rationale | Component Affected |
|---|----------|-----------|-------------------|
| 7 | **Add employee growth tracking** | Grata tracks 1mo/3mo/6mo/1yr growth rates. Strong "why now" signal. Store historical counts. | `collectors/linkedin.py`, new table |
| 8 | **Enhance search with boolean keyword groups** | Current flat keyword lists cause false positives. Support AND/OR + "core business" depth. | `utils/thesis_matcher.py` |
| 9 | **Scout-style auto-enrichment** | When signal has company_name but no website, trigger web search. | New `utils/auto_enricher.py` |
| 10 | **Declarative pipeline configuration** | Replace CLI args with YAML. Enables reproducible runs and A/B testing. | `config/pipeline.yaml`, new |
| 11 | **Add reasoning traces to signal processing** | Log chain-of-thought for each major decision. New `signal_audit_log` table. | `storage/signal_store.py` |
| 12 | **Conference/award signal collector** | Sourcescrub charges 10 credits (HIGHEST). Track: speaker > sponsor > attendee. | New `collectors/events.py` |

### Low Priority (Backlog)

| # | Takeaway | Rationale | Component Affected |
|---|----------|-----------|-------------------|
| 13 | **Multi-model consensus** | For borderline cases, query Gemini + GPT-4o. Flag disagreements for human review. | Maestro integration |
| 14 | **Vector DB for semantic search** | ChromaDB/Weaviate for "find companies like X" queries. | New integration |
| 15 | **Formal NAICS industry classification** | Better B2B filtering. Current thesis matching is sufficient. | Later |
| 16 | **Relationship intelligence layer** | Requires email/calendar access. Defer until in scope. | Future |
| 17 | **Automated research brief generation** | LLM synthesizes signals into investment memos. | New `utils/research_brief_generator.py` |

---

## Architecture Validation

Research confirms Discovery Engine architecture aligns with industry patterns:

| Pattern | Industry Approach | Discovery Engine Status |
|---------|-------------------|------------------------|
| Two-stage classification | Keyword → LLM | ✅ Implemented (thesis_matcher + llm_classifier) |
| Multi-source ingestion | Scrapy/APIs | ✅ 13 collectors |
| Structured extraction | LLM + Pydantic | ✅ thesis_classifications table |
| Signal consolidation | Synthesis agents | ✅ signal_consolidator.py |
| Verification gate | Confidence scoring | ✅ verification_gate_v2.py |
| Semantic search | Vector DB + embeddings | ❌ Not implemented (priority add) |
| Evaluation harness | Labeled benchmarks | ❌ Not implemented (biggest gap) |

---

## Biggest Gap Identified

**Systematic evaluation is missing.** Discovery Engine has 445+ tests but no benchmark measuring real-world thesis classification accuracy. Building an eval harness with ground-truth data from Notion would transform optimization from guesswork to data-driven tuning.

---

## Action Items

| # | Item | Priority | Component |
|---|------|----------|-----------|
| 1 | Create evaluation harness with 100+ labeled examples from Notion | HIGH | New `tests/evaluation/` |
| 2 | Add chain-of-thought to llm_classifier.py | HIGH | `consumer/thesis_filter/` |
| 3 | Implement self-critique loop for 0.25-0.35 thesis_fit | HIGH | `consumer/thesis_filter/` |
| 4 | Add similarity_search.py with embeddings | HIGH | New `utils/` |
| 5 | Build human review queue table + CLI | HIGH | `storage/`, CLI |
| 6 | Add per-field confidence to ConsolidatedSignal | HIGH | `utils/signal_consolidator.py` |
| 7 | Design employee_count_history table | MEDIUM | New migration |
| 8 | Create config/pipeline.yaml schema | MEDIUM | New config |
| 9 | Add signal_audit_log table | MEDIUM | New migration |

---

## Inspect AI Framework Details (from inspect.aisi.org.uk)

### Overview
Inspect AI is an open-source LLM evaluation framework from the UK AI Safety Institute. It provides the exact infrastructure needed for Discovery Engine's thesis classification evaluation harness.

**Install:** `pip install inspect-ai`

### Core Components

| Component | Purpose | Discovery Engine Application |
|-----------|---------|------------------------------|
| **Tasks** | Combine dataset + solver + scorer | Thesis classification task |
| **Datasets** | Labeled samples with input/target | 100+ companies from Notion Funded/Passed |
| **Solvers** | Chain processing steps | `chain_of_thought()` → `generate()` → `self_critique()` |
| **Scorers** | Evaluate outputs | `match()` for QUALIFIED/HELD/REJECTED |

### Key Solvers for Thesis Classification

```python
from inspect_ai.solver import chain, chain_of_thought, generate, self_critique

# Chain-of-thought + self-critique pattern
solver = chain(
    chain_of_thought(),  # "Think step by step..."
    generate(),          # Model call
    self_critique()      # "Critique your answer..."
)
```

### Dataset Structure for Thesis Eval

```python
from inspect_ai.dataset import Sample, MemoryDataset

samples = [
    Sample(
        input="Company: Acme Wellness\nDescription: B2C fitness app...",
        target="QUALIFIED",
        metadata={"notion_id": "abc123", "actual_outcome": "Funded"}
    ),
    Sample(
        input="Company: DevTools Inc\nDescription: API platform for developers...",
        target="REJECTED",
        metadata={"notion_id": "def456", "actual_outcome": "Passed"}
    )
]
dataset = MemoryDataset(samples)
```

### Scoring Classification Results

```python
from inspect_ai.scorer import match, accuracy, stderr

@task
def thesis_classification_eval():
    return Task(
        dataset=thesis_dataset,
        solver=chain(chain_of_thought(), generate()),
        scorer=match(),  # Compare output to target
        metrics=[accuracy(), stderr()]
    )
```

### Running Evaluations

```bash
# Run against different models
inspect eval thesis_eval.py --model openai/gpt-4o
inspect eval thesis_eval.py --model google/gemini-1.5-flash

# View results
inspect view
```

### Implementation Plan for Discovery Engine

**Phase 1: Create Labeled Dataset**
1. Export Notion companies with status "Funded" → label as QUALIFIED ground truth
2. Export Notion companies with status "Passed" → label as REJECTED ground truth
3. Export current "Tracking" → use for HELD validation
4. Format as JSON Lines with company description + thesis classification target

**Phase 2: Build Evaluation Task**
```python
# tests/evaluation/thesis_eval.py
from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.solver import chain, chain_of_thought, generate
from inspect_ai.scorer import match, accuracy

@task
def thesis_classification():
    return Task(
        dataset=json_dataset("datasets/thesis_ground_truth.jsonl"),
        solver=chain(
            chain_of_thought(),
            generate()
        ),
        scorer=match(),
        metrics=[accuracy(), stderr()]
    )
```

**Phase 3: Parameter Sweep**
- Compare models: Gemini Flash vs Pro vs GPT-4o
- Compare thresholds: 0.25, 0.30, 0.35 for HELD/QUALIFIED boundary
- Compare prompts: current vs chain-of-thought vs self-critique

**Phase 4: Integrate Best Config**
- Update `llm_classifier.py` with winning configuration
- Add chain-of-thought to prompt template
- Store reasoning traces in `thesis_classifications` table

---

---

## Evaluation Stack Summary

| Tool | Purpose | Install | Status |
|------|---------|---------|--------|
| **Inspect AI** | Core evaluation framework | `pip install inspect-ai` | Ready |
| **inspect_flow** | Parameter sweeping at scale | `pip install inspect-flow` | Ready |
| **inspect_scout** | Transcript analysis | `pip install git+...inspect_scout` | Optional |

### Quick Start

```bash
# Install dependencies
pip install inspect-ai inspect-flow

# Run thesis evaluation
inspect eval tests/evaluation/thesis_eval.py --model google/gemini-1.5-flash

# View results
inspect view

# Run parameter sweep
flow run tests/evaluation/flow_config.py
```

### Implementation Checklist

- [x] Document inspect_flow and inspect_scout in findings.md
- [x] Create scripts/export_notion_ground_truth.py (export script ready)
- [x] Create tests/evaluation/thesis_eval.py
- [x] Create tests/evaluation/solvers.py (chain-of-thought, self-critique)
- [x] Create tests/evaluation/scorers.py (QUALIFIED/HELD/REJECTED)
- [x] Create tests/evaluation/flow_config.py (parameter sweep)
- [x] Update llm_classifier.py with chain-of-thought (ENABLE_COT_REASONING env)
- [x] Add reasoning_trace column to thesis_classifications (migration 17)
- [x] Create .github/workflows/thesis_eval.yml (weekly runs)

---

*Last updated: 2026-01-29*
