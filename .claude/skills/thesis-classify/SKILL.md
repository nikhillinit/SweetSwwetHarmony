---
name: thesis-classify
description: Run keyword + LLM thesis classification for a single signal and store
  it in thesis_classifications.
allowed-tools:
- Bash
- Read
---

# thesis-classify

## When to use
- You want a high-signal LLM-based category/thesis fit estimate for an example signal.
- You’re debugging keyword vs LLM disagreements.

## Inputs
- signal_id
- LLM model (default gemini-2.0-flash)
- GEMINI_API_KEY env var (google-genai)

## Workflow
1. Run: `python -m ops.cli quality thesis-classify <signal_id> --model gemini-2.0-flash`.
2. Confirm a row exists in thesis_classifications for that signal (latest classification).

## Outputs
- JSON summary of classification fields (keyword_score, thesis_match, thesis_fit_score, category, etc).

## Guardrails
- LLM output should not be treated as ground truth; use it as a decision aid + for tuning.
- Handle API failures gracefully; do not spam retries.

## References
- `references/reference.md`
- `docs/QUALITY_OPS_ARCHITECTURE.md`
