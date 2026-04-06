"""Shadow collectors — first-wave non-production collectors.

These collectors run inside the ShadowSidecar safety contract:
  - They write ONLY to data/shadow/discovery.db
  - They never touch signals.db, governance state, or Notion
  - They are intended for the Step 4B regret window (Phase 0)
  - Promotion to production is gated by Phase 1+ replay-harness evidence

Each collector exposes a `collect()` callable that takes a ShadowSidecar
and returns the number of items written.

See: artifacts/red-team-execution/phase0/evidence-ontology.md
     analytics/shadow_sidecar.py
"""
