# Retries and resilience (Windows-first)

Network calls fail: DNS hiccups, transient 429/503, short-lived outages. The ops layer should recover from these without turning every run into manual babysitting.

## A simple policy that works

- Retry *only* idempotent operations
- Keep retries localized around true network edges (LLM calls, HTTP fetches)
- Cap total time spent retrying (avoid infinite loops)
- Log retries with enough context to debug

## Localized retry loops (lowest dependency)

For small internal tools, a short loop with exponential backoff is often sufficient:

- 2–3 retries
- backoff like 1s, 2s, 4s
- stop on non-transient errors

## Optional: Tenacity (cleaner ergonomics)

If you prefer decorators + consistent policies across call sites, Tenacity is a good fit.

Install:

```powershell
pip install tenacity
```

Example pattern:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests

@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
)
def fetch_url(url: str) -> str:
    return requests.get(url, timeout=10).text
```

## Where Tenacity is worth it

- multiple call sites share the same retry policy
- you want uniform logging + instrumentation
- you want to separate retry logic from business logic

## Where it’s overkill

- only 1–2 places need retries
- you already have a “claim + cooldown + attempts” mechanism at the DB level
- you’re optimizing for minimal dependencies

A hybrid approach is common: keep DB-level attempt tracking, and add small per-call retries for truly transient failures.
