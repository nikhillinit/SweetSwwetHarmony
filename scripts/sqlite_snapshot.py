"""Create deterministic SQLite snapshot artifacts.

The snapshot path is intentionally local-only. Off-host replication is handled
by Litestream; this script creates auditable point-in-time files for CI smoke
checks and operator-run daily/monthly snapshots.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SNAPSHOT_DB_NAME = "snapshot.db"
SNAPSHOT_GZIP_NAME = "snapshot.db.gz"
SNAPSHOT_MANIFEST_NAME = "snapshot.manifest.json"


@dataclass(frozen=True)
class SnapshotResult:
    compressed_path: Path
    manifest_path: Path
    source_sha256: str
    uncompressed_sha256: str
    compressed_sha256: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _checkpoint_wal_if_safe(db_path: Path) -> None:
    if not db_path.exists() or not db_path.is_file():
        return
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            conn.close()
    except sqlite3.Error:
        return


def _read_schema_version(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row[0]) if row and row[0] is not None else None


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    counts: dict[str, int] = {}
    for (name,) in rows:
        count = conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(name)}").fetchone()
        counts[str(name)] = int(count[0])
    return counts


def _write_stable_gzip(source: Path, destination: Path) -> None:
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=output_file,
            mtime=0,
        ) as gzip_file:
            shutil.copyfileobj(input_file, gzip_file)


def _write_sha_file(path: Path, digest: str, artifact_name: str) -> None:
    path.write_text(f"{digest}  {artifact_name}\n", encoding="utf-8")


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def create_snapshot(
    *,
    db_path: str | Path,
    out_dir: str | Path,
    manifest_out: str | Path | None = None,
    created_at: str | None = None,
) -> SnapshotResult:
    source_path = Path(db_path)
    if not source_path.exists():
        raise FileNotFoundError(f"database not found: {source_path}")

    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / SNAPSHOT_MANIFEST_NAME
    manifest_copy_path = Path(manifest_out) if manifest_out else manifest_path
    temp_db = output_dir / f".{SNAPSHOT_DB_NAME}.tmp"
    temp_gzip = output_dir / f".{SNAPSHOT_GZIP_NAME}.tmp"
    compressed_path = output_dir / SNAPSHOT_GZIP_NAME

    created_at = created_at or _utc_now()
    _checkpoint_wal_if_safe(source_path)

    try:
        with _connect_read_only(source_path) as conn:
            row_counts = _row_counts(conn)
            schema_version = _read_schema_version(conn)
            escaped_temp = str(temp_db).replace("'", "''")
            conn.execute(f"VACUUM INTO '{escaped_temp}'")

        uncompressed_sha256 = _sha256_file(temp_db)
        _write_stable_gzip(temp_db, temp_gzip)
        compressed_sha256 = _sha256_file(temp_gzip)
        source_sha256 = _sha256_file(source_path)

        temp_gzip.replace(compressed_path)
        _write_sha_file(
            output_dir / "snapshot.db.sha256",
            uncompressed_sha256,
            SNAPSHOT_DB_NAME,
        )
        _write_sha_file(
            output_dir / "snapshot.db.gz.sha256",
            compressed_sha256,
            SNAPSHOT_GZIP_NAME,
        )

        manifest: dict[str, Any] = {
            "created_at": created_at,
            "schema_version": schema_version,
            "source_db": {
                "path": str(source_path),
                "sha256": source_sha256,
                "size_bytes": source_path.stat().st_size,
            },
            "snapshot": {
                "path": str(compressed_path),
                "uncompressed_sha256": uncompressed_sha256,
                "compressed_sha256": compressed_sha256,
                "compressed_size_bytes": compressed_path.stat().st_size,
            },
            "row_counts": row_counts,
        }
        _write_manifest(manifest_path, manifest)
        if manifest_copy_path != manifest_path:
            _write_manifest(manifest_copy_path, manifest)

        return SnapshotResult(
            compressed_path=compressed_path,
            manifest_path=manifest_copy_path,
            source_sha256=source_sha256,
            uncompressed_sha256=uncompressed_sha256,
            compressed_sha256=compressed_sha256,
        )
    finally:
        for path in (temp_db, temp_gzip):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create deterministic SQLite snapshot artifacts.")
    parser.add_argument("--db-path", required=True, help="SQLite database path to snapshot.")
    parser.add_argument("--out-dir", required=True, help="Directory for snapshot artifacts.")
    parser.add_argument(
        "--manifest-out",
        default=None,
        help="Optional extra path for a copy of the snapshot manifest.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = create_snapshot(
            db_path=args.db_path,
            out_dir=args.out_dir,
            manifest_out=args.manifest_out,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(result.manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
