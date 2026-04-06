"""Analytics package — derived ontology and shadow sidecar.

This package is intentionally separate from `workflows/`, `storage/`, and
`collectors/`. Nothing in this package writes to `signals.db` or to any
production governance state.

See `artifacts/red-team-execution/phase0/evidence-ontology.md` for the design.
"""
