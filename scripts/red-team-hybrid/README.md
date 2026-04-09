# scripts/red-team-hybrid

Tooling for the Direction-A-Derived Hybrid strategy. See
`docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`.

## Files

| File | Purpose | Move |
|---|---|---|
| `check_protected_paths.sh` | Step 4B regret-window guard. Fails if any commit on `prep/red-team-hybrid-prep` touches `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, or `storage/migrations/`. Run before every commit on the prep branch. | 0 |

## Usage

### Pre-commit guard
```bash
bash scripts/red-team-hybrid/check_protected_paths.sh && git commit -m "your message"
```

To check against a specific base ref:
```bash
bash scripts/red-team-hybrid/check_protected_paths.sh origin/main
```

## What's NOT here yet (Move 1+)

These scripts are specced in the design docs but NOT built in Move 0:

- `build_holdout_split.py` — builds `data/shadow/holdout_split/episodes_v1.csv`
  from Track B episodes. See `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md`.
- `run_tier2_eval.py` — runs the Tier-2 recall eval against the holdout split.
  See `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md`.

These are Move 1 deliverables (post 2026-04-19).
