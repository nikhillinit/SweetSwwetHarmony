# EXECUTION GUIDE: Ops Layer Integration
## How to Run the Automated Procedure

---

## 📖 OVERVIEW

This guide shows you **exactly** how to execute the integrated ops layer procedure with minimal manual intervention.

**Total Estimated Time:** 12-16 hours active implementation  
**Checkpoints:** 14 pause points requiring your approval  
**Automation Level:** 75% automated (code generation, testing, validation)

---

## 🚀 QUICK START

### Step 1: Open the Procedure
Open `INTEGRATED_OPS_LAYER_PROCEDURE.md` in your preferred editor or markdown viewer.

### Step 2: Start with Pre-Flight Checklist
Execute these commands in PowerShell:

```powershell
# Navigate to your project root
cd C:\dev\Harmonic  # Adjust to your path

# Run pre-flight checks
python --version
python -c "import sqlite3; conn = sqlite3.connect(':memory:'); conn.execute('CREATE VIRTUAL TABLE t USING fts5(content)'); print('FTS5: OK')"
python -c "import os; assert os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY'); print('API Key: OK')"
```

**If ANY command fails:** Stop and resolve before continuing.

### Step 3: Begin Phase 0

The procedure document will guide you through each phase. At each checkpoint:

1. **Review the automated output** (code generation, test results, etc.)
2. **Make your decision** at the pause point
3. **Type your choice** to continue

---

## 🎯 CHECKPOINT PATTERN

Every checkpoint follows this pattern:

```
⏸️  WORKFLOW PAUSED - USER ACTION REQUIRED

[Description of what was just completed]

Do you want to:
1. [OPTION 1] - Description
2. [OPTION 2] - Description  
3. [OPTION 3] - Description

Type your choice:
```

**You must actively type your choice** (e.g., "CONTINUE", "DEBUG", etc.) to proceed.

---

## 🔄 HOW AUTOMATION WORKS

### What Happens Automatically

When you approve a step, the procedure will:

1. **Auto-activate relevant skills**
   - Example: `/python-development:python-testing-patterns` for test creation
   - You'll see: `AUTO-ACTIVATE SKILL: /skill-name`

2. **Auto-execute code generation**
   - Files are created/modified based on templates
   - You'll see: `AUTO-CREATE FILE: path/to/file.py`

3. **Auto-run verification**
   - Tests execute automatically
   - Results displayed for your review
   - You'll see: `AUTO-EXECUTE: command`

### What Requires Your Action

You must actively participate when you see:

1. **⏸️  WORKFLOW PAUSED**
   - Always requires your input
   - Type one of the provided options

2. **CHECKPOINT [ID]**
   - Critical decision point
   - Review output before proceeding

3. **MANUAL STEPS - USER EXECUTES**
   - You must copy and execute commands yourself
   - Common in deployment phase

---

## 📋 PHASE-BY-PHASE BREAKDOWN

### Phase 0: Foundation (Est. 2 hours)
**Automation:** 75%  
**Checkpoints:** 3  
**Your Role:** Verify environment, approve bootstrap

**Key Decision Points:**
- CHECKPOINT 0.1A: Approve environment verification
- CHECKPOINT 0.2A: Confirm bootstrap succeeded
- CHECKPOINT 0.3A: Validate baseline tests passed

### Phase 1: Self-Healing (Est. 4 hours)
**Automation:** 80%  
**Checkpoints:** 3  
**Your Role:** Review incident module, decide on Claude CLI integration

**Key Decision Points:**
- CHECKPOINT 1.1A: Approve incident module
- CHECKPOINT 1.2A: Confirm Claude CLI prerequisites
- CHECKPOINT 1.3A: Proceed to CLI integration

**Special Note:** If you don't have `claude` CLI installed, you can choose `[SKIP-AUTOMATION]` to defer self-healing features.

### Phase 2: Memory & Intelligence (Est. 3 hours)
**Automation:** 85%  
**Checkpoints:** 4  
**Your Role:** Validate memory extraction, approve FTS5 integration

**Key Decision Points:**
- Architecture approval
- Migration verification
- Search integration validation

### Phase 3: CLI Integration (Est. 2 hours)
**Automation:** 90%  
**Checkpoints:** 2  
**Your Role:** Test new CLI commands

**Key Commands to Test:**
```bash
python -m ops.cli maint list-incidents
python -m ops.cli stats --db "$env:DISCOVERY_DB_PATH"
```

### Phase 4: Testing (Est. 2 hours)
**Automation:** 70%  
**Checkpoints:** 1  
**Your Role:** Review E2E test results, approve for production

**Critical Checkpoint:**
- CHECKPOINT 4.1A: All tests must pass before proceeding

### Phase 5: Deployment (Est. 1-2 hours)
**Automation:** 50%  
**Checkpoints:** 3  
**Your Role:** Manual deployment execution, verification

**IMPORTANT:** You execute deployment commands yourself:
```powershell
Copy-Item signals.db signals.db.backup_$(Get-Date -Format "yyyyMMdd_HHmmss")
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m ops.bootstrap --db "$env:DISCOVERY_DB_PATH"
python -m ops.cli run-extraction --limit 3 --db "$env:DISCOVERY_DB_PATH"
```

---

## 🛠️ TROUBLESHOOTING COMMON ISSUES

### Issue: "FTS5 not available"
**Solution:**
```powershell
# Verify Python version
python --version  # Must be 3.11+

# Reinstall if needed
py -3.11 -m pip install --upgrade pip
```

### Issue: "claude CLI not found"
**Solution:**
```bash
npm install -g @anthropic-ai/claude
```
**Alternative:** Choose `[SKIP-AUTOMATION]` at CHECKPOINT 1.2A

### Issue: "Database is locked"
**Solution:**
```bash
# Check for zombie processes
Get-Process | Where-Object {$_.ProcessName -like "*python*"}

# Enable WAL mode
sqlite3 signals.db "PRAGMA journal_mode=WAL;"
```

### Issue: Test failures
**Solution:**
1. Choose `[DEBUG]` at the checkpoint
2. Review error messages
3. Check `pytest` output for specifics
4. Fix issues
5. Re-run: `pytest tests/ops/test_file.py -v`

---

## 📊 PROGRESS TRACKING

Use this checklist to track your progress:

```
Phase 0: Foundation & Verification
[ ] CHECKPOINT 0.1A - Environment Verification
[ ] CHECKPOINT 0.2A - Bootstrap Verification  
[ ] CHECKPOINT 0.3A - Baseline Tests

Phase 1: Self-Healing Infrastructure
[ ] CHECKPOINT 1.1A - Incident Module
[ ] CHECKPOINT 1.2A - Claude CLI Wrapper
[ ] CHECKPOINT 1.3A - Repair Agent

Phase 2: Memory & Intelligence Subsystem
[ ] Architecture approval
[ ] Extractor implementation
[ ] Search integration
[ ] Classification validation

Phase 3: CLI Integration  
[ ] CHECKPOINT 3.1A - CLI Commands

Phase 4: Integration Testing
[ ] CHECKPOINT 4.1A - E2E Test Results

Phase 5: Production Deployment
[ ] CHECKPOINT 5.1A - Deployment Readiness
[ ] CHECKPOINT 5.2A - Deployment Verification
[ ] Final verification
```

---

## ⚡ COMMAND REFERENCE

### Most Frequently Used Commands

```bash
# Bootstrap
python -m ops.bootstrap --db "$env:DISCOVERY_DB_PATH"

# Run tests
pytest tests/ops/ -v

# CLI stats
python -m ops.cli stats --db "$env:DISCOVERY_DB_PATH"

# List incidents
python -m ops.cli maint list-incidents

# Extraction
python -m ops.cli run-extraction --limit 5 --db "$env:DISCOVERY_DB_PATH"

# Health check
python ops/monitor.py
```

---

## 🎓 BEST PRACTICES

### 1. Review Before Approving
- **Always** read the automated output before typing `[CONTINUE]`
- If unsure, choose `[REVIEW]` to examine code

### 2. Save Your Progress
- The procedure generates files as it goes
- Git commit after each successful phase
- Tag major milestones: `git tag phase-1-complete`

### 3. Test in Isolation
- Use `[TEST]` options when available
- Verify changes work before proceeding

### 4. Document Deviations
- If you choose `[SKIP]` or `[MODIFY]`, note why
- Create a `DEPLOYMENT_NOTES.md` with your decisions

### 5. Backup Frequently
```powershell
# Before each major phase
Copy-Item signals.db "signals.db.backup_phase_N"
```

---

## 🚨 EMERGENCY PROCEDURES

### If Something Goes Wrong

1. **Don't Panic**
   - The procedure is designed to be reversible
   - Backups are created automatically

2. **Choose [DEBUG] or [ABORT]**
   - Don't force `[CONTINUE]` if tests fail
   - Take time to investigate

3. **Restore from Backup**
```powershell
# Stop any running processes
Get-Process | Where-Object {$_.ProcessName -like "*python*"} | Stop-Process

# Restore database
Copy-Item signals.db.backup signals.db

# Verify core system
pytest tests/core/ -v
```

4. **Review Logs**
```bash
# Check ops logs
python -m ops.cli stats --db "$env:DISCOVERY_DB_PATH"

# Check system health
sqlite3 signals.db "SELECT * FROM system_health ORDER BY timestamp DESC LIMIT 20;"
```

5. **Ask for Help**
   - Document the exact error
   - Note which checkpoint failed
   - Share relevant logs

---

## ✅ SUCCESS CRITERIA

You'll know the procedure completed successfully when:

1. ✅ All phases marked complete in checklist
2. ✅ No failing tests in `pytest tests/ops/`
3. ✅ `python -m ops.cli stats` shows healthy metrics
4. ✅ Production extraction runs complete successfully
5. ✅ All 14 checkpoints approved

---

## 🎯 FINAL CHECKLIST

Before marking complete, verify:

```bash
# 1. Bootstrap works
python -m ops.bootstrap --db "$env:DISCOVERY_DB_PATH"
# Expected: "✅ OK: DB ready at signals.db"

# 2. All tests pass
pytest tests/ops/ -v --tb=short
# Expected: All PASSED

# 3. CLI works
python -m ops.cli stats --db "$env:DISCOVERY_DB_PATH"
# Expected: Statistics displayed

# 4. Extraction works
python -m ops.cli run-extraction --limit 1 --db "$env:DISCOVERY_DB_PATH"
# Expected: "Facts created: 0+" (depends on data)

# 5. Monitoring works
python ops/monitor.py &
# Expected: Health checks running every 5 minutes
```

---

## 📞 GETTING HELP

If you encounter issues:

1. **Re-read the checkpoint description** - Often contains troubleshooting hints
2. **Check the TROUBLESHOOTING section** above
3. **Review generated code** - Files are in `ops/` directory
4. **Examine test output** - `pytest` gives detailed error messages
5. **Check logs** - `ops/cli.py audit-log` for recent changes

---

## 🎉 COMPLETION

When you reach the final checkpoint and type `[DONE]`:

**Your ops layer is now:**
- ✅ Self-healing (automatic collector repairs)
- ✅ Intelligent (learning from past decisions)
- ✅ Observable (comprehensive monitoring)
- ✅ Production-ready (tested and deployed)

**Next steps:**
1. Set up scheduled jobs (cron/Task Scheduler)
2. Monitor `extraction_runs` table daily
3. Review and approve pending memory facts
4. Configure alerting for system_health degradation

Congratulations! 🎊
