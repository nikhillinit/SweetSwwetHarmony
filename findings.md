# Ops Layer Phase 1 — Findings

## Existing Module Inventory

### ops/maintenance/incident.py (4.3KB)
- MaintenanceIncident dataclass: incident_id, component, error_type, error_message, status, traceback_text, context, repair_attempts
- Status values: open | investigating | resolved | wont_fix
- create_incident(), load_incident(), update_incident_status(), list_incidents()
- Tested in test_e2e_integration.py (lines 205-237)

### ops/maintenance/claude_code_cli.py (3KB)
- ClaudeCodeCLI class wrapping `claude -p` for non-interactive use
- `.available` property checks CLI installation
- `.call()` method: prompt, session_id, output_format, allowed_tools, timeout_s=300
- Returns dict: {success, output, error}
- NOT tested

### ops/maintenance/repair_agent.py (3.4KB)
- RepairAgent class using ClaudeCodeCLI
- _build_repair_prompt(): constructs repair prompt from incident artifacts
- repair_incident(): orchestrates repair for specific incident
- repair_latest(): repairs most recent open incident
- NOT tested

### ops/cli.py (24KB, 9 commands)
Existing subcommands: list, approve, retire, list-actions, reset-action, audit-unused, stats, run-extraction, cleanup
- Uses argparse with subparsers
- Transaction-safe stats (nested transaction bug fixed in Phase 0)
- Windows encoding: sys.stdout.reconfigure(errors='replace')

### ops/infra/ (empty)
- __init__.py only, no modules yet

## Architecture Decisions

### Docker Manager: subprocess over SDK
Use `subprocess.run(["docker", ...])` rather than the `docker` Python SDK. Rationale:
- No extra dependency to install
- Graceful degradation when Docker not installed (catch FileNotFoundError)
- Simpler for the 4 commands we need (ps, restart, stop, network prune)
- Consistent with ClaudeCodeCLI pattern (also subprocess-based)

### CLI Structure: Nested subparsers
Use `add_subparsers()` on a `maint` parent parser for `list-incidents`, `show`, `repair-latest`, `repair`.
Same pattern for `docker` parent with `status`, `restart`, `stop`, `prune-networks`.

### Test Strategy: Mock external processes
- Mock `subprocess.run` for Docker commands
- Mock `ClaudeCodeCLI.call()` for repair agent tests
- Use real filesystem (tmp_path) for incident capsule tests
- No actual Docker or Claude CLI invocations in tests
