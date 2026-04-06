# PRD: Atomic Beaming Moore Proposal Revision

Date: 2026-04-05
Mode: consensus proposal-revision plan
Source artifact:
- `C:\Users\nikhi\.claude\plans\atomic-beaming-moore.md`

Context snapshot:
- `.omx/context/atomic-beaming-moore-20260406T034433Z.md`

Companion delta spec:
- `.omx/plans/atomic-beaming-moore-delta-spec-ralplan.md`

## Goal

Revise the external proposal so it reflects the current live tree and current environment, without replacing it wholesale.

## Problem Statement

The current proposal correctly clears several stale items, but its top priority is still wrong: it keeps DB-hardening first even though the live tree already landed the `restore_db.py` DB-path contract and its CI coverage. The strongest live discrepancy is elsewhere: `MERGE_WRITES_ENABLED` has no `feature_promote` or `regret_check` rows in direct DB reads.

The proposal also needs an executor-safe verification section that works in this environment:
- `sqlite3` CLI is unavailable
- governance has a write API but no read API
- read-side verification must therefore either name an authoritative API read surface or derive/export a readable DB file path explicitly

## Scope

### In scope

- rewrite the external proposal’s ranking
- rewrite lane `#1`
- rewrite lane `#2`
- rewrite lane `#3`
- replace broken verification guidance

### Out of scope

- implementing the next priority lane itself
- reopening already-closed review-followup, signal 438, or capability-router identity work
- creating a new strategy unrelated to the external proposal

## Approved Rewrite

### Lane order

Replace the current ranking with:

1. `MERGE_WRITES_ENABLED` audit-trail investigation plus baseline snapshot
2. support-tooling quarantine decision
3. DB-hardening stale-artifact reconciliation / residual decision-close work
4. deferred thesis classifier expansion

### Lane `#1`

Rewrite as one bounded investigation-plus-baseline lane.

Required order:
1. resolve the authoritative governance store first
2. check for bypassed governance flow second
3. consider naming/schema drift only as fallback

Required authoritative-store branch:
- if there is a named authoritative API read surface, use it
- otherwise derive/export the readable DB path to inspect from:
  - explicit operator-provided path
  - or confirmed `DISCOVERY_DB_PATH`
- if API transport is active and no readable DB path can be established, stop as a documented transport blocker instead of guessing

Required outcomes:
- resolved explanation
- repair handoff
- or explicit documented discrepancy/blocker

### Lane `#2`

Keep this as a boundary decision lane, not an implementation bucket.

Allowed outcomes:
- promote-with-guardrails
- quarantine
- retire

Any promotion must meet the same DB-safety contract used by maintained tooling.

### Lane `#3`

Demote DB hardening to stale-artifact reconciliation only:
- annotate or retire stale hardening PRD/test-spec/context language
- identify true residual decisions only
- do not send executors after already-landed `restore_db.py` contract work

## Acceptance Criteria

1. The revised external proposal no longer presents `restore_db.py` DB-path normalization as open implementation work.
2. Lane `#1` is top-ranked and cites the live direct-DB query absence as the primary evidence.
3. Lane `#1` has a concrete authoritative-store branch that is executable in this environment.
4. Lane `#1` includes a feature baseline capture step and explicit stop behavior when no readable read-side store can be identified.
5. Lane `#2` is explicitly framed as a boundary decision lane.
6. Lane `#3` is artifact reconciliation only.
7. The verification section contains no `sqlite3` CLI dependency and no implicit fallback to `signals.db`.

## Concrete Verification Shape For The Revised Proposal

### Baseline capture

```powershell
python -m monitoring.feature_gate snapshot --json
```

### Read-side store derivation for local/direct-DB verification

```powershell
$env:GOV_DB_PATH = python -c "import os, pathlib, sys; db=os.environ.get('DISCOVERY_DB_PATH'); sys.exit('Set DISCOVERY_DB_PATH to the authoritative governance DB path before verification') if not db else None; p=pathlib.Path(db); ok=p.is_file() and os.access(db, os.R_OK); sys.exit(f'Configured DISCOVERY_DB_PATH is not a readable file: {db}') if not ok else None; print(db)"
```

### Read-only audit queries

```powershell
@'
import json
import os
import sqlite3

db_path = os.environ["GOV_DB_PATH"]
conn = sqlite3.connect(db_path)
rows = conn.execute(
    """
    SELECT entity_id, action_type, MAX(created_at)
    FROM audit_events
    WHERE action_type IN ('feature_promote', 'regret_check')
    GROUP BY entity_id, action_type
    ORDER BY 3 DESC
    """
).fetchall()
print(json.dumps({"db_path": db_path, "rows": rows}, indent=2))
conn.close()
'@ | python -
```

```powershell
@'
import json
import os
import sqlite3

db_path = os.environ["GOV_DB_PATH"]
conn = sqlite3.connect(db_path)
rows = conn.execute(
    """
    SELECT entity_id, action_type, created_at
    FROM audit_events
    WHERE entity_id = 'MERGE_WRITES_ENABLED'
      AND action_type IN ('feature_promote', 'regret_check')
    ORDER BY created_at DESC
    """
).fetchall()
print(json.dumps({"db_path": db_path, "rows": rows}, indent=2))
conn.close()
'@ | python -
```

```powershell
@'
import json
import os
import sqlite3

db_path = os.environ["GOV_DB_PATH"]
conn = sqlite3.connect(db_path)
rows = conn.execute(
    """
    SELECT COUNT(*)
    FROM canary_drift_alerts
    WHERE status = 'open'
    """
).fetchall()
print(json.dumps({"db_path": db_path, "rows": rows}, indent=2))
conn.close()
'@ | python -
```

### API-backed / no-read-API fallback

```powershell
$env:GOV_DB_PATH = "<resolved-gov-db-path>"
python -c "import os, pathlib, sys; db=os.environ['GOV_DB_PATH']; p=pathlib.Path(db); ok=p.is_file() and os.access(db, os.R_OK); print({'db_path': db, 'exists': p.is_file(), 'readable': ok}); sys.exit(0 if ok else 1)"
```

If that fails:
- stop as a documented transport blocker
- capture only:
  - `python -m monitoring.feature_gate snapshot --json`
  - active transport evidence (`DISCOVERY_API_URL` present/absent, `DISCOVERY_DB_PATH` present/absent)
  - the reason audit-event verification could not proceed without guessing the backing store

## ADR

### Decision

Revise the proposal as a delta, with `MERGE_WRITES_ENABLED` first, support-tooling second, DB-hardening reconciliation third, thesis expansion deferred.

### Drivers

- live repo evidence
- direct DB evidence
- executor safety
- stale-artifact cleanup

### Alternatives

- keep DB-hardening first
- collapse audit-trail work into later verification-only work
- drop DB-hardening completely

### Why chosen

It is the narrowest rewrite that corrects the live discrepancy and keeps the external proposal recognizable.

### Consequences

- executors focus first on a real unresolved discrepancy
- support-tooling stays a governance boundary decision
- DB hardening stays visible only as reconciliation

## Available Agent Types

- `planner`
- `architect`
- `critic`
- `verifier`
- `writer`
- `explore`
- `executor`

## Staffing Guidance

### `$ralph`

- one owner rewrites the external proposal
- `architect`/`critic` challenge the ranking and boundaries
- `verifier` confirms the revised text matches live repo evidence and executable verification shape

### `$team`

Recommended lanes:
1. `planner` lane: rewrite ranking and lane scopes
2. `architect` or `critic` lane: challenge ordering and transport-store logic
3. `verifier` lane: confirm cited code paths and commands run
4. `writer` lane: compress the final wording into the external plan

Suggested reasoning:
- `planner`: medium
- `architect`: high
- `critic`: high
- `verifier`: high
- `writer`: medium
