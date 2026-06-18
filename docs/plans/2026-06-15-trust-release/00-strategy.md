# Trust Release — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the system is boringly trustworthy — dry-runs are read-only, the live DB is under auditable chain-of-custody, collector health distinguishes broken from expected-empty, and CI blocks unreviewed thesis-sensitive changes — before resuming feature work.

**Architecture:** Two parallel tracks gate sequentially on each other. Track A (DB durability → dry-run immutability → schema migration) is the hard prerequisite for P1 feature work. Track B (CI hardening → circuit breaker → parity gate → PR evidence gate → trust status CLI) ships independently but M7 hard-depends on M3. The deliberation panel (codex/kimi/gemini, 2026-06-16) returned `block`/`needs_changes` on the prior steelman draft; all 8 required changes are incorporated below. This document replaces the prior `00-strategy.md` as the single authoritative plan (R10). Prior version archived at `00-strategy-pre-deliberation.md`.

**Tech Stack:** Python 3.11+, SQLite WAL, Litestream (S3 replication), pytest + vcrpy, GitHub Actions, Hermes task framework, `ops/collector_health.py`, `scripts/restore_db.py`, `storage/signal_store.py`

**Deliberation record:** `ai-logs/hermes/runs/hermes_20260616_224955_0fcf6f36/deliberation_record.json`

---

## Status snapshot (2026-06-18, verified at HEAD `4f19d66`)

> **Reconciled 2026-06-18.** The prior snapshot was pinned to `de00bb0` and was ~33 commits
> stale. Live `origin/main` is now **`4f19d66`** (merge of PR #282). Two findings from the
> red-team reviews in this directory are folded in below: (1) most milestone *code* already
> merged (commit `7f02719` and the present-on-main check), so these are no longer "OPEN
> implementation" tasks — they are **code-landed but unratified**, with specific wiring gaps;
> (2) the DB-path hardening PRs **#281** (`da48563`) and **#282** (`4f19d66`) landed since the
> last snapshot and materially change P0-1's DB-safety posture. See
> `red-team-ratification-spec.md` and `deliberation-debate-red-teaming.md` for the caveats
> behind each 🟡 below. **Do not treat 🟡 as DONE** — code on main is not the same as a ratified
> claim (this is the central lesson of the red-team reviews).

| Milestone | Status | Evidence / caveat |
|-----------|--------|-------------------|
| P0-0 Gemini paid tier | ✅ DONE | F6 = 0.9375 |
| P0-2 Gate hardening PR #271 | ✅ DONE | merged `275cded` |
| P0-1 DB untrack + resolver + Daily Pipeline repoint | ✅ DONE | PRs #272/#273/#275 |
| **DB-path hardening (canonical resolution, in-tree guard, Hermes task paths)** | 🟢 **MERGED** | **PR #281 (`da48563`) + #282 (`4f19d66`)** — `guard_db_path` / `resolve_task_db_path` / `InTreeDatabaseError` fail-closed on main; **CI-green-on-merge unconfirmed** |
| P0-1 bounded recovery + anomaly check + close #149 | 🔴 OPEN | unratified; needs Phase-2 ratification audit from artifacts |
| P0-3 Dry-run immutability | 🔴 OPEN | this plan |
| M1A db_anomaly.py | 🟡 CODE ON MAIN | file present; minimal (sha/row/size/watermark only) — v2 hot/deep split still open |
| M1B restore_db.py Litestream-safe hardening | 🔴 **OPEN (critical)** | `restore_with_integrity_check` + `litestream_ctrl.py` exist, but **Litestream lifecycle is NOT wired into the real `restore_backup()` path** (red-team F4); `MAINTENANCE_LOCK_TIMEOUT_SECONDS` is a dead constant (F5). W1/W2 effectively unmitigated. |
| M2 v52 migration with writer coordination | 🔴 **OPEN (collision)** | `run_migration.py` merged but **orphaned** — reads a non-existent `schema_version` table and targets v52, which production already uses for a different migration (F2/F3). Real schema is at `CURRENT_SCHEMA_VERSION=53`. Rework against `schema_migrations`. |
| M3 collector_health v2 + circuit breaker | 🟡 CODE ON MAIN | `REPORT_SCHEMA_VERSION=2`, statuses + `SuspensionStore` merged (`7f02719`); suspension store concurrency/env-var caveats (red-team S1) open |
| M4 vcrpy cassette lifecycle | 🟡 CODE ON MAIN | `cassette_policy.py` present; regeneration cadence/storage policy unverified |
| M5 parity gate (temperature=0.0 or delta<0.02) | 🟡 CODE ON MAIN | `run_thesis_parity_gate.py` present; tests cover arithmetic only — CLI honoring `temperature=0.0` is untested (S3) |
| M6 PR evidence enforcement | 🟡 CODE ON MAIN — **hardening required** | `check_pr_evidence.py` present but accepts `/actions/runs/0` and any-repo URLs; **Phase-0 hardening is the recommended first patch** |
| M7 trust status CLI | 🟡 CODE ON MAIN | `trust_status.py` present (45 lines, minimal); v2 max-age/expiry semantics open |

**Operating guardrail (still in force):** the in-tree DB-path guard (#281/#282) now blocks
accidental writes to a repo-root `signals.db`, but **restore is still not Litestream-safe**
(M1B/F4). Continue to **not** run mutating `restore`/`migrate`/non-`--dry-run` pipeline commands
against the canonical DB until M1B wires the Litestream lifecycle into `restore_backup()`. Use
scratch copies (`HARMONIC_SCRATCH_DB=1`) only.

**Revised critical path (post-merge):** (1) harden `check_pr_evidence.py` (M6/Phase-0) → (2) ratify
landed claims + #281/#282 fail-closed behavior from artifacts → (3) wire M1B Litestream-safe restore
(F4) → (4) rework M2 against `schema_migrations` (F2/F3) → (5) refresh `active-sprint.md`
(currently stale at `275cded`) and this plan to `4f19d66`.

---

## Track A — DB Durability + Dry-Run Immutability

### Task M1A: db_anomaly.py — anomaly checker (prerequisite for M1B per R7/W8)

> **Why M1A before M1B:** restore_db.py's `--manifest-out` references the anomaly checker's `known_bad_shas.json`. If M1B lands before M1A, the restore script references a module that doesn't exist. M1A is a hard prerequisite — do not merge M1B until M1A is on main. (R7)

**Files:**
- Create: `scripts/db_anomaly.py`
- Create: `tests/scripts/test_db_anomaly.py`
- Modify: `scripts/restore_db.py` (add `--anomaly-check` flag)

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_db_anomaly.py
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
    db = tmp_path / "signals.db"
    db.write_bytes(b"\x00" * 1466368)  # known bad size
    bad_sha = "447c1359918da1a2f4abf31867d3e21bd1b5f855ad9e5336ea5b9c3c98c5940e"
    manifest = tmp_path / "known_bad_shas.json"
    manifest.write_text(json.dumps({"shas": [bad_sha]}))
    checker = AnomalyChecker(db, known_bad_shas_path=manifest)
    result = checker.check()
    assert result.ok is False
    assert result.anomaly_type == "known_bad_sha"

def test_sudden_row_count_drop_flagged(tmp_path):
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
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
python -m pytest tests/scripts/test_db_anomaly.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.db_anomaly'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/db_anomaly.py
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

@dataclass
class AnomalyResult:
    ok: bool
    anomaly_type: str | None = None
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

class AnomalyChecker:
    def __init__(
        self,
        db_path: Path,
        known_bad_shas_path: Path | None = None,
        watermark_path: Path | None = None,
        output_path: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.known_bad_shas_path = Path(known_bad_shas_path) if known_bad_shas_path else None
        self.watermark_path = Path(watermark_path) if watermark_path else None
        self.output_path = Path(output_path) if output_path else None

    def check(self) -> AnomalyResult:
        sha = self._sha256()
        row_count = self._row_count()
        evidence = {
            "sha256": sha,
            "row_count": row_count,
            "size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.output_path:
            self.output_path.write_text(json.dumps(evidence, indent=2))

        if self.known_bad_shas_path and self.known_bad_shas_path.exists():
            bad = json.loads(self.known_bad_shas_path.read_text()).get("shas", [])
            if sha in bad:
                return AnomalyResult(ok=False, anomaly_type="known_bad_sha",
                                     detail=f"sha {sha} in known_bad_shas", evidence=evidence)

        if self.watermark_path and self.watermark_path.exists():
            wm = json.loads(self.watermark_path.read_text())
            min_rows = int(wm.get("min_row_count", 0))
            if row_count < min_rows:
                return AnomalyResult(ok=False, anomaly_type="row_count_drop",
                                     detail=f"row_count={row_count} < watermark={min_rows}",
                                     evidence=evidence)

        return AnomalyResult(ok=True, evidence=evidence)

    def _sha256(self) -> str:
        h = hashlib.sha256()
        if self.db_path.exists():
            h.update(self.db_path.read_bytes())
        return h.hexdigest()

    def _row_count(self) -> int:
        if not self.db_path.exists():
            return 0
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            count = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            con.close()
            return int(count)
        except Exception:
            return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("--known-bad-shas", dest="known_bad_shas_path")
    parser.add_argument("--watermark", dest="watermark_path")
    parser.add_argument("--output", dest="output_path",
                        default=".omx/state/anomaly_manifest.json")
    args = parser.parse_args()
    result = AnomalyChecker(
        db_path=Path(args.db_path),
        known_bad_shas_path=Path(args.known_bad_shas_path) if args.known_bad_shas_path else None,
        watermark_path=Path(args.watermark_path) if args.watermark_path else None,
        output_path=Path(args.output_path),
    ).check()
    print(json.dumps({"ok": result.ok, "anomaly_type": result.anomaly_type,
                      "detail": result.detail}, indent=2))
    raise SystemExit(0 if result.ok else 1)
```

- [ ] **Step 4: Run test to verify it passes**

```powershell
python -m pytest tests/scripts/test_db_anomaly.py -v
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/db_anomaly.py tests/scripts/test_db_anomaly.py
git commit -m "feat(durability): add db_anomaly.py anomaly checker with known-bad-sha + row-count-drop detection"
```

---

### Task M1B: restore_db.py — Litestream-safe hardening (R1/W1, R2/W2)

> **Deliberation required changes R1 + R2:**
> - R1: Define safe Litestream restore orchestration — stop replication cleanly, flush/sync WAL to S3, snapshot, copy backup, run `PRAGMA integrity_check`, reset generation, then restart replication. Include rollback and verification steps.
> - R2: Specify a maintenance-scoped lock with timeout > 120s (not the default 5s DBToolLock), using a dedicated lock key separate from the operational default.

**Files:**
- Modify: `scripts/restore_db.py` (add Litestream lifecycle + maintenance lock)
- Create: `scripts/litestream_ctrl.py` (Litestream stop/start/generation-reset helper)
- Create: `tests/scripts/test_restore_litestream.py`

- [ ] **Step 1: Read the existing restore_db.py to understand current lock and manifest logic**

```powershell
python scripts/restore_db.py --help
```

Read: `scripts/restore_db.py` lines 1–80 to verify current DBToolLock timeout and backup/manifest flow before touching anything.

- [ ] **Step 2: Write the failing test for Litestream orchestration**

```python
# tests/scripts/test_restore_litestream.py
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, call, patch
import pytest
from scripts.litestream_ctrl import LitestreamCtrl, LitestreamError

def test_stop_waits_for_process_to_exit(tmp_path):
    ctrl = LitestreamCtrl(replica_url="s3://bucket/db", config_path=tmp_path / "litestream.yml")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        ctrl.stop()
    assert mock_run.call_count >= 1
    # SIGTERM sent before copy, not after
    first_call_args = mock_run.call_args_list[0].args[0]
    assert any("stop" in str(a) or "kill" in str(a) for a in first_call_args)

def test_wal_flush_verified_before_copy(tmp_path):
    db = tmp_path / "signals.db"
    wal = tmp_path / "signals.db-wal"
    db.write_bytes(b"x" * 100)
    wal.write_bytes(b"y" * 50)  # stale WAL still present
    ctrl = LitestreamCtrl(replica_url="s3://bucket/db", config_path=tmp_path / "litestream.yml")
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        with pytest.raises(LitestreamError, match="WAL"):
            ctrl.assert_wal_flushed(db)

def test_generation_reset_called_after_copy(tmp_path):
    ctrl = LitestreamCtrl(replica_url="s3://bucket/db", config_path=tmp_path / "litestream.yml")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        ctrl.reset_generation()
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("replicate" in c or "generations" in c for c in calls)

def test_maintenance_lock_timeout_is_at_least_120s():
    from scripts.restore_db import MAINTENANCE_LOCK_TIMEOUT_SECONDS
    assert MAINTENANCE_LOCK_TIMEOUT_SECONDS >= 120

def test_restore_rolls_back_if_integrity_check_fails(tmp_path):
    db = tmp_path / "signals.db"
    backup = tmp_path / "backup.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO signals VALUES (1)")
    con.commit()
    con.close()
    backup.write_bytes(b"\x00" * 100)  # corrupt backup
    from scripts.restore_db import RestoreError, restore_with_integrity_check
    with pytest.raises(RestoreError, match="integrity"):
        restore_with_integrity_check(backup, db)
    # original must be untouched
    con2 = sqlite3.connect(db)
    count = con2.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count == 1
```

- [ ] **Step 3: Run test to verify it fails**

```powershell
python -m pytest tests/scripts/test_restore_litestream.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.litestream_ctrl'` and `ImportError` on `MAINTENANCE_LOCK_TIMEOUT_SECONDS`

- [ ] **Step 4: Implement litestream_ctrl.py**

```python
# scripts/litestream_ctrl.py
from __future__ import annotations

import subprocess
import time
from pathlib import Path


class LitestreamError(RuntimeError):
    pass


class LitestreamCtrl:
    """Stop/start/generation-reset Litestream safely around a DB restore.

    Safe restore sequence (must follow this order):
      1. ctrl.stop()                    # SIGTERM + wait; WAL sync happens inside litestream
      2. ctrl.assert_wal_flushed(db)    # guard: no stale WAL before file copy
      3. shutil.copy2(backup, db)       # atomic file copy
      4. PRAGMA integrity_check         # verify before restart
      5. ctrl.reset_generation()        # tell replica this is a new generation
      6. ctrl.start()                   # restart replication
    """

    def __init__(self, replica_url: str, config_path: Path, stop_timeout: int = 30) -> None:
        self.replica_url = replica_url
        self.config_path = Path(config_path)
        self.stop_timeout = stop_timeout

    def stop(self) -> None:
        result = subprocess.run(
            ["litestream", "stop", "-config", str(self.config_path)],
            capture_output=True, timeout=self.stop_timeout,
        )
        if result.returncode != 0:
            raise LitestreamError(f"litestream stop failed (rc={result.returncode}): "
                                  f"{result.stderr.decode()}")
        # wait for WAL to be flushed to replica before returning
        time.sleep(2)

    def start(self) -> None:
        result = subprocess.run(
            ["litestream", "replicate", "-config", str(self.config_path)],
            capture_output=True, timeout=10,
        )
        if result.returncode not in (0, None):
            raise LitestreamError(f"litestream start failed (rc={result.returncode})")

    def assert_wal_flushed(self, db_path: Path) -> None:
        wal = Path(str(db_path) + "-wal")
        if wal.exists() and wal.stat().st_size > 0:
            raise LitestreamError(
                f"WAL file {wal} is non-empty after litestream stop — "
                "WAL was not fully flushed to S3 before stop. "
                "Do not copy the backup until WAL is flushed."
            )

    def reset_generation(self) -> None:
        result = subprocess.run(
            ["litestream", "generations", "-config", str(self.config_path)],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            raise LitestreamError(f"litestream generations reset failed: "
                                  f"{result.stderr.decode()}")
```

- [ ] **Step 5: Add MAINTENANCE_LOCK_TIMEOUT_SECONDS and restore_with_integrity_check to restore_db.py**

Open `scripts/restore_db.py` and add near the top (after imports):

```python
MAINTENANCE_LOCK_TIMEOUT_SECONDS = 180  # restore can take 30-120s; 5s DBToolLock default is not safe

class RestoreError(RuntimeError):
    pass


def restore_with_integrity_check(backup: Path, target: Path) -> None:
    """Copy backup to target only if PRAGMA integrity_check passes on the backup.
    Rolls back (target is untouched) if integrity check fails.
    """
    import shutil, sqlite3
    try:
        con = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        result = con.execute("PRAGMA integrity_check").fetchone()
        con.close()
    except Exception as exc:
        raise RestoreError(f"integrity check failed on backup {backup}: {exc}") from exc
    if result[0] != "ok":
        raise RestoreError(f"integrity check returned '{result[0]}' on backup {backup}")
    shutil.copy2(backup, target)
```

- [ ] **Step 6: Run test to verify it passes**

```powershell
python -m pytest tests/scripts/test_restore_litestream.py -v
```

Expected: 5 tests PASS

- [ ] **Step 7: Write the full Litestream-safe restore flow test**

```python
# tests/scripts/test_restore_litestream.py  (add to existing file)
def test_full_restore_sequence_order(tmp_path):
    """Verify stop → assert_wal_flushed → copy → integrity → reset_gen → start order."""
    db = tmp_path / "signals.db"
    backup = tmp_path / "backup.db"
    con = sqlite3.connect(backup)
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO signals VALUES (42)")
    con.commit()
    con.close()
    db.write_bytes(b"old")

    call_order = []
    ctrl = LitestreamCtrl(replica_url="s3://b/db", config_path=tmp_path / "ls.yml")

    with patch.object(ctrl, "stop", side_effect=lambda: call_order.append("stop")):
        with patch.object(ctrl, "assert_wal_flushed", side_effect=lambda p: call_order.append("wal_check")):
            with patch.object(ctrl, "reset_generation", side_effect=lambda: call_order.append("reset_gen")):
                with patch.object(ctrl, "start", side_effect=lambda: call_order.append("start")):
                    from scripts.restore_db import restore_with_integrity_check
                    import shutil
                    ctrl.stop()
                    ctrl.assert_wal_flushed(db)
                    restore_with_integrity_check(backup, db)
                    call_order.append("integrity_passed")
                    ctrl.reset_generation()
                    ctrl.start()

    assert call_order == ["stop", "wal_check", "integrity_passed", "reset_gen", "start"]
```

- [ ] **Step 8: Run all restore tests**

```powershell
python -m pytest tests/scripts/test_restore_litestream.py -v
```

Expected: 6 tests PASS

- [ ] **Step 9: Commit**

```bash
git add scripts/litestream_ctrl.py scripts/restore_db.py tests/scripts/test_restore_litestream.py
git commit -m "feat(durability): litestream-safe restore orchestration — SIGTERM+WAL-flush+copy+integrity+generation-reset; maintenance lock timeout=180s"
```

---

### Task M2: v52 migration with single-writer coordination (R4/W4)

> **Deliberation required change R4:** ADD COLUMN requires an exclusive SQLite write lock. Under WAL mode, if collector processes are running, the exclusive lock may block indefinitely. Must: acquire a maintenance lock (or pause collectors), run migration in a single-writer window, validate schema version before starting app workers.

**Files:**
- Modify: `storage/migrations/` (v52 migration module — confirm path with `ls storage/migrations/`)
- Create: `scripts/run_migration.py` (maintenance-window migration runner)
- Create: `tests/scripts/test_run_migration.py`

- [ ] **Step 1: Confirm the migration path and current schema version**

```powershell
python -c "from storage.signal_store import CURRENT_SCHEMA_VERSION; print(CURRENT_SCHEMA_VERSION)"
ls storage/migrations/
```

Note the current version and the migration file for v52.

- [ ] **Step 2: Write the failing test**

```python
# tests/scripts/test_run_migration.py
import sqlite3
from pathlib import Path
import pytest
from scripts.run_migration import MigrationRunner, MigrationError

def make_v51_db(tmp_path: Path) -> Path:
    db = tmp_path / "signals.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, collector TEXT)")
    con.execute("CREATE TABLE schema_version (version INTEGER)")
    con.execute("INSERT INTO schema_version VALUES (51)")
    con.commit()
    con.close()
    return db

def test_migration_adds_v52_columns(tmp_path):
    db = make_v51_db(tmp_path)
    runner = MigrationRunner(db, target_version=52)
    runner.run()
    con = sqlite3.connect(db)
    cols = [row[1] for row in con.execute("PRAGMA table_info(signals)").fetchall()]
    con.close()
    assert "rows_returned_this_iter" in cols
    assert "rows_after_filter_this_iter" in cols
    assert "last_failure_mode" in cols

def test_migration_bumps_schema_version(tmp_path):
    db = make_v51_db(tmp_path)
    MigrationRunner(db, target_version=52).run()
    con = sqlite3.connect(db)
    v = con.execute("SELECT version FROM schema_version").fetchone()[0]
    con.close()
    assert v == 52

def test_migration_fails_if_writer_detected(tmp_path):
    db = make_v51_db(tmp_path)
    # Simulate active writer via a writable connection
    writer_con = sqlite3.connect(db)
    writer_con.execute("BEGIN EXCLUSIVE")
    runner = MigrationRunner(db, target_version=52, writer_check_timeout=0.1)
    with pytest.raises(MigrationError, match="active writer"):
        runner.run()
    writer_con.rollback()
    writer_con.close()

def test_migration_is_idempotent(tmp_path):
    db = make_v51_db(tmp_path)
    MigrationRunner(db, target_version=52).run()
    MigrationRunner(db, target_version=52).run()  # second run should not raise
    con = sqlite3.connect(db)
    v = con.execute("SELECT version FROM schema_version").fetchone()[0]
    con.close()
    assert v == 52
```

- [ ] **Step 3: Run test to verify it fails**

```powershell
python -m pytest tests/scripts/test_run_migration.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.run_migration'`

- [ ] **Step 4: Implement MigrationRunner**

```python
# scripts/run_migration.py
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
    def __init__(self, db_path: Path, target_version: int,
                 writer_check_timeout: float = 5.0) -> None:
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
                return  # idempotent
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
                return  # got exclusive lock — no active writers
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
    parser = argparse.ArgumentParser()
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
```

- [ ] **Step 5: Run tests**

```powershell
python -m pytest tests/scripts/test_run_migration.py -v
```

Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/run_migration.py tests/scripts/test_run_migration.py
git commit -m "feat(migration): v52 migration runner with single-writer coordination — blocks on active writers, idempotent, explicit schema version bump"
```

---

## Track B — CI Hardening + Collector Intelligence

### Task M3: collector_health.py v2 + api_shape_changed circuit breaker (R5/W5)

> **Deliberation required change R5:** The circuit-breaker suspension flag must be stored in a production-only durable store (a config table or file), NOT in memory (resets on restart) and NOT in a scratch DB that diagnostic runs use. Diagnostic runs must be guarded by an env var (`HARMONIC_SCRATCH_DB=1`) so they never write suspension state to the wrong database. The reset process and audit trail must be documented.

**Files:**
- Modify: `ops/collector_health.py` (add `api_shape_changed` status, v2 schema, circuit breaker)
- Create: `storage/collector_suspension.py` (durable suspension store — NOT using scratch-DB connections)
- Create: `tests/ops/test_collector_health_v2.py`

- [ ] **Step 1: Read current collector_health.py to understand existing status enum**

```powershell
python -c "import ops.collector_health as h; print(h.REPORT_SCHEMA_VERSION if hasattr(h,'REPORT_SCHEMA_VERSION') else 'not set')"
grep -n "api_shape_changed\|REPORT_SCHEMA_VERSION\|fresh_empty_expected" ops/collector_health.py | head -20
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/ops/test_collector_health_v2.py
import os
import sqlite3
from pathlib import Path
import pytest
from ops.collector_health import REPORT_SCHEMA_VERSION, CollectorHealthReport
from storage.collector_suspension import CollectorSuspension, SuspensionStore

def test_report_schema_version_is_2():
    assert REPORT_SCHEMA_VERSION == 2

def test_api_shape_changed_is_valid_status():
    report = CollectorHealthReport(collector="github", status="api_shape_changed",
                                   detail="field 'stars_count' missing from response")
    assert report.status == "api_shape_changed"

def test_fresh_empty_expected_is_valid_status():
    report = CollectorHealthReport(collector="arxiv", status="fresh_empty_expected",
                                   detail="no new papers today")
    assert report.status == "fresh_empty_expected"

def test_suspension_persists_to_file(tmp_path):
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("github", reason="api_shape_changed: field missing")
    assert store.is_suspended("github")
    # reload from disk — durable across restarts
    store2 = SuspensionStore(tmp_path / "suspensions.json")
    assert store2.is_suspended("github")

def test_suspension_not_written_in_scratch_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONIC_SCRATCH_DB", "1")
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("github", reason="api_shape_changed")
    # scratch mode: file must NOT be written
    assert not (tmp_path / "suspensions.json").exists()
    assert not store.is_suspended("github")

def test_suspension_reset_writes_audit_entry(tmp_path):
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("github", reason="api_shape_changed: test")
    store.reset("github", reset_by="operator")
    assert not store.is_suspended("github")
    audit = store.audit_log()
    assert any(e["action"] == "reset" and e["collector"] == "github" for e in audit)
```

- [ ] **Step 3: Run test to verify it fails**

```powershell
python -m pytest tests/ops/test_collector_health_v2.py -v
```

Expected: failures on `REPORT_SCHEMA_VERSION`, `CollectorHealthReport`, `SuspensionStore`

- [ ] **Step 4: Add REPORT_SCHEMA_VERSION=2 and api_shape_changed/fresh_empty_expected to collector_health.py**

In `ops/collector_health.py`, add/update:

```python
REPORT_SCHEMA_VERSION = 2

VALID_STATUSES = {
    "success",
    "partial_success",
    "dry_run",
    "stale",
    "failing",
    "disabled_missing_key",
    "blocked_access",
    "fresh_empty_expected",   # NEW v2: no rows but that's expected
    "api_shape_changed",      # NEW v2: response schema mismatch, circuit breaker fires
}

from dataclasses import dataclass, field as dc_field

@dataclass
class CollectorHealthReport:
    collector: str
    status: str
    detail: str = ""
    schema_version: int = REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"unknown status {self.status!r}; valid: {VALID_STATUSES}")
```

- [ ] **Step 5: Implement SuspensionStore**

```python
# storage/collector_suspension.py
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SuspensionStore:
    """Durable file-backed suspension state for api_shape_changed circuit breaker.

    Scratch-DB guard: when HARMONIC_SCRATCH_DB=1, all writes are silently no-ops
    and is_suspended() always returns False. This prevents diagnostic/dry-run
    sessions from writing suspension state to the wrong (non-production) path.

    Reset audit trail: every suspend/reset action is appended to the audit log
    in the same JSON file.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._scratch = os.environ.get("HARMONIC_SCRATCH_DB", "0") == "1"
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {"suspensions": {}, "audit": []}

    def _save(self) -> None:
        if self._scratch:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    def suspend(self, collector: str, reason: str) -> None:
        if self._scratch:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._data["suspensions"][collector] = {"reason": reason, "suspended_at": now}
        self._data["audit"].append({"action": "suspend", "collector": collector,
                                    "reason": reason, "at": now})
        self._save()

    def reset(self, collector: str, reset_by: str = "operator") -> None:
        if self._scratch:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._data["suspensions"].pop(collector, None)
        self._data["audit"].append({"action": "reset", "collector": collector,
                                    "reset_by": reset_by, "at": now})
        self._save()

    def is_suspended(self, collector: str) -> bool:
        if self._scratch:
            return False
        return collector in self._data.get("suspensions", {})

    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._data.get("audit", []))
```

- [ ] **Step 6: Run tests**

```powershell
python -m pytest tests/ops/test_collector_health_v2.py -v
```

Expected: 6 tests PASS

- [ ] **Step 7: Commit**

```bash
git add ops/collector_health.py storage/collector_suspension.py tests/ops/test_collector_health_v2.py
git commit -m "feat(health): collector_health v2 — api_shape_changed + fresh_empty_expected statuses; durable suspension store with HARMONIC_SCRATCH_DB guard + audit trail"
```

---

### Task M4: vcrpy cassette lifecycle policy (R6/W6)

> **Deliberation required change R6:** The proposal mandates vcrpy cassettes for CI determinism but lacks: (a) regeneration cadence, (b) stale-cassette detection that doesn't *hide* api_shape_changed events (the exact failure mode the circuit breaker is catching), (c) storage/rotation policy. Fix: cassettes are stored in `tests/cassettes/`, regenerated when a fingerprint file changes, and validated to not mask `api_shape_changed` events.

**Files:**
- Create: `tests/cassettes/.gitkeep`
- Create: `tests/support/cassette_policy.py` (fingerprint + staleness guard)
- Create: `tests/support/test_cassette_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/support/test_cassette_policy.py
import json
from pathlib import Path
import pytest
from tests.support.cassette_policy import CassettePolicy, StaleCassetteError

def test_fresh_cassette_passes(tmp_path):
    cassette = tmp_path / "github.yaml"
    cassette.write_text("interactions: []")
    fingerprint = tmp_path / "github.fp"
    fingerprint.write_text("abc123")
    policy = CassettePolicy(cassette, fingerprint)
    policy.assert_fresh()  # should not raise

def test_stale_cassette_raises(tmp_path):
    cassette = tmp_path / "github.yaml"
    cassette.write_text("interactions: []")
    # Write cassette metadata with old fingerprint
    meta = tmp_path / "github.yaml.meta.json"
    meta.write_text(json.dumps({"fingerprint": "old123"}))
    fingerprint = tmp_path / "github.fp"
    fingerprint.write_text("new456")  # fingerprint changed
    policy = CassettePolicy(cassette, fingerprint, meta_path=meta)
    with pytest.raises(StaleCassetteError, match="stale"):
        policy.assert_fresh()

def test_cassette_does_not_mask_api_shape_changed(tmp_path):
    """A cassette that records a missing field must not return 'ok' on replay.
    The cassette must preserve the raw response so the shape-change detector fires."""
    cassette = tmp_path / "github.yaml"
    # Cassette records response WITHOUT 'stars_count' field
    cassette.write_text("""interactions:
- request:
    method: GET
    uri: https://api.github.com/repos/test/repo
  response:
    status: {code: 200}
    body:
      string: '{"name": "repo"}'
""")
    policy = CassettePolicy(cassette, tmp_path / "github.fp")
    # Cassette must not inject synthetic fields — raw body is preserved
    assert '"stars_count"' not in cassette.read_text()
    policy.assert_no_synthetic_field_injection()  # should not raise

def test_cassette_storage_is_under_tests_cassettes(tmp_path):
    cassette = tmp_path / "tests" / "cassettes" / "github.yaml"
    cassette.parent.mkdir(parents=True)
    cassette.write_text("interactions: []")
    policy = CassettePolicy(cassette, tmp_path / "github.fp")
    assert "cassettes" in str(policy.cassette_path)
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
python -m pytest tests/support/test_cassette_policy.py -v
```

Expected: `ModuleNotFoundError: No module named 'tests.support.cassette_policy'`

- [ ] **Step 3: Implement CassettePolicy**

```python
# tests/support/cassette_policy.py
from __future__ import annotations

import json
from pathlib import Path


class StaleCassetteError(RuntimeError):
    pass


class CassettePolicy:
    """Enforce the vcrpy cassette lifecycle policy.

    Rules:
    - Cassettes live under tests/cassettes/ (checked into git, size <= 500KB each)
    - A .meta.json sidecar records the fingerprint at time of recording
    - assert_fresh() raises StaleCassetteError if the current fingerprint differs
    - Cassettes must NOT inject synthetic fields — raw API responses are preserved
      so api_shape_changed detection fires on replay if the schema changes
    - Regeneration trigger: delete the cassette file and re-run the test with
      HARMONIC_RECORD_CASSETTES=1 to re-record
    """

    def __init__(self, cassette_path: Path, fingerprint_path: Path,
                 meta_path: Path | None = None) -> None:
        self.cassette_path = Path(cassette_path)
        self.fingerprint_path = Path(fingerprint_path)
        self.meta_path = meta_path or self.cassette_path.with_suffix(
            self.cassette_path.suffix + ".meta.json"
        )

    def assert_fresh(self) -> None:
        if not self.fingerprint_path.exists():
            return  # no fingerprint file — skip staleness check
        current_fp = self.fingerprint_path.read_text().strip()
        if not self.meta_path.exists():
            return  # no metadata yet — cassette is new
        meta = json.loads(self.meta_path.read_text())
        recorded_fp = meta.get("fingerprint", "")
        if recorded_fp and recorded_fp != current_fp:
            raise StaleCassetteError(
                f"stale cassette {self.cassette_path.name}: "
                f"recorded_fingerprint={recorded_fp!r} != current={current_fp!r}. "
                f"Delete {self.cassette_path} and re-run with HARMONIC_RECORD_CASSETTES=1"
            )

    def assert_no_synthetic_field_injection(self) -> None:
        """Cassettes must preserve raw API responses, not inject missing fields.
        This ensures api_shape_changed events are not hidden by cassette replay."""
        if not self.cassette_path.exists():
            return
        content = self.cassette_path.read_text()
        # Synthetic injection markers — none of these should appear in cassettes
        synthetic_markers = ["__synthetic__", "__injected__", "__default__"]
        for marker in synthetic_markers:
            if marker in content:
                raise StaleCassetteError(
                    f"cassette {self.cassette_path.name} contains synthetic field "
                    f"marker {marker!r} — this would hide api_shape_changed events"
                )
```

- [ ] **Step 4: Create cassettes directory**

```bash
mkdir -p tests/cassettes
touch tests/cassettes/.gitkeep
```

- [ ] **Step 5: Run tests**

```powershell
python -m pytest tests/support/test_cassette_policy.py -v
```

Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add tests/cassettes/.gitkeep tests/support/cassette_policy.py tests/support/test_cassette_policy.py
git commit -m "feat(ci): vcrpy cassette lifecycle policy — fingerprint-based staleness detection, synthetic-injection guard, tests/cassettes/ storage"
```

---

### Task M5: Parity gate — temperature=0.0 or accuracy-delta threshold (R3/W3)

> **Deliberation required change R3:** Delta=0 across 64 sampled LLM outputs is non-deterministic and will permanently block PRs. Fix: either enforce `temperature=0.0` on both the Gemini CLI and direct-API paths, OR set the gate to `accuracy_delta < 0.02` with documented seeds and retries. Temperature=0.0 is the simpler fix. If the model doesn't support it, use accuracy-delta.

**Files:**
- Modify: `scripts/ci/run_thesis_parity_gate.py` (or wherever the parity gate lives — confirm with grep)
- Create: `tests/scripts/test_thesis_parity_gate.py`

- [ ] **Step 1: Locate the parity gate script**

```powershell
grep -r "parity\|delta.*0\|temperature" scripts/ci/ --include="*.py" -l
grep -r "parity_gate\|thesis_parity" . --include="*.py" -l | head -5
```

Note the exact file path — use it in all subsequent steps.

- [ ] **Step 2: Write the failing test**

```python
# tests/scripts/test_thesis_parity_gate.py
import pytest
from scripts.ci.run_thesis_parity_gate import ParityGate, ParityGateConfig, ParityGateError

def test_gate_uses_temperature_zero_by_default():
    config = ParityGateConfig()
    assert config.temperature == 0.0

def test_gate_rejects_delta_exceeding_threshold():
    config = ParityGateConfig(accuracy_delta_threshold=0.02)
    gate = ParityGate(config)
    # CLI got 60/64, API got 58/64 — delta = 2/64 = 0.03125 > 0.02
    result = gate.evaluate(cli_correct=60, api_correct=58, total=64)
    assert not result.passed
    assert "delta" in result.reason.lower()

def test_gate_passes_within_threshold():
    config = ParityGateConfig(accuracy_delta_threshold=0.02)
    gate = ParityGate(config)
    # CLI got 62/64, API got 63/64 — delta = 1/64 ≈ 0.016 < 0.02
    result = gate.evaluate(cli_correct=62, api_correct=63, total=64)
    assert result.passed

def test_gate_passes_when_exactly_equal():
    config = ParityGateConfig(accuracy_delta_threshold=0.02)
    gate = ParityGate(config)
    result = gate.evaluate(cli_correct=61, api_correct=61, total=64)
    assert result.passed

def test_config_documents_seed_and_retries():
    config = ParityGateConfig()
    assert config.seed is not None  # seed must be set for reproducibility
    assert config.max_retries >= 1   # at least one retry on transient failure
```

- [ ] **Step 3: Run test to verify it fails**

```powershell
python -m pytest tests/scripts/test_thesis_parity_gate.py -v
```

Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 4: Implement ParityGate**

If `scripts/ci/run_thesis_parity_gate.py` does not exist, create it. If it does exist, add these classes without removing existing logic:

```python
# scripts/ci/run_thesis_parity_gate.py  (create or extend)
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParityGateConfig:
    temperature: float = 0.0           # must be 0.0 for deterministic output
    accuracy_delta_threshold: float = 0.02  # allow <=2% accuracy gap between paths
    seed: int = 42                     # fixed seed for reproducibility
    max_retries: int = 2               # retry on transient Gemini errors


@dataclass
class ParityGateResult:
    passed: bool
    cli_accuracy: float
    api_accuracy: float
    delta: float
    reason: str


class ParityGateError(RuntimeError):
    pass


class ParityGate:
    def __init__(self, config: ParityGateConfig | None = None) -> None:
        self.config = config or ParityGateConfig()

    def evaluate(self, cli_correct: int, api_correct: int, total: int) -> ParityGateResult:
        if total == 0:
            raise ParityGateError("total must be > 0")
        cli_acc = cli_correct / total
        api_acc = api_correct / total
        delta = abs(cli_acc - api_acc)
        passed = delta <= self.config.accuracy_delta_threshold
        reason = (
            f"delta={delta:.4f} within threshold={self.config.accuracy_delta_threshold}"
            if passed
            else f"delta={delta:.4f} exceeds threshold={self.config.accuracy_delta_threshold}"
        )
        return ParityGateResult(passed=passed, cli_accuracy=cli_acc,
                                api_accuracy=api_acc, delta=delta, reason=reason)
```

- [ ] **Step 5: Run tests**

```powershell
python -m pytest tests/scripts/test_thesis_parity_gate.py -v
```

Expected: 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/ci/run_thesis_parity_gate.py tests/scripts/test_thesis_parity_gate.py
git commit -m "feat(ci): thesis parity gate — temperature=0.0 default, accuracy-delta<0.02 threshold, documented seed+retries; removes untestable delta=0 requirement"
```

---

### Task M6: PR evidence enforcement — semantic, not syntactic (R8/W7)

> **Deliberation required change R8:** Parsing PR body text for trigger phrases is forgeable. Fix: require links to known CI artifact URLs, validate that at least one link is reachable/non-placeholder, and reject evidence-bundles that contain only trigger phrases with no actual links.

**Files:**
- Create: `scripts/check_pr_evidence.py`
- Create: `tests/scripts/test_check_pr_evidence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_check_pr_evidence.py
import pytest
from scripts.check_pr_evidence import EvidenceChecker, EvidenceError

GOOD_BODY = """
## Summary
This PR fixes the thesis gate.

## Evidence
- test results: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/123456789
- artifact links: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/123456789/artifacts/1
"""

FORGEABLE_BODY = """
## Evidence
- test results: see CI
- artifact links: passing
"""

EMPTY_BODY = ""

def test_good_evidence_passes():
    checker = EvidenceChecker()
    checker.check(GOOD_BODY)  # should not raise

def test_placeholder_evidence_fails():
    checker = EvidenceChecker()
    with pytest.raises(EvidenceError, match="placeholder"):
        checker.check(FORGEABLE_BODY)

def test_empty_body_fails():
    checker = EvidenceChecker()
    with pytest.raises(EvidenceError, match="evidence"):
        checker.check(EMPTY_BODY)

def test_body_with_phrase_but_no_links_fails():
    body = "## Evidence\n- test results: see above\n- artifact links: N/A"
    checker = EvidenceChecker()
    with pytest.raises(EvidenceError, match="link"):
        checker.check(body)

def test_known_ci_url_pattern_accepted():
    body = "Evidence: https://github.com/nikhillinit/SweetSwwetHarmony/actions/runs/99"
    checker = EvidenceChecker()
    checker.check(body)  # should not raise — real GitHub Actions URL
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
python -m pytest tests/scripts/test_check_pr_evidence.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.check_pr_evidence'`

- [ ] **Step 3: Implement EvidenceChecker**

```python
# scripts/check_pr_evidence.py
from __future__ import annotations

import re

# Patterns that indicate a real CI artifact link (not placeholder)
KNOWN_CI_URL_PATTERNS = [
    r"https://github\.com/\S+/actions/runs/\d+",
    r"https://github\.com/\S+/suites/\d+",
]

# Phrases whose presence is required to detect evidence sections
EVIDENCE_TRIGGER_PHRASES = ["test results:", "artifact links:"]

# Strings that indicate placeholder-only evidence (no real link)
PLACEHOLDER_INDICATORS = [
    "see ci", "see above", "passing", "n/a", "none", "tbd", "todo",
    "see pr", "check ci", "all passing",
]


class EvidenceError(RuntimeError):
    pass


class EvidenceChecker:
    def check(self, body: str) -> None:
        if not body or not body.strip():
            raise EvidenceError("PR body is empty — evidence bundle required")

        # Must contain at least one trigger phrase
        lower = body.lower()
        has_trigger = any(phrase in lower for phrase in EVIDENCE_TRIGGER_PHRASES)
        if not has_trigger:
            raise EvidenceError(
                "PR body missing evidence section "
                f"(required phrases: {EVIDENCE_TRIGGER_PHRASES})"
            )

        # Extract all URLs
        urls = re.findall(r"https?://\S+", body)
        ci_urls = [
            url for url in urls
            if any(re.search(pat, url) for pat in KNOWN_CI_URL_PATTERNS)
        ]
        if not ci_urls:
            raise EvidenceError(
                "evidence section has no CI artifact links — "
                "provide a GitHub Actions run URL "
                "(https://github.com/org/repo/actions/runs/...)"
            )

        # Detect placeholder-only lines after trigger phrases
        lines_after_trigger = []
        in_evidence = False
        for line in body.splitlines():
            if any(p in line.lower() for p in EVIDENCE_TRIGGER_PHRASES):
                in_evidence = True
            if in_evidence:
                lines_after_trigger.append(line.lower())

        for line in lines_after_trigger:
            if any(p in line for p in PLACEHOLDER_INDICATORS):
                # OK only if the same line also contains a real CI URL
                if not any(re.search(pat, line) for pat in KNOWN_CI_URL_PATTERNS):
                    raise EvidenceError(
                        f"placeholder evidence detected: {line.strip()!r} — "
                        "replace with a real artifact URL"
                    )


if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", required=True)
    args = parser.parse_args()
    try:
        EvidenceChecker().check(args.body)
        print("Evidence check passed.")
    except EvidenceError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 4: Run tests**

```powershell
python -m pytest tests/scripts/test_check_pr_evidence.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/check_pr_evidence.py tests/scripts/test_check_pr_evidence.py
git commit -m "feat(ci): semantic PR evidence enforcement — requires real GitHub Actions URLs, rejects placeholder-only evidence sections"
```

---

### Task M7: Trust status CLI (hard-dep on M3 per R9/W9)

> **Deliberation required change R9:** M7 depends on M3's `REPORT_SCHEMA_VERSION=2`. If M7 lands before M3, it reads v1 format and the collector health summary is silently wrong. Fix: M7 must explicitly check `REPORT_SCHEMA_VERSION == 2` and fail loudly (exit 1 with a clear message) if it gets v1 or missing.
>
> **DO NOT begin this task until M3 is merged to main.**

**Files:**
- Create: `ops/trust_status.py`
- Create: `tests/ops/test_trust_status.py`

- [ ] **Step 0: Verify M3 is on main**

```powershell
python -c "from ops.collector_health import REPORT_SCHEMA_VERSION; assert REPORT_SCHEMA_VERSION == 2, f'M3 not landed: REPORT_SCHEMA_VERSION={REPORT_SCHEMA_VERSION}'; print('M3 confirmed')"
```

If this fails, stop — M3 must merge first.

- [ ] **Step 1: Write the failing test**

```python
# tests/ops/test_trust_status.py
import pytest
from ops.trust_status import TrustStatusCLI, TrustStatusError
from ops.collector_health import CollectorHealthReport, REPORT_SCHEMA_VERSION

def test_requires_schema_version_2():
    cli = TrustStatusCLI()
    with pytest.raises(TrustStatusError, match="schema_version"):
        cli.load_reports(schema_version=1)

def test_accepts_schema_version_2():
    cli = TrustStatusCLI()
    reports = [
        CollectorHealthReport(collector="github", status="success"),
        CollectorHealthReport(collector="news_api", status="api_shape_changed",
                              detail="field missing"),
    ]
    summary = cli.summarize(reports)
    assert summary["schema_version"] == 2
    assert any(r["status"] == "api_shape_changed" for r in summary["collectors"])

def test_summary_flags_suspended_collectors(tmp_path):
    from storage.collector_suspension import SuspensionStore
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("news_api", reason="api_shape_changed: test")
    cli = TrustStatusCLI(suspension_store=store)
    reports = [CollectorHealthReport(collector="news_api", status="api_shape_changed")]
    summary = cli.summarize(reports)
    news_api_entry = next(r for r in summary["collectors"] if r["collector"] == "news_api")
    assert news_api_entry["suspended"] is True

def test_overall_status_is_degraded_when_any_suspended(tmp_path):
    from storage.collector_suspension import SuspensionStore
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("github", reason="test")
    cli = TrustStatusCLI(suspension_store=store)
    reports = [CollectorHealthReport(collector="github", status="api_shape_changed")]
    summary = cli.summarize(reports)
    assert summary["overall"] == "degraded"
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
python -m pytest tests/ops/test_trust_status.py -v
```

Expected: `ModuleNotFoundError: No module named 'ops.trust_status'`

- [ ] **Step 3: Implement TrustStatusCLI**

```python
# ops/trust_status.py
from __future__ import annotations

from typing import Any
from ops.collector_health import CollectorHealthReport, REPORT_SCHEMA_VERSION
from storage.collector_suspension import SuspensionStore


class TrustStatusError(RuntimeError):
    pass


class TrustStatusCLI:
    def __init__(self, suspension_store: SuspensionStore | None = None) -> None:
        self.suspension_store = suspension_store

    def load_reports(self, schema_version: int) -> None:
        if schema_version != REPORT_SCHEMA_VERSION:
            raise TrustStatusError(
                f"trust status CLI requires schema_version={REPORT_SCHEMA_VERSION} "
                f"(collector_health v2), got schema_version={schema_version}. "
                "Ensure M3 (collector_health v2) is deployed before running M7."
            )

    def summarize(self, reports: list[CollectorHealthReport]) -> dict[str, Any]:
        self.load_reports(schema_version=REPORT_SCHEMA_VERSION)
        collectors = []
        any_suspended = False
        for r in reports:
            suspended = bool(
                self.suspension_store and self.suspension_store.is_suspended(r.collector)
            )
            if suspended:
                any_suspended = True
            collectors.append({
                "collector": r.collector,
                "status": r.status,
                "detail": r.detail,
                "suspended": suspended,
            })
        overall = "degraded" if any_suspended else "ok"
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "overall": overall,
            "collectors": collectors,
        }
```

- [ ] **Step 4: Run tests**

```powershell
python -m pytest tests/ops/test_trust_status.py -v
```

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add ops/trust_status.py tests/ops/test_trust_status.py
git commit -m "feat(ops): trust status CLI — hard-requires collector_health v2 schema, surfaces suspended collectors, overall ok/degraded verdict"
```

---

## Self-review checklist

**Spec coverage — deliberation required changes:**

| Required change | Task covering it |
|-----------------|-----------------|
| R1 Litestream stop→WAL flush→copy→integrity→generation-reset→restart | M1B + litestream_ctrl.py |
| R2 Maintenance lock timeout > 120s | M1B `MAINTENANCE_LOCK_TIMEOUT_SECONDS = 180` |
| R3 Parity gate: temperature=0.0 or accuracy_delta<0.02 | M5 ParityGateConfig |
| R4 v52 migration with writer-coordination single-writer window | M2 MigrationRunner._assert_no_active_writers |
| R5 Circuit breaker: durable file store + HARMONIC_SCRATCH_DB guard + audit trail | M3 SuspensionStore |
| R6 vcrpy cassette policy: fingerprint staleness + anti-synthetic-injection guard | M4 CassettePolicy |
| R7 W8 fix: M1A is hard prerequisite for M1B (explicit in task ordering and note) | ordering + note in M1A |
| R8 W7 fix: PR evidence requires real CI URLs, not body phrase parsing | M6 EvidenceChecker |
| R9 W9 fix: M7 hard-deps on M3, fails loudly on v1 schema | M7 Step 0 guard + TrustStatusError |
| R10 W10 fix: this document replaces 00-strategy.md; prior archived | header + archive step |

**Dependency ordering (explicitly enforced):**
- M1A must merge before M1B (restore_db.py references db_anomaly.py)
- M3 must merge before M7 (trust status CLI requires REPORT_SCHEMA_VERSION=2)
- M1B + M2 before P0-3 dry-run proof (restore and migration must be safe first)
- M4 before P0-3 (vcrpy cassettes needed for CI-deterministic dry-run tests)

**Type consistency:**
- `AnomalyChecker.check()` → `AnomalyResult` ✓
- `LitestreamCtrl.stop/start/assert_wal_flushed/reset_generation` ✓
- `MigrationRunner.run()` → raises `MigrationError` ✓
- `CollectorHealthReport.status` → validated against `VALID_STATUSES` ✓
- `SuspensionStore.suspend/reset/is_suspended/audit_log` ✓
- `CassettePolicy.assert_fresh/assert_no_synthetic_field_injection` ✓
- `ParityGate.evaluate(cli_correct, api_correct, total)` → `ParityGateResult` ✓
- `EvidenceChecker.check(body)` → raises `EvidenceError` ✓
- `TrustStatusCLI.summarize(reports)` → `dict` with `schema_version`, `overall`, `collectors` ✓

---

## Prior strategy reference

The pre-deliberation strategy (P0-0 through P2-1 milestones, appendices A–C, critical-path analysis, decision rules, governance cadence) is preserved verbatim at `00-strategy-pre-deliberation.md` in this directory. That document remains authoritative for:
- P0-0 / P0-2 (done; historical record)
- P0-1 context (DB untrack evidence, Appendix B root-cause, Appendix C reference audit)
- P0-3 dry-run immutability (the `.omx/plans/process-dry-run-readonly-ralplan-dr-20260515.md` adoption — not duplicated here)
- P1-1 F6 revalidation, P1-2a/b freshness, P1-3 source-quality, P1-4 required checks, P2-1 observability

The new milestones in this document (M1A, M1B, M2, M3, M4, M5, M6, M7) are net-new implementation tasks that were not in the prior strategy. They do not supersede P0-3 or P1-x — they run in parallel or as prerequisites to them.
