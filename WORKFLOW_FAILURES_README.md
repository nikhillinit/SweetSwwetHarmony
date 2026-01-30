# Workflow Failure Summary

Quick reference for GitHub Actions workflow failures in this repository.

## Status Overview

| Workflow | Failures | Status | Priority |
|----------|----------|--------|----------|
| daily-pipeline.yml | 35 | ACTIVE ⚠️ | 🔴 HIGH |
| thesis_eval.yml | 15 | ACTIVE ⚠️ | 🔴 HIGH |
| daily-monitoring.yml | 37 | DEPRECATED | ⚪ LOW |
| daily-signal-collection.yml | 107 | DEPRECATED | 🟡 MEDIUM |

**Total Failure Rate:** 100% (204 consecutive failures across all workflows)

## Root Cause

🔍 **YAML Parsing Issue**: The `on:` keyword in workflow files may be interpreted as a boolean, causing parsing failures before any jobs execute.

📋 **Evidence**: All workflows show 0 jobs executed with "failure" conclusion

## Impact

- ❌ Daily discovery pipeline not running (should run at 6 AM UTC daily)
- ❌ Thesis evaluation not running (should run Sundays at 2 AM UTC)
- ❌ No automated signal collection
- ❌ No portfolio monitoring
- ❌ Wasted CI/CD resources

## Quick Fixes Needed

### High Priority (Active Workflows)

1. **Fix daily-pipeline.yml**
   - Fix YAML `on:` syntax (quote key or restructure)
   - Add explicit trigger guards to prevent push triggers
   - Test with manual workflow_dispatch

2. **Fix thesis_eval.yml**
   - Fix YAML `on:` syntax
   - Add explicit trigger guards
   - Test with manual workflow_dispatch

### Medium Priority (Cleanup)

3. **Remove deprecated workflows**
   - Archive or delete `daily-monitoring.yml`
   - Archive or delete `daily-signal-collection.yml`
   - These are marked deprecated and replaced by daily-pipeline.yml

## Documentation

📖 **Full Analysis:** See [WORKFLOW_FAILURE_ANALYSIS.md](./WORKFLOW_FAILURE_ANALYSIS.md) for:
- Detailed root cause analysis
- Individual workflow breakdowns
- Complete impact assessment
- Testing plan
- Recommended fixes

📊 **Quick Reference:** See [workflow_failures.csv](./workflow_failures.csv) for tabular data

## Verification Commands

```bash
# Check workflow status
gh workflow list

# View recent runs
gh run list --workflow=daily-pipeline.yml --limit 5

# Manually trigger workflow
gh workflow run daily-pipeline.yml

# View workflow run logs
gh run view <run-id>
```

## Timeline

- **Analysis Date:** 2026-01-30
- **First Known Failure:** Unknown (all historical runs failed)
- **Total Failures Documented:** 204+
- **Workflows Analyzed:** 4

---

**Generated:** 2026-01-30  
**For:** nikhillinit/SweetSwwetHarmony  
**By:** GitHub Copilot Coding Agent
