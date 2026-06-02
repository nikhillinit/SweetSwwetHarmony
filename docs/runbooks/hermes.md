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
6. Gemini CLI execute: Gemini is available as a CLI-backed executor for
   non-mutating review/routing work. The adapter runs Gemini headlessly with
   plan approval mode and the trusted-workspace bypass required for automated
   CLI runs.
7. Deferred providers: Antigravity and any future Vertex surface stay
   non-executable until adapters and doctor checks are landed with tests.

## Commands

```powershell
python -m ops.cli hermes --help
python -m ops.hermes_cli --help
python -m ops.cli hermes providers doctor
python -m ops.cli hermes providers doctor --json
python -m ops.cli hermes route --json --phase production --task "fix thesis filter"
python -m ops.cli hermes run --plan-only --phase production --task "schema migration"
python -m ops.cli hermes run --dry-run --phase production --task "schema migration for signal store"
python -m ops.cli hermes route --json --phase production --task "update runbook docs"
python -m ops.cli hermes run --execute --phase planning --task "update runbook docs" --gemini
python -m ops.cli hermes run --execute --phase production --task "schema migration" --codex
python -m ops.cli hermes run --execute --phase production --task "schema migration" --codex --ack-risk I-ACK-RISK
```

## Track A Hardening Strategy

The post-PR235 enforcement roadmap is tracked at
`docs/superpowers/specs/2026-06-01-hermes-track-a-post-pr235-hardening.md`.
Use that document as the source of truth for H1-H5 definitions: canonical plan
hash, contract versions, causal deliberation freshness, dry-run drift,
ledger-audit v2, bypass lifecycle, and PR sequencing.

Current post-PR260 implementation status:

- Plan contracts carry `contractVersion` and canonical plan hashes.
- `deliberation_passed` checks plan hash, consensus status, blockers, dissent,
  trusted reviewer policy, recorded quorum evidence, restore readiness when
  required, and TTL.
- `ledger-audit` checks index/run/artifact consistency and v2 subsystem
  reconciliation for restore/SQLite, governance/config, collector promotion,
  suppression/outbox, and bypass lifecycle artifacts.
- `ledger-audit` reports `operatorSummary` and supports the read-only
  `rehearsals` scope for registered Hermes task contracts.
- H5 adds typed `failure_event.json` artifacts, canonical task lock-order
  assertions, and the `Hermes Ledger Audit` workflow for PR, nightly, and
  manual runs.
- `incident` records incident lifecycle state; it does not authorize restore or
  bypass decisions.

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

Failure artifacts include `failure_event.json` for typed operator/audit parsing.
When a command, gate, or executor repair path exists, Hermes also writes
`repair_prompt.md` with state snapshot paths, gate artifact paths, routing plan,
exit code, and the next safe operator action.

The `Hermes Ledger Audit` GitHub workflow runs on pull requests, nightly
schedule, and manual dispatch. It initializes an empty local Hermes ledger
scaffold, runs `ledger-audit` in dry-run mode, and uploads the generated
operator reports without invoking restore, canary, or Notion-facing paths.
The audit report also supports a read-only `rehearsals` scope that records the
registered Hermes task contract surface and fails closed on malformed static
metadata such as non-canonical task lock declarations.

## Safety Rules

- Provider doctor is read-only and does not make network probes.
- Codex and Kimi execution delegates to existing wrappers.
- Gemini execution delegates to the installed Gemini CLI, not the Gemini API.
  `C:\Users\nikhi\.gemini` is the CLI data/config area; Hermes discovers the
  executable via PATH, typically `C:\Users\nikhi\AppData\Roaming\npm\gemini.CMD`
  on Windows.
- Gemini doctor checks wrapper import and CLI binary availability. It does not
  require `GEMINI_API_KEY`.
- Gemini runs with `--prompt`, `--approval-mode plan`, `--output-format text`,
  and `--skip-trust` so Hermes can use it in headless CLI mode without granting
  file-edit or external-system mutation approval.
- Hermes launches Gemini from an isolated temp working directory and passes
  context through stdin so a Gemini CLI run cannot dirty the project checkout.
- Deferred executors are refused by the adapter registry.
- High-risk execution fails closed without `--ack-risk I-ACK-RISK`.
- Hermes code uses `datetime.now(timezone.utc)`, not `datetime.utcnow()`.
- Do not add a repo-wide datetime lint ratchet in a Hermes-only change.
