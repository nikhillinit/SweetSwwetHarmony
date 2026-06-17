"""ops/bootstrap.py -- Run basic environment checks and initialize the SQLite database.

Usage: python -m ops.bootstrap --db signals.db

Intentionally dependency-light and Windows-friendly.
"""

import argparse
import os
import sys
from pathlib import Path


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        return


def _check_python_version(min_major: int = 3, min_minor: int = 11) -> None:
    if sys.version_info < (min_major, min_minor):
        raise RuntimeError(
            f"Python {min_major}.{min_minor}+ required (found {sys.version.split()[0]})"
        )


def _ensure_dirs() -> None:
    for p in [
        Path("ops/artifacts"),
        Path("ops/artifacts/maintenance"),
        Path("ops/memory"),
        Path("ops/trends"),
    ]:
        p.mkdir(parents=True, exist_ok=True)


def _check_sqlite_fts5() -> None:
    import sqlite3

    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    except sqlite3.OperationalError as e:
        if "fts5" in str(e).lower() or "no such module" in str(e).lower():
            raise RuntimeError(
                "SQLite FTS5 required but not available. Use Python.org 3.11+ builds."
            ) from e
        raise
    finally:
        con.close()


def _check_windows_long_paths() -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        )
        value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        if value != 1:
            print("[BOOTSTRAP] WARNING: Windows long path support disabled.")
    except (ImportError, OSError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Ops bootstrap (Windows-first)")
    ap.add_argument("--db", default=None, help="SQLite database path")
    args = ap.parse_args()

    _load_dotenv_if_available()

    print("[BOOTSTRAP] Checking environment...")

    _check_python_version()
    print(f"[BOOTSTRAP] Python {sys.version.split()[0]}: OK")

    _check_windows_long_paths()
    _ensure_dirs()
    print("[BOOTSTRAP] Directories: OK")

    _check_sqlite_fts5()
    import sqlite3
    print(f"[BOOTSTRAP] SQLite {sqlite3.sqlite_version} with FTS5: OK")

    # Check API key
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        print("[BOOTSTRAP] LLM API key: OK")
    else:
        print("[BOOTSTRAP] WARNING: No GEMINI_API_KEY or GOOGLE_API_KEY set (extraction disabled)")

    # Initialize database
    try:
        from ops.storage import OpsStorage

        storage = OpsStorage(args.db)
        with storage.pool.get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            print(f"[BOOTSTRAP] Journal mode: {mode}")

            # Verify ops tables exist
            ops_tables = [
                "user_actions", "memory_facts", "memory_facts_fts",
                "memory_action_state", "extraction_runs",
                "audit_log", "system_health", "fact_citations",
            ]
            missing = []
            for table in ops_tables:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                    (table,),
                ).fetchone()
                if not exists:
                    missing.append(table)

            if missing:
                print(f"[BOOTSTRAP] WARNING: Missing tables: {', '.join(missing)}")
            else:
                print(f"[BOOTSTRAP] All {len(ops_tables)} ops tables verified")

    except Exception as e:
        print(f"[BOOTSTRAP] DB init failed: {e}", file=sys.stderr)
        return 1

    print(f"[BOOTSTRAP] OK: DB ready at {storage.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
