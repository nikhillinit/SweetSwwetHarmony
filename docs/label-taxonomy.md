# Label Taxonomy

## Three Label Layers

### 1. Operator Labels (immediate)
Applied during triage by human operators.

| Label | Meaning | Applied by |
|-------|---------|------------|
| TP | Thesis-relevant company | operator (approve) |
| FP | Not thesis-relevant | operator (reject) |
| UNSURE | Needs more info | operator (defer) |
| DEFERRED | Intentionally held | operator |

**Lag window:** 0 days (immediate)

### 2. Outcome Labels (30-90 days)
Derived from Notion CRM status after companies move through the pipeline.

| Label | Notion Status | Meaning |
|-------|---------------|---------|
| funded | Funded | Strong TP |
| committed | Committed | Active diligence |
| passed | Passed | Explicitly passed |
| lost | Lost | Lost deal |
| tracking | Tracking | Still in pipeline |
| no_outcome | (stale) | No update after lag window |

**Lag window:** 30 days minimum, 90 days recommended.

### 3. Gold Labels (manual verification)
Hand-verified labels for canary/evaluation sets. Highest authority.

| Label | Meaning |
|-------|---------|
| TP | Confirmed thesis-relevant |
| FP | Confirmed not thesis-relevant |
| BORDERLINE | Edge case — useful for calibration |

**Lag window:** None (requires human review)

## Conflict Resolution

When labels disagree, higher layers win:

```
Gold (priority 3) > Outcome (priority 2) > Operator (priority 1)
```

Outcome-to-TP/FP mapping:
- funded, committed → TP
- passed → FP
- tracking, lost, no_outcome → no override

## Usage

- **Canary checks** use Gold labels for ground truth
- **Quality stats** use Operator labels for real-time metrics
- **ML training** uses Outcome labels (most reliable, but lagged)
- **Trend analysis** compares Operator vs Outcome to measure operator accuracy
