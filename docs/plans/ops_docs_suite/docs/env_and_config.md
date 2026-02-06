# Configuration via `.env` (Windows-friendly)

Windows makes environment variables easy to *set incorrectly* and hard to *share consistently* across teammates. A `.env` file helps: it’s local, explicit, and works with venv workflows.

## Recommendation

- Keep **secrets** in `.env` (not committed)
- Keep **defaults** in code
- Keep **templates** in `.env.example` (committed)

## Loading `.env`

You have two practical options:

### Option A — use `python-dotenv` (recommended)
Install:

```powershell
pip install python-dotenv
```

In each entrypoint (`ops/cli.py`, `ops/memory/extractor.py`, etc.), call:

```python
from dotenv import load_dotenv
load_dotenv()
```

### Option B — minimal built-in loader
If you prefer no extra dependency, implement a tiny `.env` parser (key/value, `#` comments) and call it early. This doc set includes an example inside `ops/bootstrap.py` you can copy.

## Variables used by the current ops layer

These names come from the codebase (adjust if you rename them):

### API keys / LLM configuration
- `GOOGLE_API_KEY` — used by the GenAI SDK
- `GEMINI_API_KEY` — alternate name supported by the extractor
- `GEMINI_MODEL` — model id/name (defaults exist in code)
- `MAX_OUTPUT_TOKENS` — response size cap

### Budgeting / throttling
- `DAILY_LLM_BUDGET` — soft budget guardrail
- `MAX_EXTRACTION_ATTEMPTS` — action retry cap
- `EXTRACTION_SLEEP_SECONDS` — pause between work items

### Briefing output tuning
- `BRIEFING_SIMILARITY_THRESHOLD`
- `BRIEFING_JACCARD_PREFILTER`
- `BRIEFING_TOKEN_BUDGET`

## Suggested precedence rules

1. OS environment variables (CI, production, one-off overrides)
2. `.env` (local dev defaults)
3. Code defaults (safe fallbacks)

This lets you override temporarily without editing `.env`.
