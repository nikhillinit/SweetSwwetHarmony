# Ops Layer Phase 1 — Self-Healing Infrastructure

**Goal:** Complete Phase 1 by adding CLI maintenance commands, Docker orchestration, and comprehensive tests for all self-healing components.
**Started:** 2026-02-05
**Status:** IN PROGRESS

---

## Current State (Verified)

### Existing (from Phase 0)
- ops/maintenance/incident.py — incident capsule management (tested in e2e)
- ops/maintenance/claude_code_cli.py — Claude CLI wrapper (untested)
- ops/maintenance/repair_agent.py — repair orchestrator (untested)
- ops/cli.py — 9 subcommands, missing `maint` and `docker` groups
- ops/infra/ — empty directory, no docker_manager yet
- 26 ops tests passing, 31 migration tests, 33 CRUD tests

### Missing
1. CLI `maint` subcommand group (list-incidents, repair-latest, show)
2. CLI `docker` subcommand group (status, restart, prune-networks)
3. ops/infra/docker_manager.py module
4. Tests for maintenance CLI commands
5. Tests for Claude CLI wrapper (mocked)
6. Tests for repair agent (mocked)
7. Tests for docker manager (mocked)

---

## Phases

### Phase 1.1: Maintenance CLI Commands
**Status:** `pending`
**Files:** ops/cli.py
**Tasks:**
- [ ] Add `maint` subparser with sub-subcommands
- [ ] Implement `list_incidents_cmd()` — list incidents with optional --status filter
- [ ] Implement `show_incident_cmd()` — show incident details + artifact paths
- [ ] Implement `repair_latest_cmd()` — trigger repair agent on most recent open incident
- [ ] Implement `repair_cmd()` — trigger repair on specific incident_id
**Estimated:** 20 min

### Phase 1.2: Maintenance CLI Tests (TDD)
**Status:** `pending`
**Files:** tests/ops/test_maintenance_cli.py
**Tasks:**
- [ ] Write failing tests first for each maint subcommand
- [ ] Test list-incidents with empty dir, with incidents, with --status filter
- [ ] Test show-incident with valid and invalid incident IDs
- [ ] Test repair-latest with mocked ClaudeCodeCLI
- [ ] Test repair with mocked ClaudeCodeCLI
- [ ] Verify all tests pass after Phase 1.1 implementation
**Estimated:** 30 min

### Phase 1.3: Docker Orchestration Module
**Status:** `pending`
**Files:** ops/infra/docker_manager.py
**Tasks:**
- [ ] Create DockerManager class with graceful degradation (no docker SDK required)
- [ ] Implement service_status() — check running containers via `docker ps`
- [ ] Implement restart_service() — restart named container
- [ ] Implement stop_service() — stop named container
- [ ] Implement prune_networks() — clean up unused Docker networks
- [ ] Implement health_check() — check Docker daemon availability
- [ ] All methods return structured results, never raise on missing Docker
**Estimated:** 30 min

### Phase 1.4: Docker CLI Commands
**Status:** `pending`
**Files:** ops/cli.py
**Tasks:**
- [ ] Add `docker` subparser with sub-subcommands
- [ ] Implement `docker_status_cmd()` — show container status
- [ ] Implement `docker_restart_cmd()` — restart a service
- [ ] Implement `docker_prune_cmd()` — prune unused networks
**Estimated:** 15 min

### Phase 1.5: Docker Tests (TDD)
**Status:** `pending`
**Files:** tests/ops/test_docker_manager.py
**Tasks:**
- [ ] Mock subprocess.run for all docker commands
- [ ] Test graceful degradation when Docker not installed
- [ ] Test service status parsing
- [ ] Test restart/stop with success and failure cases
- [ ] Test prune output parsing
**Estimated:** 30 min

### Phase 1.6: Repair Agent & Claude CLI Tests
**Status:** `pending`
**Files:** tests/ops/test_repair_agent.py
**Tasks:**
- [ ] Mock ClaudeCodeCLI.call() for repair scenarios
- [ ] Test _build_repair_prompt() sanitization
- [ ] Test repair_incident() status transitions (pending → investigating → resolved)
- [ ] Test repair_incident() failure handling (pending → investigating → failed)
- [ ] Test repair_latest() with no open incidents
- [ ] Test ClaudeCodeCLI.available property
- [ ] Test ClaudeCodeCLI.call() timeout handling
**Estimated:** 30 min

### Phase 1.7: Integration Verification
**Status:** `pending`
**Tasks:**
- [ ] Run full test suite: pytest tests/ops/ -v
- [ ] Run full test suite: pytest tests/storage/ -v
- [ ] Verify zero regressions in existing tests
- [ ] Manual smoke test: python -m ops.cli maint list-incidents
- [ ] Manual smoke test: python -m ops.cli docker status
- [ ] Update progress.md with final counts
**Estimated:** 10 min

---

## Completion Criteria

- [ ] `python -m ops.cli maint list-incidents` works
- [ ] `python -m ops.cli maint show <id>` works
- [ ] `python -m ops.cli maint repair-latest` works (with Claude CLI available)
- [ ] `python -m ops.cli docker status` works (graceful if no Docker)
- [ ] All new tests pass
- [ ] All existing tests still pass (zero regressions)
- [ ] Checkpoint saved to memory-keeper

---

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
