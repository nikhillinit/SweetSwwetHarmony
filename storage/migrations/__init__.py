"""storage.migrations — migration DDL modules and CLI utilities.

Public API (lazy-loaded to avoid circular import with signal_store):
    list_migrations, export_data, import_data, validate_schema, get_info
"""

__all__ = [
    "list_migrations",
    "export_data",
    "import_data",
    "validate_schema",
    "get_info",
]

_CLI_ATTRS = frozenset(__all__)


def __getattr__(name: str):
    if name in _CLI_ATTRS:
        from storage.migrations.cli import (  # noqa: F811
            export_data,
            get_info,
            import_data,
            list_migrations,
            validate_schema,
        )
        # Cache on the module so __getattr__ is not called again
        import storage.migrations as _self
        for attr in _CLI_ATTRS:
            setattr(_self, attr, locals()[attr])
        return locals()[name]
    raise AttributeError(f"module 'storage.migrations' has no attribute {name!r}")
