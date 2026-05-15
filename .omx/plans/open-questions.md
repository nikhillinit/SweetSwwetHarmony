# Open Questions

- [x] Should `keepalive_monitor_ping.py` preserve backward compatibility with raw watchdog artifacts, or is composite-only input an intentional breaking change with a controlled redeploy path?
  - Resolved: preserve backward compatibility. `keepalive_monitor_ping.py` now accepts raw watchdog artifacts as `raw_watchdog_compat`, while generated runners should still post composite artifacts.
- [ ] After the read-only reconciliation pass, does the vault's open salvage/recovery language still reflect a real corpus problem, or is it primarily stale status text?
- [ ] Should the routing-layer-as-SoR ADR run immediately after the read-only reconciliation lane, or in parallel with the two build-strategy ADR follow-ons?
- [ ] For `process --dry-run`, should `SignalStore(read_only=True)` fail fast when the target DB is missing or schema-incompatible, rather than creating/bootstraping anything?
- [ ] When the GitHub issue for `fix/process-dry-run-readonly` is opened, should its body embed the DB repair runbook gates directly or link to `.omx/plans/process-dry-run-readonly-ralplan-dr-20260515.md` as the canonical operator artifact?
