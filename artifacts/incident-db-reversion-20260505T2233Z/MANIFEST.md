# signals.db incident packet

Incident: suspected `signals.db` reversion/truncation around `2026-05-05T22:33:37Z` UTC (`15:33:37` America/Los_Angeles).

GitHub issue: https://github.com/nikhillinit/SweetSwwetHarmony/issues/149

Private raw packet: `packet-20260508T001346Z` under the local private evidence root. Raw DB files, shell history, scheduler exports, and machine-local metadata are intentionally not stored in this repo-facing packet.

Repo-facing files:

- `phase0-freeze-summary.md` - public-safe Phase 0 evidence freeze summary.
- `SHA256SUMS.redacted.txt` - hashes for repo-safe evidence surfaces and DB identity proof.
- `artifact-index.redacted.md` - repo-safe size, mtime, collected-copy mtime, and SHA-256 index.
- `attribution-report.md` - initial attribution matrix and current classification.
- `attribution-report-addendum.md` - Phase 2A residual narrowing: 32-ms triple-mtime cluster + primary-source-attested >=413-row data-loss disclosure.
- `restore-candidates.local.md` - quarantine-only local restore-candidate validation summary.
- `restore-candidates.notion.md` - read-only Notion mirror inventory for bounded delta/provenance analysis.
- `incident-issue-body.md` - hypothesis-first GitHub issue body.

Scope boundaries:

- No branch switch occurred before the raw packet was copied and hashed.
- No live restore/reset has been performed.
- SQLite inspection was run only against quarantined copies in the private packet.
- Raw operator history and raw machine exports remain private.
- GitHub issue #149 is the primary incident record. Recovery execution and hardening remain separate follow-ups.
