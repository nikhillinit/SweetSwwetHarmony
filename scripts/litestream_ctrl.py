"""QUARANTINED (Litestream Mode B — orchestration out of scope).

Litestream lifecycle orchestration is intentionally NOT wired into the restore
path. The real restore path (``scripts.restore_db.restore_backup`` /
``restore_backup_with_lock_and_ledger``) performs artifact / local-file restore
only and records ``litestream_mode="off"`` in every ledger row. S3/R2 cloud
restore durability is proven independently by
``.github/workflows/litestream-restore-verify-nightly.yml``.

Why this module is quarantined rather than used: Litestream is pinned to 0.5.2
in this deployment, and the controller that previously lived here issued
commands that are unsafe or incorrect on 0.5.2 —

  * ``litestream stop`` / ``litestream replicate -config`` under a 10s subprocess
    timeout cannot hold a long-lived daemon;
  * ``litestream generations`` is a *listing* command, not a generation reset;
  * there is no ``status`` command, and ``litestream reset`` is a 0.5.7-only
    command absent from 0.5.2.

Leaving those callable would let a non-interactive run believe a 0.5.2 lifecycle
exists when it does not. So the orchestration surface is removed: constructing
``LitestreamCtrl`` raises ``LitestreamUnsupportedError``. If a Litestream-managed
restore (Mode A) is ever required, re-introduce a controller built ONLY on
commands proven by a recorded ``litestream <cmd> -h`` capability smoke test on
the pinned version.
"""
from __future__ import annotations

# Mode B: Litestream orchestration is off. Mirrors scripts.restore_db.LITESTREAM_MODE.
LITESTREAM_MODE = "off"


class LitestreamError(RuntimeError):
    """Base error for the (quarantined) Litestream controller surface."""


class LitestreamUnsupportedError(LitestreamError):
    """Raised when code attempts to drive Litestream, which is out of scope (Mode B)."""


class LitestreamCtrl:
    """Quarantined stub — see module docstring.

    Constructing this raises ``LitestreamUnsupportedError`` so the unsafe 0.5.2
    stop/replicate/generations commands cannot be invoked as if supported.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise LitestreamUnsupportedError(
            "Litestream lifecycle orchestration is out of scope (Mode B). "
            "restore_db.py performs artifact/local-file restore only and records "
            'litestream_mode="off"; S3/R2 restore is proven by '
            "litestream-restore-verify-nightly.yml. Do not drive Litestream from "
            "the restore path on the pinned 0.5.2 — its stop/generations commands "
            "cannot safely run the lifecycle."
        )
