# Rule: Plan Verification Before Execution

## Trigger
When a multi-phase implementation plan references CLI commands, function signatures, or processing pipelines.

## Required: Command-Level Verification

Before declaring any plan ready for execution, verify every command and code path against the live codebase:

### 1. CLI commands must be verified against --help
```powershell
# For every CLI command in the plan, run:
python <module> <subcommand> --help
# Confirm: flag exists, flag name matches, flag position is correct
```

**Known gotcha:** `ops.cli quality` takes `--db` on the PARENT command, not on subcommands like `stats`.

### 2. Function signatures must be traced through the full call chain
Don't stop at the storage layer. Trace mutations through:
```
CLI arg parsing -> handler function -> pipeline method -> internal stage -> storage method
```

**Known gotcha:** `process_pending()` calls `_process_signals_stage()` calls `get_pending_signals()`. Adding a parameter to the storage method requires updating all 3 signatures.

### 3. Side effects must be traced past the first function call
`--dry-run` may not guard all state mutations. Read the code path for:
- What gets written to the DB BEFORE the dry-run guard?
- What status transitions happen in active vs shadow mode?
- Does the function modify `signal_processing.status` before checking `dry_run`?

**Known gotcha:** In active (non-shadow) LLM_THESIS_MODE, thesis reject/hold paths call `mark_rejected()` and `update_signal_status()` BEFORE the dry-run Notion push guard. `--dry-run` only prevents Notion writes, not processing state mutations.

### 4. Metrics and counts are timestamped snapshots, not durable facts
Every number in a plan file (test counts, git status counts, queue sizes) must include:
- Source command that produced it
- Timestamp when it was captured
- Explicit label: "snapshot at YYYY-MM-DDTHH:MMZ"

Counts drift as the session progresses (editing files changes git status, running tests changes collection counts). Re-verify before execution if >30 minutes have passed.

### 5. Cross-file consistency check
When plan spans multiple files (task_plan.md, findings.md, progress.md):
- State description must agree across all files
- Option/path naming must be consistent (don't call it "Option C" in one file and "Option D" in another)
- Commands must be identical everywhere they appear

### 6. Environment-appropriate syntax
This project runs on Windows with PowerShell. Use:
- `$env:VAR="value"` not `VAR=value`
- Forward slashes in Python paths, but PowerShell commands use native paths
- ASCII punctuation in plan files (-- not em-dash, -> not arrow)

## Anti-Pattern: Analytical Depth Without Fidelity

Systems thinking, red teaming, and evaluation frameworks catch structural and logical flaws. They do NOT catch:
- Wrong CLI flag names
- Missing function parameters in the call chain
- Side effects hidden three function calls deep
- Stale numbers from earlier in the session

**Always follow analytical planning with a line-level verification pass.**
