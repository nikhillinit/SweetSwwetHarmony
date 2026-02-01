# Pipeline Architecture Reference

Technical details of the Discovery Engine pipeline orchestration.

## Pipeline Flow

```
┌──────────────┐
│ COLLECTORS   │ Run in parallel, write to SignalStore
└──────┬───────┘
       ▼
┌──────────────┐
│ STORAGE      │ SQLite persistence with canonical key dedup
└──────┬───────┘
       ▼
┌──────────────┐
│ CONSOLIDATION│ (Optional) Merge multi-source signals
└──────┬───────┘
       ▼
┌──────────────┐
│ ENRICHMENT   │ (Optional) Boost confidence from metadata
└──────┬───────┘
       ▼
┌──────────────┐
│ THESIS FILTER│ Consumer focus (CPG, Health, Travel, Marketplace)
└──────┬───────┘
       │
       ├─→ QUALIFIED (fit >= 0.3) ─→ Verification Gate
       ├─→ HELD (fit < 0.3) ─────→ Manual review queue
       └─→ REJECTED (excluded) ──→ Filtered out

       ▼
┌──────────────┐
│ VERIFICATION │ Confidence-based routing
└──────┬───────┘
       │
       ├─→ AUTO_PUSH (conf >= 0.7, multi-source) ─→ Notion "Source"
       ├─→ NEEDS_REVIEW (conf 0.4-0.7) ───────────→ Notion "Tracking"
       └─→ HELD (conf < 0.4) ─────────────────────→ Hold for batch

       ▼
┌──────────────┐
│ EXIT PREDICT │ (Optional) Heuristic scoring
└──────┬───────┘
       ▼
┌──────────────┐
│ NOTION PUSH  │ CRM sync with suppression cache
└──────────────┘
```

## DiscoveryPipeline Class

**Location:** `workflows/pipeline.py`

**Key Methods:**
- `run_full_pipeline()` - Orchestrates all stages
- `run_collectors()` - Execute collector subset
- `process_pending()` - Verify + route signals
- `sync_suppression()` - Refresh Notion cache
- `get_stats()` - Metrics snapshot

**Configuration:**
```python
@dataclass
class PipelineConfig:
    db_path: str = "signals.db"
    use_gating: bool = True              # Verification gate
    use_entities: bool = False           # Entity resolution
    use_asset_store: bool = False        # Change detection
    use_consolidation: bool = True       # Multi-source merge
    use_enrichment_boost: bool = True    # Metadata boost
    use_thesis_filter: bool = True       # Consumer focus
    use_competitor_detection: bool = True
    use_exit_predictor: bool = False     # Heuristic scoring
    use_investor_matching: bool = False
    use_phase_g_identity_resolution: bool = False
    use_claim_facts: bool = False
```

## PipelineStats Dataclass

**Metrics Captured:**
```python
@dataclass
class PipelineStats:
    # Collectors
    collectors_run: int = 0
    collectors_succeeded: int = 0
    collectors_failed: int = 0
    collectors_skipped: int = 0
    signals_collected: int = 0

    # Storage
    signals_stored: int = 0
    signals_deduplicated: int = 0

    # Consolidation
    signals_consolidated: int = 0
    conflicts_detected: int = 0

    # Enrichment
    enrichment_boosts_applied: int = 0
    avg_enrichment_boost: float = 0.0

    # Thesis Filter
    thesis_passed: int = 0
    thesis_held: int = 0
    thesis_rejected: int = 0

    # Verification
    signals_processed: int = 0
    signals_auto_push: int = 0
    signals_needs_review: int = 0
    signals_held: int = 0
    signals_rejected: int = 0

    # Notion
    prospects_created: int = 0
    prospects_updated: int = 0
    prospects_skipped: int = 0

    # Timing
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0
```

## Feature Flags

Enable/disable pipeline components via environment variables:

```bash
# Enable exit prediction scoring
ENABLE_EXIT_PREDICTOR=true

# Enable entity resolution
ENABLE_ENTITY_RESOLVER=true

# Enable source asset store (change detection)
ENABLE_ASSET_STORE=true
```

## Integration with CLI

The skill uses these CLI commands:

| Command | Maps To | Stage |
|---------|---------|-------|
| `collect --collectors X` | `run_collectors()` | Collection |
| `process` | `process_pending()` | Verification |
| `pipeline status` | Query signal counts | Status check |
| `pipeline qualified` | List ready signals | Review |
| `pipeline push --confirm` | Push to Notion | Sync |
| `sync` | `sync_suppression()` | Pre-flight |
| `health` | Component health | Post-flight |

## Error Codes

| Code | Meaning | Recovery |
|------|---------|----------|
| `COLLECTOR_ERROR` | API/network failure | Retry or skip collector |
| `DATABASE_LOCKED` | Concurrent access | Wait or stop other process |
| `NOTION_SCHEMA_DRIFT` | Field mismatch | Run `sync` command |
| `RATE_LIMIT_EXCEEDED` | API quota hit | Wait or use other collectors |

## Performance

**Typical Runtimes (Fast Preset):**
- Collection: 1-3 minutes (3 collectors)
- Processing: 10-30 seconds
- Notion push: 5-15 seconds

**Total:** 2-4 minutes end-to-end

**Bottlenecks:**
- SEC EDGAR (0.15s delay per request)
- Notion API (rate limit: 3 req/sec)
