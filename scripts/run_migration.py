from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class MigrationError(RuntimeError):
    pass


V52_COLUMNS = [
    ("rows_returned_this_iter", "INTEGER"),
    ("rows_after_filter_this_iter", "INTEGER"),
    ("last_failure_mode", "TEXT"),
]


class MigrationRunner:
    def __init__(
        self,
        db_path: Path,
        target_version: int,
        writer_check_timeout: float = 5.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.target_version = target_version
        self.writer_check_timeout = writer_check_timeout

    def run(self) -> None:
        self._assert_no_active_writers()
        con = sqlite3.connect(self.db_path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        try:
            current = con.execute("SELECT version FROM schema_version").fetchone()[0]
            if current >= self.target_version:
                return
            if self.target_version == 52:
                self._apply_v52(con)
            con.execute("UPDATE schema_version SET version = ?", (self.target_version,))
            con.commit()
        finally:
            con.close()

    def _assert_no_active_writers(self) -> None:
        deadline = time.monotonic() + self.writer_check_timeout
        while time.monotonic() < deadline:
            try:
                con = sqlite3.connect(self.db_path, timeout=0.05)
                con.execute("BEGIN EXCLUSIVE")
                con.rollback()
                con.close()
                return
            except sqlite3.OperationalError:
                time.sleep(0.05)
        raise MigrationError(
            f"active writer detected on {self.db_path} after "
            f"{self.writer_check_timeout}s — stop all collectors before migrating"
        )

    def _apply_v52(self, con: sqlite3.Connection) -> None:
        existing = {row[1] for row in con.execute("PRAGMA table_info(signals)")}
        for col_name, col_type in V52_COLUMNS:
            if col_name not in existing:
                con.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_type}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run schema migration with single-writer coordination")
    parser.add_argument("db_path")
    parser.add_argument("--target-version", type=int, required=True)
    parser.add_argument("--writer-check-timeout", type=float, default=5.0)
    args = parser.parse_args()
    MigrationRunner(
        Path(args.db_path),
        target_version=args.target_version,
        writer_check_timeout=args.writer_check_timeout,
    ).run()
    print(f"Migration to v{args.target_version} complete.")
