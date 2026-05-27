import gzip
import ast
import json
import sqlite3
from pathlib import Path


def _current_schema_version() -> int:
    source = Path("storage/signal_store.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "CURRENT_SCHEMA_VERSION"
                for target in node.targets
            )
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("CURRENT_SCHEMA_VERSION not found")


CURRENT_SCHEMA_VERSION = _current_schema_version()


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER)")
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO signals (id, name) VALUES (?, ?)",
            [(1, "alpha"), (2, "bravo"), (3, "charlie")],
        )
        conn.commit()
    finally:
        conn.close()


def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_identical_input_produces_identical_gzip_hash(tmp_path):
    from scripts.sqlite_snapshot import create_snapshot

    db_path = tmp_path / "signals.db"
    _create_db(db_path)

    first = create_snapshot(
        db_path=db_path,
        out_dir=tmp_path / "first",
        created_at="2026-05-27T00:00:00Z",
    )
    second = create_snapshot(
        db_path=db_path,
        out_dir=tmp_path / "second",
        created_at="2026-05-27T00:00:00Z",
    )

    assert first.compressed_sha256 == second.compressed_sha256
    assert (tmp_path / "first" / "snapshot.db.gz").read_bytes() == (
        tmp_path / "second" / "snapshot.db.gz"
    ).read_bytes()

    with gzip.open(tmp_path / "first" / "snapshot.db.gz", "rb") as handle:
        assert handle.read(16).startswith(b"SQLite format 3")


def test_manifest_contains_hashes_row_counts_schema_version_and_timestamp(tmp_path):
    from scripts.sqlite_snapshot import create_snapshot

    db_path = tmp_path / "signals.db"
    manifest_out = tmp_path / "latest.manifest.json"
    _create_db(db_path)

    result = create_snapshot(
        db_path=db_path,
        out_dir=tmp_path / "snapshots",
        manifest_out=manifest_out,
        created_at="2026-05-27T12:34:56Z",
    )

    manifest = _read_manifest(manifest_out)
    assert manifest["source_db"]["sha256"] == result.source_sha256
    assert manifest["snapshot"]["uncompressed_sha256"] == result.uncompressed_sha256
    assert manifest["snapshot"]["compressed_sha256"] == result.compressed_sha256
    assert manifest["row_counts"]["signals"] == 3
    assert manifest["row_counts"]["schema_migrations"] == 1
    assert manifest["schema_version"] == CURRENT_SCHEMA_VERSION
    assert manifest["created_at"] == "2026-05-27T12:34:56Z"

    assert (tmp_path / "snapshots" / "snapshot.db.sha256").read_text(
        encoding="utf-8"
    ).startswith(result.uncompressed_sha256)
    assert (tmp_path / "snapshots" / "snapshot.db.gz.sha256").read_text(
        encoding="utf-8"
    ).startswith(result.compressed_sha256)


def test_snapshot_cleans_temp_files(tmp_path):
    from scripts.sqlite_snapshot import create_snapshot

    db_path = tmp_path / "signals.db"
    out_dir = tmp_path / "snapshots"
    _create_db(db_path)

    create_snapshot(db_path=db_path, out_dir=out_dir)

    leftovers = [
        path.name
        for path in out_dir.iterdir()
        if ".tmp" in path.name or path.suffix in {".tmp", ".part"}
    ]
    assert leftovers == []


def test_snapshot_does_not_require_production_credentials(tmp_path, monkeypatch):
    from scripts.sqlite_snapshot import create_snapshot

    for key in (
        "SQLITE_BACKUP_BUCKET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
    ):
        monkeypatch.delenv(key, raising=False)

    db_path = tmp_path / "signals.db"
    _create_db(db_path)

    result = create_snapshot(db_path=db_path, out_dir=tmp_path / "snapshots")

    assert result.compressed_path.exists()
    assert result.manifest_path.exists()
