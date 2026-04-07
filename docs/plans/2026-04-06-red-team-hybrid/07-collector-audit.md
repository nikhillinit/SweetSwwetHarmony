# Systematic Collector Correctness Audit — Read-Only Catalog

**Date:** 2026-04-06
**Status:** Catalog only — fixes ship in Move 2 (NOT Move 0)
**Resolves:** Risk R8 (over-rotating on a single anecdote — `_processed_identities`)

---

## 1. Why this is a catalog, not a fix

Per red-team §3.2:
> The user elevates the collector instance-reuse bug as a real recall risk,
> which it is. But fixing it is a 1-day task. There are likely a half-dozen
> similar bugs across 16 collectors. The right move is `superpowers:systematic-debugging`
> on the collector layer, producing a small batch of correctness fixes — not
> gating the entire merge process behind a golden-set canary on the strength of
> one anecdote. Over-rotating on a single example is a classic strategic error:
> the strategic move is the *systematic audit*, not the *anecdote response*.

Move 0 ships the catalog. Move 2 ships the top 3-5 fixes from the catalog.
The Move 0 catalog is **read-only** — no collector code is modified during
the regret window.

This document is structured by *risk category* with specific findings under
each. Each finding is ranked by:
- **Severity**: how bad if it bites (silent recall loss > visible crash)
- **Likelihood**: how often it bites today
- **Effort**: rough fix size
- **Move**: which Move it should land in

---

## 2. Verification done

The catalog below is based on:
- `Grep` searches across `collectors/` for instance state, async lifecycle, rate
  limit calls, dedup patterns, sleep calls
- Reading `collectors/base.py` for the shared base class behavior
- Cross-referencing with project memory (DB hardening incident, HN FP investigation)
- The known `_processed_identities` instance-reuse anecdote

The catalog is **NOT** based on running the collectors — that would risk live
API calls and is out of scope for Move 0.

---

## 3. Risk categories

### 3.1 Instance-reuse bugs (the `_processed_identities` family)

**Pattern:** mutable state on `self.` set in `__init__`, mutated during
`collect()`, never reset between runs. If the orchestrator constructs the
collector once and calls `collect()` multiple times, state from run N leaks
into run N+1, causing silent suppression of legitimate signals.

#### Findings

**INSTANCE-1: `BaseCollector._processed_identities` survives across `collect()` calls**

- **Location:** `collectors/base.py:118` (initialized), used at `collectors/base.py:337,352,365,386,431,454`
- **Mechanism:** `self._processed_identities: set[tuple[str, str, str]] = set()`
  is created in `__init__`. Every call to `collect()` reads and adds to it.
  Nothing resets it between runs.
- **Impact:** If a single `BaseCollector` subclass instance is reused, signals
  whose identities were seen in a prior run are dropped as duplicates in the
  current run. The drop is silent (`_signals_suppressed += 1`, no log line at
  WARN level).
- **Severity:** HIGH (silent recall loss)
- **Likelihood:** HIGH today if any orchestrator code reuses collector
  instances across calls. **Verification needed:** does `workflows/pipeline.py`
  cache collector instances or instantiate fresh each call? Move 2 day 1
  investigation.
- **Effort:** S (1 day): add `reset_run_state()` method to `BaseCollector`
  called at the top of `collect()`, OR move `_processed_identities` to a
  `collect()` local
- **Move:** 2

**INSTANCE-2: `self._seen` and `self._cache` patterns in 7 collectors**

- **Locations:** `sec_edgar.py`, `rss_feeds.py`, `news_api.py`, `domain_whois.py`,
  `companies_house.py`, `changedetection.py` (abandoned), `base.py`
- **Mechanism:** Same shape as INSTANCE-1 — mutable instance attributes for dedup.
- **Impact:** Same shape as INSTANCE-1. Each collector needs to be checked
  individually for whether the state is intended to persist across runs (e.g.,
  domain_whois caching previous lookups is *correct*) or accidentally persists
  (e.g., dedup that should reset).
- **Severity:** MEDIUM (some are intentional, some are bugs)
- **Likelihood:** MEDIUM
- **Effort:** M (3 days): per-file audit + decision per cache
- **Move:** 2

---

### 3.2 Async lifecycle bugs (close/cleanup not called)

**Pattern:** collectors that create `aiohttp.ClientSession` or `httpx.AsyncClient`
inside `__init__` or `fetch()` and rely on the orchestrator to call `close()`.
If `close()` is not called, sockets leak, file descriptors accumulate, and
eventually the process hits OS limits.

#### Findings

**ASYNC-1: 23 collectors use `aiohttp.ClientSession` or `httpx.AsyncClient`**

- **Locations:** Per Grep: 23 files in `collectors/` reference these clients
- **Audit needed:** which ones use a context manager (`async with`) vs. which
  ones store the client as `self._session` and rely on `close()`?
- **Audit needed:** of those that store on `self.`, which ones implement
  `__aenter__`/`__aexit__` or `close()`? Per Grep: 10 files implement async
  cleanup methods. **The 13-file gap is the audit target.**
- **Severity:** MEDIUM (resource leak, not silent recall loss)
- **Likelihood:** LOW today (collectors typically run once per pipeline cycle
  and the process exits)
- **Likelihood at scale:** HIGH (long-running orchestrator would leak)
- **Effort:** M (3 days): per-file audit + standardize on context-manager
  pattern
- **Move:** 2 (lower priority than INSTANCE-1)

**ASYNC-2: `BaseCollector.__init__` synchronous, but subclasses do async work in init?**

- **Audit needed:** any subclass that does network calls in `__init__` is a
  bug (sync constructor cannot await). Move 2 day 2 grep for `await` inside
  any `__init__`.
- **Severity:** HIGH (would crash at startup)
- **Likelihood:** LOW (would have been caught already)
- **Effort:** S (audit only, fix is per-occurrence)
- **Move:** 2

---

### 3.3 Cross-collector dedup races

**Pattern:** two collectors discover the same canonical_key in the same run.
The dedup happens at the storage layer (`signal_store.add_signal`), but if
both writes are concurrent, the second one races against the first. Whichever
wins is undefined.

#### Findings

**DEDUP-1: Concurrent collector runs may double-write same canonical_key**

- **Mechanism:** if `workflows/pipeline.py` runs multiple collectors in parallel
  via `asyncio.gather`, two collectors finding the same canonical_key both
  call `add_signal`. SQLite serializes writes (single-writer), so one wins,
  but the loser may silently fail (depending on `INSERT OR IGNORE` vs `INSERT
  OR REPLACE`).
- **Audit needed:** check `storage/signal_store.py` for the upsert semantics.
  If `INSERT OR REPLACE`, the second collector's payload overwrites the first
  — signal source attribution is wrong.
- **Severity:** MEDIUM (provenance loss, not signal loss)
- **Likelihood:** LOW (low signal volume today; race window is narrow)
- **Effort:** M (storage layer audit + possible upsert semantics fix)
- **Move:** 2

---

### 3.4 Rate-limit budget leaks

**Pattern:** collectors call external APIs without coordinating against a
shared rate-limit budget. Each collector knows its own per-call sleep, but
none knows the *aggregate* across all collectors hitting the same API
(e.g., GitHub).

#### Findings

**RATE-1: GitHub token shared between `github.py` and `github_activity.py`**

- **Mechanism:** Both collectors use `GITHUB_TOKEN` (5000 req/hr authenticated).
  Neither coordinates against the other's usage.
- **Impact:** if both run in the same pipeline cycle, the second one may hit
  429 rate limits even if its own per-call sleep is correct.
- **Audit needed:** does any rate-limit middleware track aggregate? Per Grep,
  31 files reference `RateLimit|sleep`, but none clearly coordinate across
  collectors.
- **Severity:** MEDIUM (visible failure — 429s, not silent)
- **Likelihood:** LOW today (both collectors are low-volume), HIGH if Phase 0's
  shadow GH negative-space collector lands without shared rate limiting
- **Effort:** L (need a shared rate-limit broker — possibly use existing
  `hunter_budget` infrastructure as a model)
- **Move:** 3 (deferred — coupled to Track D)

**RATE-2: News API and RSS Feeds may share an upstream**

- **Mechanism:** `news_api.py` (GNews API) and `rss_feeds.py` (RSS) sometimes
  pull from overlapping sources. The dedup happens at signal level, not at
  fetch level.
- **Severity:** LOW (efficiency, not correctness)
- **Effort:** S
- **Move:** 3

---

### 3.5 Silent failure modes

**Pattern:** errors that get caught and logged at DEBUG/INFO level, hiding
real bugs in production logs.

#### Findings

**SILENT-1: `BaseCollector.collect` swallows exceptions per signal**

- **Location:** `collectors/base.py:388` (try/except inside the per-signal loop)
- **Mechanism:** Each per-signal exception is logged but the loop continues.
  This is correct behavior — one bad signal shouldn't kill the run. BUT: if
  EVERY signal in a run hits the same exception (e.g., schema change), the
  collector reports "0 new signals, 100 errors" with no aggregate alert.
- **Audit needed:** check whether the per-collector error rate feeds into SPC
  (probably yes, via `monitoring/spc_monitor.py`). If yes, low priority. If
  no, add an aggregate error alert.
- **Severity:** MEDIUM (silent collection failure)
- **Likelihood:** MEDIUM
- **Effort:** S (alert wiring)
- **Move:** 2 (overlaps with dead-letter contract from `03-dead-letter-contract.md`)

**SILENT-2: HN collector returns 100% FP for 30 days, no alert fired**

- **Reference:** `artifacts/activation/step4a_promotion_2026-03-16T19-05-16/hn-fp-investigation-2026-03-19.md`
- **Mechanism:** HN was 41/42 FP over 30d, 0 TP. The collector itself didn't
  fail — it kept producing signals. The downstream classifier kept marking them
  rejected. No alert fired because "100% rejection" looks like "the classifier
  is doing its job," not "the collector is broken."
- **Status:** Mitigated by enabling LLM_THESIS_MODE=active (per project memory).
  Not a code bug per se, but a cultural / monitoring gap.
- **Severity:** HIGH (the entire HN signal stream was noise for 30 days)
- **Likelihood:** RECURRING — the same shape will hit other collectors
- **Effort:** M (need a "collector signal value" metric, not just "collector
  produced N signals")
- **Move:** 2 (overlaps with Tier-2 recall eval from `06-tier-2-recall-eval.md`)

---

### 3.6 Schema drift bugs

**Pattern:** the parser silently coerces malformed input into "default" values,
hiding schema drift from upstream sources.

#### Findings

**SCHEMA-1: Optional field defaulting may mask source schema changes**

- **Mechanism:** if a collector's parser uses `data.get("field", "")`, a
  source field rename (`"field"` → `"newField"`) silently produces empty values
  in every signal. The signals look fine; their content is empty.
- **Audit needed:** which collectors use defaultful gets vs. explicit key
  access?
- **Severity:** HIGH (silent content loss)
- **Likelihood:** MEDIUM (sources do rename fields)
- **Effort:** L (per-collector audit + soft-validation hooks)
- **Move:** 1 (this is the soft-schema-on-write work; the dead-letter contract
  in `03-dead-letter-contract.md` catches it)

---

## 4. Triaged backlog (top 5 for Move 2)

Ranked by severity × likelihood × leverage for the substrate hardening goal:

| Rank | Finding | Severity | Likelihood | Effort | Move |
|---|---|---|---|---|---|
| 1 | INSTANCE-1: `_processed_identities` instance reuse | HIGH | HIGH | S | 2 |
| 2 | INSTANCE-2: 7-file audit of `self._seen`/`self._cache` patterns | MEDIUM | MEDIUM | M | 2 |
| 3 | SCHEMA-1: defaultful gets in parsers (overlaps with Move 1 dead-letter contract) | HIGH | MEDIUM | L | 1+2 |
| 4 | SILENT-2: collector signal value metric (HN-shaped failures) | HIGH | RECURRING | M | 2 |
| 5 | ASYNC-1: 13-collector audit of session lifecycle | MEDIUM | LOW (today) | M | 2 |

**Deferred to Move 3 or later:**
- DEDUP-1 (low likelihood today)
- RATE-1, RATE-2 (coupled to Track D shadow collectors)
- ASYNC-2 (fast audit, fix per occurrence)

---

## 5. Findings NOT to fix

Per the principle that the strategic move is the systematic audit (not the
anecdote response), some findings should be documented and *not* fixed:

- **`changedetection.py` ASYNC patterns** — collector is abandoned per
  `CLAUDE.md`. Don't fix; delete in a separate cleanup PR after Move 3.
- **`linkedin.py`, `crunchbase.py`, `proxycurl`** — collectors are disabled
  (missing API keys). Don't fix until they're enabled.
- **Test files** (`test_*.py`) — out of scope; if a test is wrong, fix it
  alongside its production code, not as part of this audit.

---

## 6. Verification commands (for Move 2 day 1)

```bash
# Confirm INSTANCE-1: does the orchestrator reuse collector instances?
grep -n "Collector(" workflows/pipeline.py | head
grep -n "self\._collectors" workflows/pipeline.py
grep -n "= .*Collector(" workflows/pipeline.py

# Confirm INSTANCE-2: catalog the 7 caches
grep -n "self\._seen\|self\._cache\|self\._processed" collectors/*.py

# Confirm ASYNC-1: which collectors implement context manager?
grep -ln "__aenter__\|__aexit__\|async def close" collectors/*.py

# Confirm SCHEMA-1: defaultful gets
grep -n "data\.get(" collectors/*.py | wc -l

# Confirm DEDUP-1: storage layer upsert semantics
grep -n "INSERT OR" storage/signal_store.py
```

These commands are documented for Move 2; **they are NOT run during Move 0**
(the read-only audit was done via the Grep tool above; running these in Move 0
would be duplicative work).

---

## 7. Open questions for Move 2

1. **Does `workflows/pipeline.py` instantiate collectors fresh per cycle, or
   reuse them across cycles?** The answer determines whether INSTANCE-1 is
   active in production today.
2. **Is there a pattern library for "correct dedup" that the team prefers?**
   Options: per-call local set, per-instance with explicit reset, persistent
   in DB. Each has tradeoffs. Move 2 should pick one and apply it consistently.
3. **Should the soft-validation hooks (Move 1) replace defaultful parser
   patterns globally, or only in the top-3 collectors?** Recommendation: top-3
   in Move 1, expand in Move 2.

---

## 8. Known gaps in this audit

- The audit relied on `Grep` patterns; a true symbol-level audit (e.g., via
  Pyright or LSP) would catch more
- Test files were excluded from the audit
- The audit did not run any collector code; runtime bugs that depend on input
  shape are not covered
- The audit did not survey error handling paths in `collectors/retry_strategy.py`
  or `collectors/http_client.py`
- The audit did not survey the legacy `changedetection.py` path

These gaps are acknowledged. The catalog is useful at 80% (per Move 0 charter
§1) — it surfaces the top-5 fixes for Move 2 without claiming completeness.
