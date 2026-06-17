from __future__ import annotations

import subprocess
import time
from pathlib import Path


class LitestreamError(RuntimeError):
    pass


class LitestreamCtrl:
    """Stop/start/generation-reset Litestream safely around a DB restore.

    Required restore sequence (must follow this exact order):
      1. ctrl.stop()                    -- SIGTERM + wait; Litestream flushes WAL to S3
      2. ctrl.assert_wal_flushed(db)    -- guard: no stale WAL sidecar before file copy
      3. restore_with_integrity_check(backup, db)  -- copy + PRAGMA integrity_check
      4. ctrl.reset_generation()        -- tell replica this is a new generation
      5. ctrl.start()                   -- restart replication

    Skipping or reordering any step risks the replica overwriting the restored
    file before the generation reset, or a stale WAL being applied post-copy.
    """

    def __init__(
        self,
        replica_url: str,
        config_path: Path,
        stop_timeout: int = 30,
    ) -> None:
        self.replica_url = replica_url
        self.config_path = Path(config_path)
        self.stop_timeout = stop_timeout

    def stop(self) -> None:
        result = subprocess.run(
            ["litestream", "stop", "-config", str(self.config_path)],
            capture_output=True,
            timeout=self.stop_timeout,
        )
        if result.returncode != 0:
            raise LitestreamError(
                f"litestream stop failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace')}"
            )
        time.sleep(2)

    def start(self) -> None:
        result = subprocess.run(
            ["litestream", "replicate", "-config", str(self.config_path)],
            capture_output=True,
            timeout=10,
        )
        if result.returncode not in (0,):
            raise LitestreamError(
                f"litestream start failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace')}"
            )

    def assert_wal_flushed(self, db_path: Path) -> None:
        wal = Path(str(db_path) + "-wal")
        if wal.exists() and wal.stat().st_size > 0:
            raise LitestreamError(
                f"WAL file {wal} is non-empty ({wal.stat().st_size} bytes) after "
                "litestream stop — WAL was not fully flushed to S3. "
                "Do not copy the backup until the WAL is flushed."
            )

    def reset_generation(self) -> None:
        result = subprocess.run(
            ["litestream", "generations", "-config", str(self.config_path)],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise LitestreamError(
                f"litestream generation reset failed: "
                f"{result.stderr.decode(errors='replace')}"
            )
