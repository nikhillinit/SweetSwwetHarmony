"""
DDL (SQLite) for Quality Ops tables.

These tables live in the same SQLite DB as the signal store (signals.db by default).

Why tables (not just logs)?
- Enables repeatable measurement (FP rate over time)
- Enables automation: pattern mining and tuning proposals
- Enables audit trail (manual labels vs inferred outcomes)

NOTE: The authoritative DDL lives in storage/migrations/quality_tables.py.
This module re-exports it so that ops/quality/db.py (and others) can import
from the expected location without modification.
"""
from __future__ import annotations

from storage.migrations.quality_tables import QUALITY_TABLES_DDL  # noqa: F401
