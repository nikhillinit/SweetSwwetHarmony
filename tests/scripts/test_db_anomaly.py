import json
import sqlite3
from pathlib import Path

import pytest

from scripts.db_anomaly import AnomalyChecker, AnomalyResult


def test_clean_db_returns_no_anomalies(tmp_path):
    db = tmp_path / "signals.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO signals VALUES (1)")
    con.commit()
    con.close()
    checker = AnomalyChecker(db)
    result = checker.check()
    assert result.anomaly_type is None
    assert result.ok is True


def test_known_bad_sha_flagged(tmp_path):
    import hashlib
    db = tmp_path / "signals.db"
    db.write_bytes(b"\x00" * 1466368)
    bad_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    manifest = tmp_path / "known_bad_shas.json"
    manifest.write_text(json.dumps({"shas": [bad_sha]}))
    checker = AnomalyChecker(db, known_bad_shas_path=manifest)
    result = checker.check()
    assert result.ok is False
    assert result.anomaly_type == "known_bad_sha"


def test_row_count_drop_flagged(tmp_path):
    db = tmp_path / "signals.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
    for i in range(100):
        con.execute("INSERT INTO signals VALUES (?)", (i,))
    con.commit()
    con.close()
    watermark = tmp_path / "watermark.json"
    watermark.write_text(json.dumps({"min_row_count": 500}))
    checker = AnomalyChecker(db, watermark_path=watermark)
    result = checker.check()
    assert result.ok is False
    assert result.anomaly_type == "row_count_drop"


def test_manifest_written_on_check(tmp_path):
    db = tmp_path / "signals.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO signals VALUES (1)")
    con.commit()
    con.close()
    out = tmp_path / "anomaly_manifest.json"
    checker = AnomalyChecker(db, output_path=out)
    checker.check()
    assert out.exists()
    data = json.loads(out.read_text())
    assert "sha256" in data
    assert "row_count" in data
    assert "checked_at" in data
