# Test Spec: Discovery Engine MCP Hardening

## Scope
Verification for the launcher and `push-to-notion` dry-run hardening work described in `prd-discovery-engine-mcp-hardening.md`.

## Test Objectives
1. Prove the launcher uses delayed expansion semantics compatible with its comment filter.
2. Prove `push-to-notion` dry-run succeeds without Notion credentials.
3. Prove dry-run does not initialize the Notion connector or verification gate.
4. Keep the verification slice narrow and directly tied to the patched behavior.

## Test Matrix

### A. Launcher behavior
- **Target file:** `run_discovery_engine.bat`
- **Required evidence:**
  1. Batch comment filtering uses delayed expansion.
  2. Missing `.env` does not break the launcher.
  3. Commented lines are skipped instead of becoming environment assignments.
- **Evidence method:**
  - direct `cmd` smoke command that mirrors the launcher's delayed-expansion pattern.

### B. MCP dry-run contract
- **Target file:** `discovery_engine/mcp_server.py`
- **Required tests:**
  1. `_handle_push_to_notion({"discovery_id": "...", "dry_run": "true"})` returns the dry-run success payload with Notion env vars unset.
  2. Dry-run path does not call `get_notion_connector()`.
  3. Dry-run path does not call `get_verification_gate()`.
- **Evidence commands:**
  - targeted `pytest` for the new MCP server test file
  - direct Python smoke invocation for the handler

### C. Syntax safety
- **Target files:**
  - `discovery_engine/mcp_server.py`
  - new MCP test file
- **Required evidence:**
  1. Touched Python files compile cleanly.

## Acceptance Gate
The work is complete only when:
1. The new regression test passes.
2. The direct dry-run smoke check passes without Notion credentials.
3. The launcher smoke check shows delayed expansion is active for the comment-filter logic.

## Deferred Coverage
These are intentionally not part of this patch's acceptance gate:
- implementing placeholder MCP tools,
- making `check-suppression` use `company_name`,
- broad `.mcp.json` and docs reconciliation.
