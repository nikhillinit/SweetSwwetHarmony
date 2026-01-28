# Network Scout v2.0 (Lean MVP) — Design Document

**Component:** Relationship Intelligence & Warm Intro
**Author:** Brainstorming session (2026-01-28)
**Status:** Approved for implementation
**Privacy Model:** Local-First / Zero-Trust (raw data never leaves `private_graph.db`)

---

## 1. Product Objective

Combine "who we know" (Notion LP database + status) with "who we talk to" (Gmail history) to surface **actionable warm intros** with clear attribution and lightweight UX signals.

### MVP Constraints (Non-Negotiable)

- **Local-first** execution (no network dependencies required for correctness)
- **Explainable** scoring (simple parameters, easy to reason about)
- **User-trust safeguards** (manual override + declined suppression before any writeback)

---

## 2. Data Flow Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     RELATIONSHIP DATA FLOW                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Gmail Takeout (.mbox)          Notion LP Database              │
│          │                              │                        │
│          ▼                              ▼                        │
│   ┌──────────────┐              ┌──────────────────┐            │
│   │ LocalEmail   │              │ NotionLPSync     │            │
│   │ Scanner      │              │ (new connector)  │            │
│   └──────┬───────┘              └────────┬─────────┘            │
│          │                               │                       │
│          │  intro_count, reply_rate      │  LP status, website   │
│          │  last_contact, first_contact  │  domain extraction    │
│          │                               │                       │
│          └───────────┬───────────────────┘                       │
│                      ▼                                           │
│            ┌─────────────────────┐                              │
│            │ RelationshipStore   │  ← private_graph.db          │
│            │ (already built)     │    NEVER signals.db          │
│            └─────────┬───────────┘                              │
│                      │                                           │
│                      ▼                                           │
│            ┌─────────────────────┐                              │
│            │ WarmIntroBoost      │  ← lookup by investor domain │
│            └─────────┬───────────┘                              │
│                      │                                           │
│                      ▼                                           │
│            ┌─────────────────────┐                              │
│            │ InvestorMatcher     │  ← adds boost + explanation  │
│            └─────────────────────┘                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Scoring System

### 3.1 Source Hierarchy

Relationship signals come from two sources with a deterministic merge rule:

| Source | Score Range | Confidence |
|--------|-------------|------------|
| Gmail (calculated) | 0.0–1.0 | High |
| Notion: Docs Signed | **0.95** (fixed) | Very High |
| Notion: Verbal Confirm | **0.70** (fixed) | Medium |
| Notion: Engagement Sent | **0.40** (fixed) | Low |
| Notion: In DB only | **0.25** (fixed) | Low |

**Merge Rule:**
```python
final_relationship_score = max(notion_score, gmail_score)
```

**Exceptions:**
- Declined → suppression rule (Section 3.3)
- Manual Override → do not overwrite (Section 5.1)

### 3.2 Gmail Scoring Formula

Already implemented in `RelationshipStore._calculate_strength_score()`:

```python
strength = clamp01(
    0.50 * sigmoid(intro_count) +
    0.35 * reply_rate +
    0.15 * recency_score
)

# Where:
# - intro_count: Number of intro/introduction emails
# - reply_rate: replies / total_messages
# - recency_score: 1.0 - min(days_since_last, 365) / 365
```

### 3.3 Declined Suppression

**Constants:**
```python
DECLINE_SUPPRESS_WINDOW_DAYS = 548  # ≈ 18 months
DECLINE_POST_WINDOW_SCORE_CAP = 0.30
DECLINE_BADGE = "⚠️ Previously declined"
```

**Rule:**
- If `status == declined` AND `days_since_decline < 548`:
  - **SUPPRESS** (do not return as warm-intro candidate)
- If `status == declined` AND `days_since_decline >= 548`:
  - **ALLOW WITH PENALTY**:
    - Include candidate
    - Set badge = `⚠️ Previously declined`
    - Cap final score: `final_score = min(final_score, 0.30)`

**Ordering constraint:** Apply after Notion/Gmail merge and after warmth boost.

**Manual override interaction:** If `Manual Override = true`, do not suppress or cap.

### 3.4 Warmth Boost in Investor Matching

**Gate:** If `thesis_fit < 0.4`, apply no warmth boost.

**Boost formula:**
```python
def apply_warm_intro_boost(thesis_fit: float, warmth: float) -> float:
    if thesis_fit < 0.4:
        return thesis_fit  # Don't boost bad fits

    max_boost = 0.05  # Configurable cap
    return min(1.0, thesis_fit + (warmth * max_boost))
```

**Properties:**
- Non-destructive: compute separate "relationship-adjusted" score
- Diminishing returns: warmth cannot over-boost already-perfect fits
- Breaks ties: elevates "good-enough fit with strong relationship"

---

## 4. Domain Resolution

### 4.1 Canonical Key Generation

Use existing `utils/canonical_keys.py`:
```python
from utils.canonical_keys import generate_canonical_key

# "https://www.sequoia.com/team" → "domain:sequoia.com"
canonical = generate_canonical_key(lp_website)
```

### 4.2 Domain Extraction Fallback

When Notion `Website` field is missing but `Email` exists:
```python
# "partner@sequoia.com" → "domain:sequoia.com"
domain = email.split("@")[1].lower()
if domain not in PROVIDER_BLOCKLIST:
    return f"domain:{domain}"
```

### 4.3 Provider Blocklist

**Hardcoded baseline:**
```python
PROVIDER_BLOCKLIST = {
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.co.uk",
    "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "protonmail.com", "proton.me",
}
```

**Configurable extension:** Allow `extra_blocked_domains` in config for edge cases.

### 4.4 Multi-LP Merge Rule

When multiple Notion LP records normalize to the same firm domain:

**Firm-level aggregation:**
1. **Status tier** = highest tier among mapped LPs
   `Docs Signed > Verbal > Engaged > In Database`
2. **Attribution** = concatenate unique names
   `"via Willie Litvack, Sean Tolkin"`
3. **Recency** = `max(last_updated)` across LP records
4. **Traceability** = preserve `notion_lp_ids = [...]`
5. **Logging** = emit warning once per run per domain:
   `"Multiple LP records map to {domain}; merged using highest-tier rule"`

**Candidate-level behavior:** Return individual LP candidates so a "declined" person doesn't erase other partners at the same firm.

---

## 5. User Trust Safeguards

### 5.1 Manual Override Protection

Before any Notion writeback:
- If `Manual Override = true` → **never overwrite** warmth score
- May still compute and display "suggested score" for reference

### 5.2 Timezone Normalization

All dates normalized to UTC before computing recency:
```python
if parsed_timestamp.tzinfo is None:
    parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)

days_since = (datetime.now(timezone.utc) - last_contact_utc).days
```

### 5.3 Epsilon Check for Notion Writes

**Constant:**
```python
EPSILON = 0.02  # Only update if score changes by >2%
```

**Logic:**
```python
def should_push_update(page_id: str, new_score: float, current_props: dict) -> bool:
    if current_props.get('Manual Override', {}).get('checkbox'):
        logger.info(f"Skipping {page_id}: Manual Override active")
        return False

    current_val = current_props.get('Warmth Score', {}).get('number', 0.0) or 0.0
    diff = abs(new_score - current_val)

    if diff < EPSILON:
        logger.debug(f"Skipped {page_id}: score delta {diff:.3f} < epsilon")
        return False

    return True
```

---

## 6. MBOX Ingestion

### 6.1 Streaming Parser

**File:** `connectors/local_email_scanner.py`

```python
class MboxStreamer:
    def stream_headers(self, mbox_path: str) -> Generator[Dict[str, Any], None, None]:
        """
        Iterative parser. Yields metadata only.
        Never loads full file into RAM.
        """
        box = mailbox.mbox(mbox_path)
        try:
            for message in box:
                try:
                    yield {
                        "date": self._parse_date(message['date']),
                        "from": message['from'],
                        "to": message['to'],
                        "cc": message['cc'],
                        "subject": message['subject'],
                        "message_id": message['message-id'],
                        "in_reply_to": message['in-reply-to'],
                        "references": message['references'],
                    }
                except Exception as e:
                    logger.warning(f"Skipped malformed msg: {e}")
        finally:
            box.close()
```

### 6.2 Thread Detection

Prefer `in_reply_to` / `references` headers over subject heuristics for thread grouping.

### 6.3 Contact Tracking

During ingestion, maintain:
- `first_contact_at = min(existing_first, email_timestamp)`
- `last_contact_at = max(existing_last, email_timestamp)`

---

## 7. Output Schema

Each warm intro candidate includes:

| Field | Type | Description |
|-------|------|-------------|
| `investor_domain` | string | Canonical domain (e.g., "sequoia.com") |
| `score` | float | Final relationship score (0.0–1.0) |
| `source` | string | "gmail" / "notion_lp" |
| `badge` | string | "📧 Active" / "📝 LP - Docs Signed" / "⚠️ Previously declined" |
| `attribution` | string | "via Willie Litvack" |
| `notion_lp_ids` | list | Traceability to Notion records |
| `confidence` | string | "high" / "medium" / "low" |

### 7.1 Badge Mapping

| Condition | Badge |
|-----------|-------|
| Gmail source, strength ≥ 0.6 | `📧 Active Conversation` |
| Notion: Docs Signed | `📝 LP - Docs Signed` |
| Notion: Verbal Confirm | `📝 LP - Verbal` |
| Notion: Engagement Sent | `📋 LP - Contacted` |
| Declined (post-window) | `⚠️ Previously declined` |

---

## 8. CLI Interface

Following existing `run_pipeline.py` patterns:

```bash
# Gmail import
python run_pipeline.py import-emails --mbox ~/Downloads/takeout.mbox

# Notion LP sync
python run_pipeline.py sync-lps [--dry-run]

# View relationship health
python run_pipeline.py relationship-health

# Debug: show warm intros for a domain
python run_pipeline.py warm-intros --investor-domain sequoia.com
```

---

## 9. Implementation Priority

### Week 1 — Core Value + Safety

1. MBOX streamer with threading fields (`message_id`, `in_reply_to`, `references`)
2. Timezone normalization for all timestamps
3. Notion LP ingest + domain extraction fallback + provider blocklist
4. Manual override handling (block overwrites)
5. Scoring tiers + merge rule (`max()` with declined suppression)
6. Matching integration (warmth as separate feature) + badge + attribution outputs

### Week 2 — Polish + Operational Hygiene

7. Epsilon check for Notion writes + skip logging
8. Staleness monitoring (alerts if scans/syncs are old)
9. Optional UX refinements in dashboard once data payload is stable

### Later — Scale Only If Needed

10. Alternative decay model experiments (radioactive/exponential)
11. Circuit breaker extensions inside existing retry utility
12. Structured audit logging / compliance upgrades

---

## 10. Acceptance Criteria

### Ingestion
- [ ] Can parse very large MBOX without memory blowups
- [ ] Thread grouping works for replies (not just subjects)

### Normalization
- [ ] Domains normalize consistently; provider domains are blocked
- [ ] Email-only LP entries still get a usable domain via fallback

### Scoring
- [ ] Tiers match configured values exactly (0.95/0.70/0.40/0.25)
- [ ] Declined suppresses intros reliably within 18-month window
- [ ] Post-window declined capped at 0.30
- [ ] Scores are stable across timezone boundaries

### Trust
- [ ] Manual override is never overwritten
- [ ] Notion writeback does not spam (epsilon) and is observable (logs)

### Output
- [ ] Each candidate includes: `score`, `source`, `badge`, `attribution`
- [ ] "Why" reasons integrated into InvestorMatcher explanations

---

## 11. Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `connectors/local_email_scanner.py` | Streaming MBOX parser |
| `connectors/notion_lp_sync.py` | LP database sync |
| `utils/warm_intro_boost.py` | Boost calculation + badge generation |
| `utils/relationship_health.py` | Staleness monitoring |
| `tests/connectors/test_local_email_scanner.py` | Scanner tests |
| `tests/connectors/test_notion_lp_sync.py` | LP sync tests |
| `tests/utils/test_warm_intro_boost.py` | Boost logic tests |

### Modified Files
| File | Change |
|------|--------|
| `storage/relationship_store.py` | Add `source`, `lp_status`, `lp_name` fields |
| `utils/investor_matching.py` | Integrate WarmIntroBoost |
| `run_pipeline.py` | Add CLI commands |

---

## 12. Dependencies

**No new dependencies required.** Uses:
- `mailbox` (stdlib)
- `email.utils` (stdlib)
- Existing `notion_connector_v2.py`
- Existing `retry_strategy.py`

---

## Appendix: Configuration Constants

```python
# Scoring tiers
NOTION_SCORE_DOCS_SIGNED = 0.95
NOTION_SCORE_VERBAL = 0.70
NOTION_SCORE_ENGAGED = 0.40
NOTION_SCORE_IN_DB = 0.25

# Declined handling
DECLINE_SUPPRESS_WINDOW_DAYS = 548
DECLINE_POST_WINDOW_SCORE_CAP = 0.30

# Warmth boost
WARMTH_BOOST_GATE_THRESHOLD = 0.40
WARMTH_BOOST_MAX = 0.05

# Notion writeback
EPSILON = 0.02

# Gmail scoring weights
INTRO_WEIGHT = 0.50
REPLY_WEIGHT = 0.35
RECENCY_WEIGHT = 0.15
```

---

*Design document finalized: 2026-01-28*
