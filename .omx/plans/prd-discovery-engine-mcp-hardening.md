# PRD: Discovery Engine MCP Hardening

## Requirements Summary
Stabilize the Discovery Engine MCP entrypoint and dry-run contract without broadening scope into unfinished MCP features.

This plan covers the current production-facing slice:
- make the Windows MCP launcher load `.env` lines correctly,
- make `push-to-notion` dry-run succeed without Notion credentials or connector initialization,
- add regression coverage for the dry-run contract,
- document the next MCP hardening steps that remain intentionally deferred.

## Brownfield Grounding
- `run_discovery_engine.bat:5-9` uses `!line:~0,1!` without delayed expansion, so comment filtering in `.env` loading is unreliable.
- `discovery_engine/mcp_server.py:417-419` initializes the Notion connector before checking `dry_run`, which breaks the advertised dry-run behavior.
- `discovery_engine/mcp_server.py:429-438` already returns a synthetic dry-run success payload, so the bug is sequencing, not missing dry-run output design.
- `discovery_engine/mcp_server.py:536-638` still exposes placeholder MCP tools; those should be handled as a separate follow-up, not bundled into this fix.
- `discovery_engine/mcp_server.py:346-400` accepts `company_name` for suppression checks but does not currently use it in the connector call; this is a separate functional gap.

## Goals
1. The MCP launcher starts with delayed expansion enabled and ignores commented `.env` lines correctly.
2. `push-to-notion` dry-run is credential-free and connector-free.
3. The dry-run behavior is protected by a focused regression test.
4. The plan explicitly records deferred MCP follow-ups so they do not get lost.

## Non-Goals
- Implement live `push-to-notion`.
- Implement placeholder MCP tools such as `get_company_signals` or `get_routing_decision`.
- Redesign `check-suppression` matching semantics.
- Rework `.mcp.json` or broader MCP package/doc alignment in the same patch.

## Acceptance Criteria
1. `run_discovery_engine.bat` enables delayed expansion before `.env` parsing and safely skips comment lines.
2. Calling `push-to-notion` with `dry_run=true` returns the dry-run success payload even when `NOTION_API_KEY` and `NOTION_DATABASE_ID` are unset.
3. Dry-run does not call `get_notion_connector()` or `get_verification_gate()`.
4. A targeted automated test proves the dry-run contract.
5. Plan artifacts exist in `.omx/plans/` describing both the current fix and deferred MCP hardening work.

## Implementation Steps

### Step 1 - Fix the Windows launcher
- Add `setlocal EnableExtensions EnableDelayedExpansion` to `run_discovery_engine.bat`.
- Wrap `.env` parsing in an `if exist ".env"` guard.
- Keep the launcher behavior minimal: load env, start `python -m discovery_engine.mcp_server`.

### Step 2 - Make `push-to-notion` truly dry
- Move the `dry_run` return path ahead of any connector or gate initialization in `discovery_engine/mcp_server.py`.
- Keep the live path explicit and unchanged in scope: still report that live push is not implemented.
- Avoid introducing new dependencies or storage lookups in this fix.

### Step 3 - Add regression coverage
- Add a focused async test for `_handle_push_to_notion(...)` proving dry-run succeeds with no Notion credentials.
- Make the test fail if dry-run attempts to initialize the Notion connector.

### Step 4 - Record the next MCP hardening backlog
- Follow-up A: decide whether placeholder tools should be implemented or hidden until ready.
- Follow-up B: wire `company_name` into `check-suppression` or remove it from the prompt contract.
- Follow-up C: align `.mcp.json` and MCP documentation around the filesystem server package naming and active MCP surfaces.

## Risks and Mitigations
| Risk | Why it matters | Mitigation |
|---|---|---|
| Over-scoping the fix into unfinished MCP features | Slows down a small repair and increases regression surface | Keep this patch limited to launcher + dry-run + regression test |
| Batch script changes behave differently on Windows shells | Launcher is shell-sensitive | Verify with a direct `cmd` smoke check after the edit |
| Dry-run path still picks up hidden side effects later | Future refactors could reintroduce connector access | Add an automated regression that fails if connector initialization occurs |

## Verification Steps
1. Run the new targeted MCP regression test.
2. Run a direct Python invocation of `_handle_push_to_notion(...)` with no Notion credentials and confirm the dry-run payload.
3. Run a `cmd` smoke check that exercises the launcher-style delayed-expansion comment filter.
4. Run Python compile checks on touched Python files.

## ADR
### Decision
Apply a narrow hardening patch for the launcher and dry-run contract now, and defer broader MCP completion work into a tracked follow-up backlog.

### Why chosen
The current defects are real user-facing breakages in the advertised MCP path. They can be fixed safely without touching unfinished live push, suppression semantics, or placeholder tool design.

### Alternatives considered
- Broader MCP completion in one patch: rejected because it mixes clear bug fixes with product decisions.
- Doc-only clarification that dry-run still needs credentials: rejected because it preserves broken behavior instead of fixing it.

### Consequences
- Dry-run becomes a real validation/preview path.
- Live push remains intentionally unimplemented.
- Broader MCP gaps stay visible as explicit deferred work instead of being silently ignored.
