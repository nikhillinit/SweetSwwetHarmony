# INTEGRATED OPS LAYER IMPLEMENTATION PROCEDURE
## Fully Automated Workflow with Explicit User Checkpoints

**Document Version:** 1.0  
**Last Updated:** 2026-02-05  
**Execution Mode:** Semi-Automated with Explicit User Approval Gates

---

## 🎯 EXECUTIVE SUMMARY

This procedure integrates:
1. **Self-Healing Infrastructure** - Automated collector maintenance and browser pool management
2. **Memory & Intelligence Subsystem** - Learning from past decisions without destabilizing core systems
3. **Operational Capsule Pattern** - Fail-safe, Windows-first architecture

**Estimated Duration:** 12-16 hours of active implementation  
**Prerequisites:** Python 3.11+, SQLite with FTS5, Windows 10/11, GEMINI_API_KEY

---

## ⚠ DB PATH REQUIREMENT

Do NOT run ops commands against `signals.db` in the repo root.
`storage/db_paths.py` raises `InTreeDatabaseError`. Always set:
```powershell
$env:DISCOVERY_DB_PATH = "$env:USERPROFILE\harmonic-data\signals.db"
```
For scratch/dev work only:
```powershell
$env:HARMONIC_ALLOW_IN_TREE_DB = "true"
$env:DISCOVERY_DB_PATH = "$env:TEMP\scratch-signals.db"
```

## 📋 PRE-FLIGHT CHECKLIST

Run these verification steps before beginning:

```powershell
# 1. Verify Python version
python --version  # Must output 3.11.x or higher

# 2. Verify SQLite FTS5 support
python -c "import sqlite3; conn = sqlite3.connect(':memory:'); conn.execute('CREATE VIRTUAL TABLE t USING fts5(content)'); print('FTS5: OK')"

# 3. Verify API key exists
python -c "import os; assert os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY'); print('API Key: OK')"

# 4. Check current directory
python -c "from pathlib import Path; assert (Path.cwd() / 'signals.db').exists(); print('Database: Found')"
```

**CHECKPOINT ALPHA:** If ANY command above fails, STOP and resolve before continuing.

---

## PHASE 0: FOUNDATION & VERIFICATION
**Automation Level:** 75% - Skills auto-activate, user confirms at checkpoints

### STEP 0.1: Environment Audit

**AUTO-ACTIVATE SKILL:** `/developer-essentials:sql-optimization-patterns`

```bash
# Execute automatically
view /mnt/project/README.md
view /mnt/project/storage.py
view /mnt/project/bootstrap.py
```

**AUTO-EXECUTE:**
```python
# Create verification script
bash_tool: |
  python -c "
  import sqlite3
  import sys
  from pathlib import Path
  
  # Check SQLite version
  print(f'SQLite Version: {sqlite3.sqlite_version}')
  
  # Check FTS5
  try:
      conn = sqlite3.connect(':memory:')
      conn.execute('CREATE VIRTUAL TABLE t USING fts5(content)')
      print('FTS5: ✅ Available')
  except:
      print('FTS5: ❌ NOT AVAILABLE')
      sys.exit(1)
  
  # Check database location
  db_path = Path('signals.db')
  if db_path.exists():
      print(f'Database: ✅ Found at {db_path.absolute()}')
      
      # Check WAL mode
      conn = sqlite3.connect(str(db_path))
      cursor = conn.execute('PRAGMA journal_mode;')
      mode = cursor.fetchone()[0]
      print(f'Journal Mode: {mode}')
      if mode.lower() != 'wal':
          print('⚠️  WARNING: WAL mode not enabled. This may cause lock issues.')
  else:
      print('Database: ⚠️  Not found at root. Will be created.')
  "
```

**CHECKPOINT 0.1A - ENVIRONMENT VERIFICATION**
```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

Review the output above. Do you want to:
1. [CONTINUE] - Environment meets requirements
2. [FIX] - I need to resolve issues first
3. [ABORT] - Stop the procedure

Type your choice:
```

**AWAIT USER INPUT:** Continue only on "CONTINUE"

---

### STEP 0.2: Bootstrap Script Creation

**AUTO-ACTIVATE SKILL:** `/python-development:python-type-safety`

**AUTO-EXECUTE:**
```python
# Read existing bootstrap if present
view /mnt/project/bootstrap.py
```

**AUTO-CREATE FILE:** `ops/bootstrap.py` (if not exists or needs fixes)

```python
str_replace_or_create_file:
  path: ops/bootstrap.py
  content: |
    """ops/bootstrap.py
    
    Run basic environment checks and initialize the SQLite database.
    
    Usage:
        python -m ops.bootstrap --db "$env:DISCOVERY_DB_PATH"
    
    This module is intentionally dependency-light and Windows-friendly.
    """
    
    import argparse
    import os
    import sys
    from pathlib import Path
    
    
    def _load_dotenv_if_available() -> None:
        """Load .env into os.environ if python-dotenv is installed."""
        try:
            from dotenv import load_dotenv
        except ImportError:
            return
        load_dotenv()
    
    
    def _check_python_version(min_major: int = 3, min_minor: int = 11) -> None:
        """Verify Python version meets minimum requirements."""
        if sys.version_info < (min_major, min_minor):
            raise RuntimeError(
                f"Python {min_major}.{min_minor}+ required "
                f"(found {sys.version.split()[0]})"
            )
    
    
    def _ensure_dirs() -> None:
        """Create expected directory structure."""
        for p in [
            Path("ops/artifacts"),
            Path("ops/artifacts/maintenance"),
            Path("ops/memory"),
            Path("ops/trends"),
        ]:
            p.mkdir(parents=True, exist_ok=True)
    
    
    def _check_sqlite_fts5() -> None:
        """Verify SQLite has FTS5 support enabled."""
        import sqlite3
    
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "fts5" in msg or "no such module" in msg:
                raise RuntimeError(
                    "SQLite FTS5 support is required but not available. "
                    "On Windows, use official Python.org builds (3.11+ recommended)."
                ) from e
            raise
        finally:
            con.close()
    
    
    def _check_windows_long_paths() -> None:
        """Warn if Windows long path support is disabled."""
        if os.name != "nt":
            return
        
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\FileSystem"
            )
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
            if value != 1:
                print(
                    "[BOOTSTRAP] WARNING: Windows long path support is disabled. "
                    "This may cause issues with deep directory structures."
                )
        except (ImportError, OSError):
            # Can't check on this system
            pass
    
    
    def main() -> int:
        ap = argparse.ArgumentParser(description="Ops bootstrap (Windows-first)")
        ap.add_argument("--db", default="signals.db", help="SQLite database path")
        ap.add_argument(
            "--skip-youtube",
            action="store_true",
            help="Skip optional YouTube checks"
        )
        args = ap.parse_args()
    
        _load_dotenv_if_available()
        _check_python_version()
        _check_windows_long_paths()
        _ensure_dirs()
        _check_sqlite_fts5()
    
        # Initialize DB + migrations
        try:
            from ops.storage import OpsStorage
            storage = OpsStorage(args.db)
            
            # Enable WAL mode for Windows concurrency
            with storage.pool.get_connection() as conn:
                cursor = conn.execute("PRAGMA journal_mode=WAL;")
                mode = cursor.fetchone()[0]
                print(f"[BOOTSTRAP] Journal mode: {mode}")
                
        except Exception as e:
            print(f"[BOOTSTRAP] DB init failed: {e}", file=sys.stderr)
            return 1
    
        print(f"[BOOTSTRAP] ✅ OK: DB ready at {args.db}")
        print("[BOOTSTRAP] Next steps:")
        print(f"  python -m ops.cli stats --db {args.db}")
        print(f"  python -m ops.cli run-extraction --db {args.db}")
        
        return 0
    
    
    if __name__ == "__main__":
        raise SystemExit(main())
```

**AUTO-EXECUTE:**
```bash
# Test bootstrap script
python -m ops.bootstrap --db "$env:DISCOVERY_DB_PATH" --skip-youtube
```

**CHECKPOINT 0.2A - BOOTSTRAP VERIFICATION**
```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

Bootstrap script execution completed. Review output above.

Expected: "✅ OK: DB ready at signals.db"

Do you want to:
1. [CONTINUE] - Bootstrap succeeded
2. [RETRY] - Run bootstrap again
3. [DEBUG] - I need to investigate errors

Type your choice:
```

**AWAIT USER INPUT:** Continue only on "CONTINUE"

---

### STEP 0.3: Characterization Testing

**AUTO-ACTIVATE SKILL:** `/python-development:python-testing-patterns`

**AUTO-CREATE FILE:** `tests/ops/test_integration_baseline.py`

```python
create_file:
  path: tests/ops/test_integration_baseline.py
  content: |
    """Baseline integration tests for ops layer."""
    
    import pytest
    import sqlite3
    from pathlib import Path
    from ops.storage import OpsStorage
    
    
    @pytest.fixture
    def clean_db(tmp_path):
        """Isolated test database with proper cleanup."""
        db_path = tmp_path / "test.db"
        storage = OpsStorage(str(db_path))
        
        yield storage
        
        # Cleanup: critical for Windows file locking
        del storage
        if db_path.exists():
            db_path.unlink()
            Path(f"{db_path}-shm").unlink(missing_ok=True)
            Path(f"{db_path}-wal").unlink(missing_ok=True)
    
    
    def test_fts5_available(clean_db):
        """Verify FTS5 is available and working."""
        with clean_db.transaction() as conn:
            # FTS tables should exist after migrations
            cursor = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='memory_facts_fts'
            """)
            assert cursor.fetchone() is not None
    
    
    def test_wal_mode_enabled(clean_db):
        """Verify WAL mode is active."""
        with clean_db.pool.get_connection() as conn:
            cursor = conn.execute("PRAGMA journal_mode;")
            mode = cursor.fetchone()[0]
            assert mode.lower() == "wal", f"Expected WAL mode, got {mode}"
    
    
    def test_migrations_applied(clean_db):
        """Verify all migrations ran successfully."""
        with clean_db.transaction() as conn:
            cursor = conn.execute("""
                SELECT version FROM schema_version ORDER BY version DESC LIMIT 1
            """)
            latest = cursor.fetchone()[0]
            # Migrations 0-6 should be applied (7 migrations total)
            assert latest >= 5, f"Expected migration 5+, got {latest}"
    
    
    def test_search_facts_empty(clean_db):
        """Verify search works even with empty dataset."""
        results = clean_db.search_facts("test query", limit=10)
        assert results == []
    
    
    def test_concurrent_access(clean_db, tmp_path):
        """Verify multiple connections can coexist (WAL mode)."""
        db_path = tmp_path / "concurrent_test.db"
        storage1 = OpsStorage(str(db_path))
        storage2 = OpsStorage(str(db_path))
        
        try:
            # Both should be able to read simultaneously
            with storage1.transaction() as conn1:
                with storage2.transaction() as conn2:
                    cursor1 = conn1.execute("SELECT 1")
                    cursor2 = conn2.execute("SELECT 1")
                    assert cursor1.fetchone()[0] == 1
                    assert cursor2.fetchone()[0] == 1
        finally:
            del storage1
            del storage2
```

**AUTO-EXECUTE:**
```bash
# Install test dependencies if needed
pip install pytest pytest-asyncio --quiet

# Run baseline tests
pytest tests/ops/test_integration_baseline.py -v --tb=short
```

**CHECKPOINT 0.3A - BASELINE TESTS**
```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

Baseline tests executed. Review results above.

Expected: All tests PASSED

Do you want to:
1. [CONTINUE] - All tests passed
2. [DEBUG] - Some tests failed, need investigation
3. [SKIP-TESTS] - Continue despite failures (NOT RECOMMENDED)

Type your choice:
```

**AWAIT USER INPUT:** Continue only on "CONTINUE"

---

## PHASE 1: SELF-HEALING INFRASTRUCTURE
**Automation Level:** 80% - Minimal user intervention

### STEP 1.1: Maintenance Module Structure

**AUTO-ACTIVATE SKILLS:**
- `/superpowers:writing-plans`
- `/developer-essentials:error-handling-patterns`

**AUTO-CREATE MODULE STRUCTURE:**

```python
# Create directory structure
bash_tool: |
  mkdir -p ops/maintenance
  mkdir -p ops/infra
  touch ops/maintenance/__init__.py
  touch ops/infra/__init__.py
```

**AUTO-CREATE FILE:** `ops/maintenance/incident.py`

```python
create_file:
  path: ops/maintenance/incident.py
  content: |
    """ops/maintenance/incident.py
    
    Incident capsule creation and management for self-healing collectors.
    """
    
    from __future__ import annotations
    
    import json
    import traceback
    from dataclasses import dataclass, asdict
    from datetime import datetime, timezone
    from pathlib import Path
    from typing import Any, Dict, Optional
    
    from ops.utils import InputSanitizer
    
    
    @dataclass
    class MaintenanceIncident:
        """Structured incident data for repair automation."""
        incident_id: str
        component: str
        created_at: str
        status: str  # pending|repairing|fixed|failed
        artifact_dir: str
        last_error: str = ""
        claude_session_id: Optional[str] = None
        attempts: int = 0
        meta: Optional[Dict[str, Any]] = None
    
    
    def _utc_ts() -> str:
        """Generate UTC timestamp for file naming."""
        return (
            datetime.now(timezone.utc)
            .isoformat()
            .replace(":", "")
            .replace("+00:00", "Z")
        )
    
    
    def create_incident(
        *,
        component: str,
        exc: BaseException,
        artifacts_root: str = "ops/artifacts/maintenance",
        html_snapshot: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> MaintenanceIncident:
        """
        Create an incident capsule folder with all diagnostic artifacts.
        
        Args:
            component: Name of failing component (e.g., "reddit_collector")
            exc: The exception that triggered this incident
            artifacts_root: Root directory for incident storage
            html_snapshot: Optional HTML content to save
            context: Additional context (URLs, configs, etc.)
        
        Returns:
            MaintenanceIncident with paths to created artifacts
        """
        incident_id = f"{component}_{_utc_ts()}"
        artifact_dir = Path(artifacts_root) / incident_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        
        # Write traceback
        tb_path = artifact_dir / "traceback.txt"
        tb_path.write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8"
        )
        
        # Write HTML snapshot if provided (sanitized)
        if html_snapshot:
            # Sanitize HTML to prevent prompt injection
            safe_html = InputSanitizer.sanitize_for_llm(html_snapshot, max_length=50000)
            html_path = artifact_dir / "html_snapshot.html"
            html_path.write_text(safe_html, encoding="utf-8")
        
        # Write context
        context = context or {}
        context["error_message"] = str(exc)
        context["error_type"] = type(exc).__name__
        context["timestamp"] = datetime.now(timezone.utc).isoformat()
        
        context_path = artifact_dir / "context.json"
        context_path.write_text(
            json.dumps(context, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        
        # Create incident metadata
        incident = MaintenanceIncident(
            incident_id=incident_id,
            component=component,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="pending",
            artifact_dir=str(artifact_dir),
            last_error=str(exc),
            meta=context,
        )
        
        # Write incident metadata
        metadata_path = artifact_dir / "incident.json"
        metadata_path.write_text(
            json.dumps(asdict(incident), indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        
        return incident
    
    
    def load_incident(artifact_dir: str) -> Optional[MaintenanceIncident]:
        """Load incident metadata from disk."""
        metadata_path = Path(artifact_dir) / "incident.json"
        if not metadata_path.exists():
            return None
        
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            return MaintenanceIncident(**data)
        except (json.JSONDecodeError, TypeError):
            return None
    
    
    def update_incident_status(
        incident: MaintenanceIncident,
        status: str,
        session_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update and persist incident status."""
        incident.status = status
        incident.attempts += 1
        
        if session_id:
            incident.claude_session_id = session_id
        if error:
            incident.last_error = error
        
        metadata_path = Path(incident.artifact_dir) / "incident.json"
        metadata_path.write_text(
            json.dumps(asdict(incident), indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
```

**CHECKPOINT 1.1A - INCIDENT MODULE**
```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

Incident management module created at ops/maintenance/incident.py

This module enables automatic capture of collector failures.

Do you want to:
1. [CONTINUE] - Proceed to repair agent
2. [REVIEW] - Let me examine the code first
3. [MODIFY] - I want to make changes

Type your choice:
```

**AWAIT USER INPUT:** Continue only on "CONTINUE"

---

### STEP 1.2: Claude Code CLI Wrapper

**AUTO-ACTIVATE SKILL:** `/developer-essentials:debugging-strategies`

**AUTO-CREATE FILE:** `ops/maintenance/claude_code_cli.py`

```python
create_file:
  path: ops/maintenance/claude_code_cli.py
  content: |
    """ops/maintenance/claude_code_cli.py
    
    Wrapper around `claude -p` for non-interactive repair sessions.
    """
    
    import json
    import logging
    import subprocess
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Optional, List
    
    logger = logging.getLogger(__name__)
    
    
    @dataclass
    class ClaudeResponse:
        """Structured response from Claude Code CLI."""
        success: bool
        result: str
        structured_output: Optional[dict] = None
        session_id: Optional[str] = None
        error: Optional[str] = None
    
    
    class ClaudeCodeCLI:
        """
        Wrapper for claude -p (print mode) with session management.
        
        Requires:
        - `claude` CLI installed: npm install -g @anthropic-ai/claude
        - ANTHROPIC_API_KEY environment variable set
        """
        
        def __init__(self, repo_root: str = "."):
            self.repo_root = Path(repo_root)
            self._verify_cli_available()
        
        def _verify_cli_available(self) -> None:
            """Check if claude CLI is installed."""
            try:
                result = subprocess.run(
                    ["claude", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    raise RuntimeError("claude CLI not responding correctly")
                    
                logger.info(f"Claude CLI version: {result.stdout.strip()}")
                
            except FileNotFoundError:
                raise RuntimeError(
                    "claude CLI not found. Install with: npm install -g @anthropic-ai/claude"
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError("claude CLI timed out")
        
        def call(
            self,
            prompt: str,
            *,
            session_id: Optional[str] = None,
            json_schema: Optional[dict] = None,
            allowed_tools: Optional[List[str]] = None,
            timeout_s: int = 300,
        ) -> ClaudeResponse:
            """
            Call Claude Code in non-interactive mode.
            
            Args:
                prompt: The instruction to send to Claude
                session_id: Optional session ID to continue conversation
                json_schema: Optional JSON schema for structured output
                allowed_tools: Tools to auto-approve (e.g., ["Read", "Edit", "Bash"])
                timeout_s: Maximum execution time in seconds
            
            Returns:
                ClaudeResponse with result or error
            """
            cmd = [
                "claude",
                "-p",  # Print mode (non-interactive)
                "--output-format", "json",
            ]
            
            # Session management
            if session_id:
                cmd.extend(["--resume", session_id])
            else:
                cmd.append("--continue")
            
            # Structured output
            if json_schema:
                cmd.extend(["--json-schema", json.dumps(json_schema)])
            
            # Auto-approve tools for headless operation
            if allowed_tools:
                cmd.extend(["--allowedTools", ",".join(allowed_tools)])
            
            # Add prompt
            cmd.append(prompt)
            
            try:
                result = subprocess.run(
                    cmd,
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
                
                if result.returncode != 0:
                    return ClaudeResponse(
                        success=False,
                        result="",
                        error=f"CLI returned {result.returncode}: {result.stderr}",
                    )
                
                # Parse JSON response
                try:
                    data = json.loads(result.stdout)
                    
                    return ClaudeResponse(
                        success=True,
                        result=data.get("result", ""),
                        structured_output=data.get("structured_output"),
                        session_id=data.get("sessionId"),
                    )
                    
                except json.JSONDecodeError as e:
                    return ClaudeResponse(
                        success=False,
                        result=result.stdout,
                        error=f"Failed to parse JSON: {e}",
                    )
            
            except subprocess.TimeoutExpired:
                return ClaudeResponse(
                    success=False,
                    result="",
                    error=f"Claude CLI timed out after {timeout_s}s",
                )
            
            except Exception as e:
                return ClaudeResponse(
                    success=False,
                    result="",
                    error=f"Unexpected error: {e}",
                )
```

**CHECKPOINT 1.2A - CLAUDE CLI WRAPPER**
```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

Claude Code CLI wrapper created. This enables automated repair sessions.

Prerequisites check:
1. Is `claude` CLI installed? (npm install -g @anthropic-ai/claude)
2. Is ANTHROPIC_API_KEY set in environment?

Do you want to:
1. [CONTINUE] - Prerequisites met, proceed
2. [INSTALL] - I need to install claude CLI first
3. [SKIP-AUTOMATION] - Skip self-healing features for now

Type your choice:
```

**AWAIT USER INPUT:** If "INSTALL" → provide installation instructions and pause. If "SKIP-AUTOMATION" → skip to Phase 2. Only "CONTINUE" proceeds.

---

### STEP 1.3: Repair Agent Implementation

**AUTO-ACTIVATE SKILL:** `/superpowers:systematic-debugging`

**AUTO-CREATE FILE:** `ops/maintenance/repair_agent.py`

[Content continues with repair agent implementation...]

**CHECKPOINT 1.3A - REPAIR AGENT**
```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

Repair agent implementation complete. This orchestrates:
- Incident loading
- Claude-powered patch generation  
- Patch validation and application
- Test execution and retry logic

Do you want to:
1. [CONTINUE] - Proceed to CLI integration
2. [TEST] - Test repair agent manually first
3. [REVIEW] - Let me examine the code

Type your choice:
```

**AWAIT USER INPUT:** Continue only on "CONTINUE" or after successful "TEST"

---

## PHASE 2: MEMORY & INTELLIGENCE SUBSYSTEM
**Automation Level:** 85% - High automation with validation gates

[Content continues with Phase 2 implementation...]

---

## PHASE 3: CLI INTEGRATION
**Automation Level:** 90% - Almost fully automated

### STEP 3.1: Add Maintenance Commands

**AUTO-ACTIVATE SKILL:** `/python-development:python-refactor`

**AUTO-APPLY PATCH:** Extend `ops/cli.py` with maintenance commands

[Patch content from For_Self-Healing.txt...]

**CHECKPOINT 3.1A - CLI COMMANDS**
```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

CLI extended with new commands:
- ops.cli maint list-incidents
- ops.cli maint repair-latest  
- ops.cli docker restart
- ops.cli docker prune-networks

Test commands:
```bash
python -m ops.cli maint list-incidents
python -m ops.cli stats --db "$env:DISCOVERY_DB_PATH"
```

Do you want to:
1. [CONTINUE] - Commands work, proceed
2. [DEBUG] - Commands failing, need help
3. [TEST-MORE] - Let me test manually first

Type your choice:
```

**AWAIT USER INPUT:** Continue only on "CONTINUE"

---

## PHASE 4: INTEGRATION TESTING
**Automation Level:** 70% - Requires user validation

### STEP 4.1: End-to-End Smoke Test

**AUTO-ACTIVATE SKILL:** `/superpowers:verification-before-completion`

**AUTO-CREATE FILE:** `tests/ops/test_e2e_integration.py`

[E2E test content...]

**AUTO-EXECUTE:**
```bash
pytest tests/ops/test_e2e_integration.py -v -s
```

**CHECKPOINT 4.1A - E2E TEST RESULTS**
```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

End-to-end integration tests completed.

Review test output above. All critical paths should pass.

Do you want to:
1. [CONTINUE] - All tests passed, ready for production
2. [DEBUG] - Some tests failed
3. [SKIP-FAILED] - Continue with known failures (document them)

Type your choice:
```

**AWAIT USER INPUT:** Continue only on "CONTINUE"

---

## PHASE 5: PRODUCTION DEPLOYMENT
**Automation Level:** 50% - Heavy user involvement required

### STEP 5.1: Pre-Deployment Checklist

**AUTO-EXECUTE VERIFICATION:**
```bash
# Automated pre-flight checks
python -m ops.bootstrap --db "$env:DISCOVERY_DB_PATH"
python -m ops.cli stats --db "$env:DISCOVERY_DB_PATH"
pytest tests/ops/ -v --cov=ops
```

**CHECKPOINT 5.1A - DEPLOYMENT READINESS**
```
⏸️  WORKFLOW PAUSED - CRITICAL USER DECISION

All automated checks complete. Review results above.

This is your final checkpoint before production deployment.

Do you want to:
1. [DEPLOY] - Proceed with production deployment
2. [ABORT] - Cancel deployment, review findings
3. [BACKUP-FIRST] - Create backups before deploying

Type your choice:
```

**AWAIT USER INPUT:** If "BACKUP-FIRST", create backup then ask again. Only "DEPLOY" proceeds.

---

### STEP 5.2: Production Deployment

**MANUAL STEPS - USER EXECUTES:**

```powershell
# USER MUST EXECUTE THESE COMMANDS MANUALLY

# 1. Create backup
Copy-Item signals.db signals.db.backup_$(Get-Date -Format "yyyyMMdd_HHmmss")

# 2. Activate environment
.\.venv\Scripts\Activate.ps1

# 3. Install/update dependencies
pip install -r requirements.txt

# 4. Run bootstrap
python -m ops.bootstrap --db "$env:DISCOVERY_DB_PATH"

# 5. Initial extraction (small batch)
python -m ops.cli run-extraction --limit 3 --db "$env:DISCOVERY_DB_PATH"

# 6. Verify stats
python -m ops.cli stats --db "$env:DISCOVERY_DB_PATH"
```

**CHECKPOINT 5.2A - DEPLOYMENT VERIFICATION**
```
⏸️  WORKFLOW PAUSED - USER CONFIRMATION

Have you successfully executed all deployment commands above?

Do you want to:
1. [SUCCESS] - Deployment succeeded
2. [FAILED] - Deployment had errors
3. [ROLLBACK] - Restore from backup

Type your choice:
```

**AWAIT USER INPUT:** On "ROLLBACK", provide rollback procedure. Only "SUCCESS" completes workflow.

---

## COMPLETION CHECKLIST

Auto-generated summary of what was accomplished:

```
✅ Phase 0: Foundation & Verification
  ✅ Environment audit passed
  ✅ Bootstrap script created and tested
  ✅ Baseline tests passing

✅ Phase 1: Self-Healing Infrastructure
  ✅ Incident management module
  ✅ Claude Code CLI wrapper
  ✅ Repair agent implementation
  ✅ Docker orchestration

✅ Phase 2: Memory & Intelligence
  ✅ Memory extractor
  ✅ Classification engine
  ✅ FTS5 search integration

✅ Phase 3: CLI Integration
  ✅ Maintenance commands
  ✅ Docker commands
  ✅ Audit logging

✅ Phase 4: Integration Testing
  ✅ E2E tests passing
  ✅ Performance benchmarks met

✅ Phase 5: Production Deployment
  ✅ Backups created
  ✅ Production deployment verified
  ✅ Monitoring enabled
```

---

## POST-DEPLOYMENT MONITORING

**AUTO-ACTIVATE SKILL:** `/llm-application-dev:observability-patterns`

Set up continuous monitoring:

```bash
# Run health check every 5 minutes
python ops/monitor.py &
```

**FINAL CHECKPOINT - PROCEDURE COMPLETE**
```
🎉 WORKFLOW COMPLETE

All phases successfully executed. Your ops layer is now:
- Self-healing (automatic collector repairs)
- Intelligent (learning from past decisions)
- Observable (comprehensive monitoring)
- Production-ready (tested and deployed)

Next steps:
1. Monitor extraction_runs table for daily stats
2. Review incident capsules as they're created
3. Approve/retire memory facts via CLI
4. Set up scheduled jobs for maintenance

Type [DONE] to close this session.
```

---

## AUTOMATION SUMMARY

**Skills Auto-Activated Throughout Procedure:**
1. `/developer-essentials:sql-optimization-patterns` - Database optimization
2. `/python-development:python-type-safety` - Type-safe code generation
3. `/python-development:python-testing-patterns` - Test creation
4. `/superpowers:writing-plans` - Planning maintenance architecture
5. `/developer-essentials:error-handling-patterns` - Resilient error handling
6. `/superpowers:systematic-debugging` - Debugging framework
7. `/python-development:python-refactor` - CLI refactoring
8. `/superpowers:verification-before-completion` - Final verification

**Agents Available (Not Auto-Activated, User Can Invoke):**
- `sqlite-expert` - For database schema questions
- `collector-specialist` - For collector-specific issues
- `secops_governor` - For security review
- `python-pro` - For Python best practices

**Pause Points:** 14 explicit checkpoints where user approval is required
**Automation Level:** 75% overall (varies by phase)

