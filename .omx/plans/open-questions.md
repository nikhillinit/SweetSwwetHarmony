# Open Questions

- [x] Should `keepalive_monitor_ping.py` preserve backward compatibility with raw watchdog artifacts, or is composite-only input an intentional breaking change with a controlled redeploy path?
  - Resolved: preserve backward compatibility. `keepalive_monitor_ping.py` now accepts raw watchdog artifacts as `raw_watchdog_compat`, while generated runners should still post composite artifacts.
- [ ] After the read-only reconciliation pass, does the vault's open salvage/recovery language still reflect a real corpus problem, or is it primarily stale status text?
- [ ] Should the routing-layer-as-SoR ADR run immediately after the read-only reconciliation lane, or in parallel with the two build-strategy ADR follow-ons?
