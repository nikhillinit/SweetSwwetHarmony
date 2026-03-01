## Feature Promotion Request

**Feature:** `{name}`
**Current state:** `SHADOW -> ACTIVE` (or `ACTIVE -> SHADOW`, or `SHADOW -> OFF`)
**Shadow period:** `{start_date}` to `{end_date}` (`{N}` days)

### Evaluation Results
- [ ] Predictive lift: `{value}` (threshold: `>0%`)
- [ ] Stability CV: `{value}` (threshold: `<0.5`)
- [ ] Robustness: `{pass/fail}` (edge-case audit complete)
- [ ] Cost/latency: `{p95_ms}`ms, `{api_calls}`/run (within SLO)
- [ ] Leakage audit: `{pass/fail}`

### Context
- Active features at eval time: `{list}`
- Interaction testing performed: `{yes/no}` (required for Wave C+)
- Known correlations: `{list}` or `none`

### Decision
- [ ] `PROMOTE` to `ACTIVE`
- [ ] `EXTEND` shadow (`{reason}`, new window: `{date}`)
- [ ] `DEMOTE` to `SHADOW` (`{reason}`)
- [ ] `KILL` (`{reason}`)

### Rollback Plan
- Toggle flag/env back to previous state
- Record rationale in `audit_events`
- Schedule regret check date: `{date}`

