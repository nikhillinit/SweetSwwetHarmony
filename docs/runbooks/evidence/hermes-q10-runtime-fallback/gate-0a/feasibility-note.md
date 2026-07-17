# Platform Feasibility Gate 0A — provider-establishment race

**Verdict: GO for PR 2**, with a load-bearing eligibility constraint (below).

- Plan: `.omx/plans/q10-runtime-fallback-promotion-ralplan-dr-20260716.md`, section
  "Platform Feasibility Gate 0A".
- Grounded on: `origin/main` @ `5af3f257` (PR #314 merge — the owned
  `integrations/process_runtime.py` boundary).
- Branch: `feasibility/q10-gate-0a`.
- Deterministic tests: `tests/ops/test_process_establishment_race_gate0a.py`.
- Captured: 2026-07-16.

## What Gate 0A had to prove

Runtime fallback is only ever a bounded contingency for a provider that becomes
*unspawnable* in the TOCTOU window between a green route gate (the resolver
finding the executable) and the real process-creation call. Before investing in
PR 2, prove — deterministically, with no privileged host mutation and no
*production* fault-injection seam — that:

1. `process_runtime.resolve_executable` genuinely finds a disposable shim on a
   scratch `PATH`;
2. if that shim is atomically invalidated *after* resolution but *before* the
   real `create_subprocess` call, `run_process` maps it to typed
   `PROVIDER_NOT_ESTABLISHED`;
3. no provider code runs (a mutation sentinel the shim would write is absent) and
   any transient POSIX `exec` bootstrap is reaped (the loop stays healthy).

## Method (no production seam)

- **Disposable shim on a scratch PATH.** POSIX: a `#!/bin/sh` script that
  `touch`es a mutation sentinel, `chmod 0o755`. Windows direct-exec: a disposable
  copy of the base interpreter (`sys.base_prefix/python.exe`) invoked with `-c` to
  write the sentinel. Windows `.cmd`: a batch shim that writes the sentinel.
  `PATH` is monkeypatched per-test; the resolver reads it via `shutil.which`.
- **Test-only synchronization barrier.** A pytest monkeypatch wraps the module's
  own `_spawn_owned` seam (entered *after* the caller resolved `argv[0]` and
  `run_process` decided the launch form, but *before* the real
  `create_subprocess`). It pauses on spawn entry while a sibling thread performs
  the atomic invalidation (`os.replace` rename-away, or `chmod 000`), then calls
  through to the **real** spawn. The establishment failure is therefore a genuine
  OS `exec` failure on a now-missing/again-inaccessible path, never a synthetic
  injection. **No production fault-injection env var or hook was added.**
- **Scope.** Operates purely at the owned-boundary layer. No ledger, routing
  config, Hermes policy, or canonical artifact is touched (`run_process` has no
  ledger). The "scratch ledger" the plan mentions is moot here because this gate
  does not exercise Hermes routing.
- **Non-vacuity guards.** Each race test is anchored by (a) a control that proves
  the shim is resolvable AND genuinely runs + writes its sentinel, and (b) an
  assertion that the real spawn was attempted exactly once
  (`race.spawn_calls == 1`). The Windows `.cmd` crux test was additionally proven
  live by temporarily flipping its decisive assertion to the naive-wrong
  expectation (`PROVIDER_NOT_ESTABLISHED`) and watching it fail
  (`outcome=COMPLETED, exit_code=1, stderr="...is not recognized..."`).

## Results — validated on both OSes, on real hardware

| Launch form | Resolver finds it | Race outcome | Provider/sentinel ran | Attests not-established? |
|---|---|---|---|---|
| POSIX exec — vanished (ENOENT) | yes | `PROVIDER_NOT_ESTABLISHED` | no | **yes** |
| POSIX exec — perm revoked (EACCES) | yes | `PROVIDER_NOT_ESTABLISHED` (loop stays healthy; direct reap proof in PR 1) | no | **yes** |
| Windows `.exe` direct-exec — vanished (WinError 2) | yes | `PROVIDER_NOT_ESTABLISHED` | no | **yes** |
| Windows `.cmd` shell-launch — vanished | yes | `COMPLETED`, exit=1 ("not recognized") | no (cmd.exe couldn't find it) | **NO** |

- Windows host (win32): 4 Windows tests passed, 3 POSIX skipped.
- Linux (WSL Ubuntu, Python 3.12.3): 3 POSIX tests passed, 4 Windows skipped.
- Focused Windows CI job runs the Windows cases; the Ubuntu CI job runs the POSIX
  cases (the file was added to `PROCESS_BOUNDARY_SUITE` in `regression-gate.yml`).

## The Windows `.cmd` crux and its decision

A `.cmd`/`.bat` resolves and launches via `create_subprocess_shell(cmd.exe ...)`.
A *missing* `.cmd` is a `cmd.exe` non-zero **exit** (outcome `COMPLETED`), not a
spawn `OSError`. That is **indistinguishable** from a provider that ran and exited
non-zero, so the boundary cannot — and must not — infer not-established from the
`cmd.exe` exit code or output.

**Decision (input to PR 2 and PR 3):** the Windows `.cmd`/`.bat` launch form is
**INELIGIBLE** for spawn-only fallback. Only **direct-exec** launches can
structurally attest not-established.

- Eligibility is a property of the **resolved launch form at runtime**, not of
  provider identity. `should_use_shell` is true exactly for a resolved
  `.cmd`/`.bat` on Windows — that is the ineligible form; every direct-exec spawn
  (all POSIX, and `.exe` on Windows) is eligible.
- Given today's install methods on the operator host: **codex** resolves to
  `codex.CMD` (shell) → ineligible; **gemini** → `gemini.CMD` (shell) →
  ineligible *and* deprecated; **kimi-cli** → `kimi-cli.EXE` and **agy** →
  `agy.EXE` (direct-exec) → eligible; all POSIX → eligible. A host that
  npm-installed kimi/agy would resolve `.CMD` and become ineligible, so the
  durable guard is the launch form, not the provider name; `providers doctor`
  should surface the resolved suffix.
- **PR 2** (the sealed provenance envelope) must therefore **record the launch
  form (direct-exec vs shell) as an attestation field** — `ProcessRunResult`
  carries no such field today. **PR 3**'s pure eligibility truth table then gates
  on that attestation plus sealed `PROVIDER_NOT_ESTABLISHED`, never on the
  `cmd.exe` exit code or output.

## Acceptance (plan Gate 0A) — met

- [x] Resolver proven to find the shim on a scratch PATH.
- [x] Race reproducible via a test-only barrier after resolution / before the real
      spawn — no privileged host mutation, no production injection seam.
- [x] Maps to unambiguous typed `PROVIDER_NOT_ESTABLISHED` (direct-exec forms).
- [x] No provider code / mutation sentinel runs. The POSIX EACCES case leaves the
      event loop healthy (a follow-up run completes); the *direct* failed-`exec`
      bootstrap-reap proof lives in PR 1's `tests/ops/test_process_runtime.py`
      (this gate demonstrates liveness, not reaping).
- [x] Expressed on both OSes; the one form that *cannot* express it safely
      (Windows `.cmd`) is documented and declared ineligible rather than papered
      over.

## Reproduce

```bash
# Windows (host) — runs the .exe direct-exec + .cmd crux cases
C:/dev/Harmonic/.venv/Scripts/python.exe -m pytest \
  tests/ops/test_process_establishment_race_gate0a.py -q

# Linux (real POSIX) — runs the ENOENT + EACCES cases
PYTHONPATH="$PWD" python3 -m pytest \
  tests/ops/test_process_establishment_race_gate0a.py -q

# CI: both OS jobs pick it up via PROCESS_BOUNDARY_SUITE in
# .github/workflows/regression-gate.yml
```

## Bottom line

The provider-establishment race is real, reproducible, and safe to detect for
direct-exec launch forms on both OSes. **Proceed to PR 2**, carrying the
launch-form eligibility constraint above. Option 4 (preflight/manual rerouting
only) is *not* triggered — the race was shown. The live master flag
`routing.runtimeFallbackEnabled` remains **false**; this gate enables nothing.
