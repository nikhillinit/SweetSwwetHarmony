"""Schema version parity checks for migration registry and constant."""

from storage.signal_store import CURRENT_SCHEMA_VERSION, MIGRATIONS


def test_current_schema_version_matches_registry_max():
    """Schema constant must match highest registered migration key."""
    assert CURRENT_SCHEMA_VERSION == max(MIGRATIONS.keys())


def test_migration_registry_versions_are_contiguous():
    """Migration registry should not have gaps from v1 to latest."""
    versions = sorted(MIGRATIONS.keys())
    assert versions == list(range(1, max(versions) + 1))
