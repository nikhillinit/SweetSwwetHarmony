# Next Steps - Findings

**Last updated:** 2026-03-25

## What the sandbox validation confirmed

- The pending queue is still `129` total: `arxiv:56`, `rss_feeds:35`, `hacker_news:28`, `news_api:10`.
- The live blocker is gate freshness. Step 3 and Step 4 are blocked until the canary is refreshed.
- Overdue regret checks are `0`, so the current blocker is not the 2026-03-30 regret date.

## Plan corrections

1. `THESIS_SKIP_LLM_BELOW` does not default to `0.45` in current code. The effective default is `0.2`.
2. `python run_pipeline.py process --source-api hacker_news --dry-run` is not a safe live-db proof step. It still writes thesis classifications and confidence ledger rows, and active mode can still change processing state before the Notion boundary.
3. The backlog is source-specific:
   - `hacker_news` currently looks like a keyword-only pending backlog.
   - `arxiv` has pending rows with no thesis row at all.
   - `rss_feeds` and `news_api` already contain some latest LLM-backed thesis rows.
4. `thesis-classify-batch` only backfills signals missing any thesis row. It will not revisit existing keyword-only HN thesis rows.

## New sandbox command

Use this script to validate the rollout safely on a scratch copy of `signals.db`:

```bash
python scripts/thesis_activation_sandbox.py --db-path signals.db --source-api hacker_news --json
python scripts/thesis_activation_sandbox.py --db-path signals.db --source-api hacker_news --batch-size 28 --llm-mode shadow --skip-llm-below 0.0 --execute-process --json
```

What it reports:
- Current Step 3 / Step 4 activation status
- Overdue regret checks
- Pending thesis state by source
- Scratch-only proof that fresh LLM thesis rows were created

## Recommended live sequence

1. Refresh the canary and rerun activation checks.
2. Run the scratch validator with `--execute-process`.
3. After the gate is green, flip `.env` to `LLM_THESIS_MODE=active` and `THESIS_SKIP_LLM_BELOW=0.0`.
4. Process `hacker_news` first, verify fresh `model != null` thesis rows, then handle the other sources in follow-on batches.
