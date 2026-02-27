"""v44 — DNS promotion aliases table.

Tracks alias mappings from weak canonical keys (name_loc:*) to stronger
DNS-probed domain keys (domain:*). Used by PR10b DNS promotion delivery.

Distinct from migration 10's canonical_key_aliases (monitoring).
"""

V44_DNS_PROMOTION_ALIASES_DDL = """
CREATE TABLE IF NOT EXISTS dns_promotion_aliases (
    alias_key TEXT PRIMARY KEY,
    target_key TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'dns_promotion'
        CHECK(alias_type IN ('dns_promotion')),
    source TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dpa_target ON dns_promotion_aliases(target_key);
CREATE INDEX IF NOT EXISTS idx_dpa_enabled ON dns_promotion_aliases(enabled) WHERE enabled = 1;
"""
