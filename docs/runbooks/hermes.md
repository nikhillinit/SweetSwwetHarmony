# Hermes Runbook

Hermes is the repo-local multi-model routing harness for auditable model
selection, gate execution, dry-run ledgers, and guarded provider execution.

## Rollout Stages

1. JSON-only: use `python -m ops.cli hermes route --json` to inspect routing
   decisions. This creates no files and executes no providers.
2. Dry-run: use `python -m ops.cli hermes run --dry-run` to write a ledger,
   prompt, routing plan, gate output, summary, and state snapshots without
   executing a provider.
3. Preflight-only: use `python -m ops.cli hermes run --preflight-only` to prove
   configured gates before execution.
4. Low-risk execute: use `python -m ops.cli hermes run --execute` only after
   route and dry-run output look correct.
5. High-risk execute: the high-risk execute path requires
   `--ack-risk I-ACK-RISK`; without that exact acknowledgement Hermes exits 75.
6. Deferred providers: Gemini CLI, Antigravity, and any future Vertex surface
   stay non-executable until adapters and doctor checks are landed with tests.

## Commands

```powershell
python -m ops.cli hermes --help
python -m ops.hermes_cli --help
python -m ops.cli hermes providers doctor
python -m ops.cli hermes providers doctor --json
python -m ops.cli hermes route --json --phase production --task "fix thesis filter"
python -m ops.cli hermes run --plan-only --phase production --task "schema migration"
python -m ops.cli hermes run --dry-run --phase production --task "schema migration for signal store"
python -m ops.cli hermes run --execute --phase production --task "schema migration" --codex
python -m ops.cli hermes run --execute --phase production --task "schema migration" --codex --ack-risk I-ACK-RISK
```

## Artifacts

Hermes writes run artifacts under `ai-logs/hermes/runs/<run_id>/` and appends
one JSON object per line to `ai-logs/hermes/index.jsonl`.

Expected dry-run artifacts:

- `ledger.json`
- `plan.json`
- `prompt.txt`
- `summary.md`
- `state/S0_initial.json`
- `state/S1_routing.json`
- `state/S2_preflight.json`
- `state/S3_postflight.json`

Failure artifacts include `repair_prompt.md` with the failed command or
executor, state snapshot paths, gate artifact paths, routing plan, exit code,
and the next safe operator action.

## Safety Rules

- Provider doctor is read-only and does not make network probes.
- Codex and Kimi execution delegates to existing wrappers.
- Deferred executors are refused by the adapter registry.
- High-risk execution fails closed without `--ack-risk I-ACK-RISK`.
- Hermes code uses `datetime.now(timezone.utc)`, not `datetime.utcnow()`.
- Do not add a repo-wide datetime lint ratchet in a Hermes-only change.
