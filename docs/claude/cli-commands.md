# CLI commands

Common operational commands for running the pipeline and diagnostics.

## Commands

```bash
# Run full discovery pipeline
python run_pipeline.py full --collectors github,sec_edgar --dry-run

# Run specific collectors only
python run_pipeline.py collect --collectors companies_house,domain_whois

# Process pending signals (push to Notion)
python run_pipeline.py process --dry-run

# Sync suppression cache from Notion
python run_pipeline.py sync

# View pipeline stats
python run_pipeline.py stats

# View pipeline metrics with collector breakdown
python run_pipeline.py metrics
python run_pipeline.py metrics --limit 10 --collector github

# Health check (DB, APIs, anomaly detection)
python run_pipeline.py health
python run_pipeline.py health --json  # Machine-readable output

# Run canonical key tests
python utils/canonical_keys.py

# Test signal storage (manual tests)
python storage/manual_test_signal_store.py
```

## Development practices

## Development Practices (Superpowers-Inspired)

### TDD Enforcement (The Iron Law)
Write failing tests first, then minimal code to pass them.

**RED-GREEN-REFACTOR Cycle:**
1. Write failing test → 2. Verify RED → 3. Implement minimal code → 4. Verify GREEN → 5. Commit

**Red Flags Requiring Restart:**
- Code written before failing tests
- Tests passing immediately upon writing
- Tests marked for "later" addition

### Git Worktrees
- Worktree directory: `.worktrees/` (in .gitignore)
- Create isolated workspace: `git worktree add .worktrees/<feature> -b <branch>`
- Run baseline tests before claiming readiness

### Code Review Checkpoints
| Severity | Action |
|----------|--------|
| Critical | Fix immediately before progression |
| Important | Fix before proceeding |
| Minor | Document for later |

### Planning
- Plans stored in `docs/plans/YYYY-MM-DD-<feature>.md`
- Tasks should be 2-5 minutes each
- Explicit git commits after each task completion
- Follow DRY, YAGNI, TDD principles

---
