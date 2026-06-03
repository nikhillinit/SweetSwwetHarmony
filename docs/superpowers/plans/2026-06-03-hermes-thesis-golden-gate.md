# Hermes-Integrated Thesis Golden-Set Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a required CI gate that protects thesis-classification behavior on every PR, makes the keyless-CI "no live eval" case an *auditable, blocking* decision instead of a silent pass, and routes the eval through a Hermes execute-capable executor when no LLM API key is present.

**Architecture:** Extend the existing eval surfaces (`scripts/run_thesis_llm_eval_gate.py`, `utils/thesis_eval_gate.py`, `tests/fixtures/thesis_llm_golden_set.*`) rather than building parallel ones. Add four small CI scripts — a sensitive-change detector, a **Hermes eval-mode resolver** (the keystone), a gate-artifact checker that *consumes* the resolver decision, and an advisory Hermes deliberation cross-check — wired by one always-visible workflow. Hermes integration is **advisory first** (route-for-decision + doctor preflight + ledger recording), graduating to required, mirroring the control-plane stack's scope-guard philosophy.

**Tech Stack:** Python 3.11, pytest, GitHub Actions, Hermes (`integrations/hermes` via `python -m ops.cli hermes route|providers|task`), existing thesis golden-set fixtures. Windows/PowerShell dev host; ASCII-only docs.

**Supersedes:** Task 2 / PR 2 of `docs/superpowers/plans/2026-05-27-sweetswwetharmony-control-plane-hardening.md`. Reuses that plan's sensitive-path list, label model, and acceptance intent; adds the Hermes layer that closes the keyless-CI gap (RedTeam findings F1/F2) and a baseline re-validation step (F6). Recommend landing the verified merge-direction fix (that plan's Task 4a) in parallel — it is independent of this gate.

---

## Repo Reality Confirmed Before Writing

- `scripts/run_thesis_llm_eval_gate.py` resolves keys via `GOOGLE_API_KEY` then `GEMINI_API_KEY` and sets `effective_skip_llm = skip_llm or not api_key`. CLI flags: `--dataset`, `--output`, `--rebaseline-output`, `--baseline-summary`.
- `tests/fixtures/thesis_llm_golden_set.manifest.json` keys: `dataset_fingerprint` (sha256), `benchmark_version` ("2026-04-05.v2"), `sample_count` (64), `dataset_path`.
- `hermes route --json` prints `RoutingPlan.to_dict()` (`integrations/hermes/router.py:55`): `{"task","phase","recommendedExecutor","risk","specialist","score","matchedKeywords","alternatives","manualModel","lane",{...},"executorMetadata":{<name>:{"enabled":bool,"supportsExecute":bool}}}`.
- `hermes providers doctor --json` prints `ProviderReport.to_dict()` (`integrations/hermes/providers.py:91`); read-only, no network probes (per `docs/runbooks/hermes.md`).
- `hermes task deliberate` accepts `--panel`, `--rounds`, `--synthesizer`, `--coding-pair` (`tests/ops/hermes/test_cli.py`).
- `scripts/ci/` exists (e.g. `check_doc_artifacts.py`); CI style is in `.github/workflows/regression-gate.yml` and `local-artifact-validation.yml`.
- Module-form execution is mandatory: `python -m scripts.run_thesis_llm_eval_gate --help` (direct path execution breaks imports).
- Approval is by **live GitHub label**, never PR body or author env vars.
- Always-on hooks now exist: `guard_signals_db` (PreToolUse) and advisory `ruff_format_lint` (PostToolUse). Neither affects this work; new `.py` files will get advisory ruff output you should clean up.

## File Structure

| File | Responsibility |
|------|----------------|
| `scripts/ci/detect_thesis_sensitive_changes.py` | Decide if a PR's changed files touch thesis-sensitive paths. Pure file-list logic. |
| `scripts/ci/resolve_thesis_eval_mode.py` | **Keystone.** Decide `gold` / `hermes` / `structural` mode and emit an auditable decision JSON. Calls `hermes route --json`. |
| `scripts/ci/check_thesis_gate_artifact.py` | Enforce the gate: fingerprint match, accuracy floor, label-gated relabeling, and **fail on structural-blocked sensitive PRs without approval**. Consumes the resolver decision. |
| `scripts/ci/thesis_deliberation_check.py` | Advisory Hermes deliberation cross-check on borderline rows. Calls `hermes task deliberate`. Never blocks in v1. |
| `.github/workflows/thesis-golden-gate.yml` | Always-visible `Thesis Golden Set Gate` check. No path filters. Wires the above; uploads artifacts. |
| `docs/runbooks/thesis-golden-gate.md` | Operator runbook: modes, dispatch, label flow, baseline promotion. |
| `docs/evals/thesis-golden-gate-baseline.md` | Records the current baseline + the re-validation that it is still valid post-restore. |

---

### Task 1: Thesis Sensitive-Change Detector

**Files:**
- Create: `scripts/ci/detect_thesis_sensitive_changes.py`
- Test: `tests/ci/test_detect_thesis_sensitive_changes.py`

- [ ] **Step 1.1: Write the failing test**

```python
# tests/ci/test_detect_thesis_sensitive_changes.py
from scripts.ci.detect_thesis_sensitive_changes import is_sensitive, THESIS_SENSITIVE_PATTERNS


def test_thesis_filter_path_is_sensitive():
    assert is_sensitive(["consumer/thesis_filter/matcher.py"]) is True


def test_golden_set_fixture_is_sensitive():
    assert is_sensitive(["tests/fixtures/thesis_llm_golden_set.jsonl"]) is True


def test_unrelated_path_is_not_sensitive():
    assert is_sensitive(["dashboard/app.py", "README.md"]) is False


def test_empty_changeset_is_not_sensitive():
    assert is_sensitive([]) is False


def test_patterns_are_nonempty():
    assert len(THESIS_SENSITIVE_PATTERNS) >= 5
```

- [ ] **Step 1.2: Run it to verify it fails**

Run: `python -m pytest tests/ci/test_detect_thesis_sensitive_changes.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.ci.detect_thesis_sensitive_changes`.

- [ ] **Step 1.3: Write the minimal implementation**

```python
# scripts/ci/detect_thesis_sensitive_changes.py
"""Detect whether a PR's changed files touch thesis-sensitive paths."""
from __future__ import annotations

import argparse
import fnmatch
import sys

THESIS_SENSITIVE_PATTERNS = [
    "consumer/thesis_filter/*",
    "consumer/thesis_filter/**",
    "utils/thesis_*.py",
    "scripts/*thesis*.py",
    "scripts/ci/*thesis*.py",
    "tests/fixtures/thesis_llm_golden_set*",
    "artifacts/thesis_diagnostics/candidate_v3*",
    ".github/workflows/thesis-golden-gate.yml",
    ".github/workflows/thesis-eval.yml",
]


def is_sensitive(changed_files: list[str]) -> bool:
    for path in changed_files:
        norm = path.replace("\\", "/")
        for pattern in THESIS_SENSITIVE_PATTERNS:
            if fnmatch.fnmatch(norm, pattern):
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files", required=True,
                        help="Newline- or comma-separated changed file paths.")
    args = parser.parse_args(argv)
    raw = args.changed_files.replace(",", "\n")
    files = [line.strip() for line in raw.splitlines() if line.strip()]
    sensitive = is_sensitive(files)
    print("true" if sensitive else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `python -m pytest tests/ci/test_detect_thesis_sensitive_changes.py -q`
Expected: PASS (5 passed).

- [ ] **Step 1.5: Commit**

```powershell
git add scripts/ci/detect_thesis_sensitive_changes.py tests/ci/test_detect_thesis_sensitive_changes.py
git commit -m "feat(ci): add thesis sensitive-change detector"
```

---

### Task 2: Hermes Eval-Mode Resolver (keystone)

Replaces the silent `no API key -> skip` behavior with an **auditable** decision among `gold` (real classifier), `hermes` (route to an execute-capable executor), or `structural` (live eval blocked). Exit code is always 0 — this resolves; `check_thesis_gate_artifact.py` enforces.

**Files:**
- Create: `scripts/ci/resolve_thesis_eval_mode.py`
- Test: `tests/ci/test_resolve_thesis_eval_mode.py`

- [ ] **Step 2.1: Write the failing test**

```python
# tests/ci/test_resolve_thesis_eval_mode.py
from scripts.ci.resolve_thesis_eval_mode import decide, route_executor


def test_gold_mode_when_google_key_present():
    d = decide({"GOOGLE_API_KEY": "x"}, plan={})
    assert d["mode"] == "gold"


def test_gold_mode_when_gemini_key_present():
    d = decide({"GEMINI_API_KEY": "x"}, plan={})
    assert d["mode"] == "gold"


def test_hermes_mode_when_executor_supports_execute():
    plan = {
        "recommendedExecutor": "codex",
        "phase": "production",
        "risk": "low",
        "executorMetadata": {"codex": {"enabled": True, "supportsExecute": True}},
    }
    d = decide({}, plan=plan)
    assert d["mode"] == "hermes"
    assert d["executor"] == "codex"


def test_structural_mode_when_executor_cannot_execute():
    plan = {
        "recommendedExecutor": "gemini",
        "executorMetadata": {"gemini": {"enabled": True, "supportsExecute": False}},
    }
    d = decide({}, plan=plan)
    assert d["mode"] == "structural"


def test_structural_mode_when_no_plan():
    assert decide({}, plan={})["mode"] == "structural"


def test_route_executor_returns_empty_on_nonzero_exit():
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    def runner(*_a, **_k):
        return FakeProc()

    assert route_executor(runner=runner) == {}


def test_route_executor_parses_json():
    class FakeProc:
        returncode = 0
        stdout = '{"recommendedExecutor": "codex"}'
        stderr = ""

    def runner(*_a, **_k):
        return FakeProc()

    assert route_executor(runner=runner)["recommendedExecutor"] == "codex"
```

- [ ] **Step 2.2: Run it to verify it fails**

Run: `python -m pytest tests/ci/test_resolve_thesis_eval_mode.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.ci.resolve_thesis_eval_mode`.

- [ ] **Step 2.3: Write the minimal implementation**

```python
# scripts/ci/resolve_thesis_eval_mode.py
"""Resolve, auditably, how the thesis golden-set gate should run.

Modes:
  gold        - GOOGLE_API_KEY/GEMINI_API_KEY present; run the real classifier.
  hermes      - no API key, but `hermes route` yields an execute-capable executor.
  structural  - no API key and no execute-capable executor; live eval is BLOCKED.

Emits a decision JSON so the "no live eval" case is auditable, never a silent green.
Exit code is always 0; enforcement lives in check_thesis_gate_artifact.py.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

API_KEY_ENV = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
ROUTE_TASK = "thesis golden-set eval"


def resolve_api_key(env: dict[str, str]) -> str | None:
    for name in API_KEY_ENV:
        value = env.get(name)
        if value:
            return value
    return None


def route_executor(runner=subprocess.run) -> dict:
    """Return the parsed `hermes route --json` plan, or {} on any failure."""
    try:
        proc = runner(
            [sys.executable, "-m", "ops.cli", "hermes", "route",
             "--json", "--phase", "production", "--task", ROUTE_TASK],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return {}
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def decide(env: dict[str, str], plan: dict) -> dict:
    if resolve_api_key(env):
        return {"mode": "gold", "executor": None,
                "reason": "LLM API key present; running real classifier."}
    executor = plan.get("recommendedExecutor")
    meta = (plan.get("executorMetadata") or {}).get(executor or "", {})
    if executor and meta.get("supportsExecute"):
        return {"mode": "hermes", "executor": executor,
                "phase": plan.get("phase"), "risk": plan.get("risk"),
                "reason": f"No API key; Hermes routes to execute-capable '{executor}'."}
    return {"mode": "structural", "executor": None,
            "reason": ("No API key and no execute-capable Hermes executor; "
                       "live eval BLOCKED (structural checks only).")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True,
                        help="Path to write the decision JSON.")
    args = parser.parse_args(argv)
    decision = decide(dict(os.environ), route_executor())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `python -m pytest tests/ci/test_resolve_thesis_eval_mode.py -q`
Expected: PASS (7 passed).

- [ ] **Step 2.5: Smoke the real route call (no providers executed)**

Run: `python scripts/ci/resolve_thesis_eval_mode.py --out artifacts/thesis_diagnostics/eval-mode.json`
Expected: prints a one-line decision JSON; exit 0. (Mode depends on local env; `route` executes no providers.)

- [ ] **Step 2.6: Commit**

```powershell
git add scripts/ci/resolve_thesis_eval_mode.py tests/ci/test_resolve_thesis_eval_mode.py
git commit -m "feat(ci): add Hermes-routed thesis eval-mode resolver"
```

---

### Task 3: Gate Artifact Checker

Enforces the gate and — critically — **fails a thesis-sensitive PR whose decision is `structural` unless a maintainer approval label is present**, closing the silent-skip gap.

**Files:**
- Create: `scripts/ci/check_thesis_gate_artifact.py`
- Test: `tests/ci/test_check_thesis_gate_artifact.py`

- [ ] **Step 3.1: Write the failing test**

```python
# tests/ci/test_check_thesis_gate_artifact.py
import json
from pathlib import Path

import pytest

from scripts.ci.check_thesis_gate_artifact import GateError, check_gate

MANIFEST_FP = "536e081d4ceec265a27cf037f7bb33ae88831895554bf8ebdbc29bf578d392fc"


def _manifest(tmp_path: Path, fingerprint: str = MANIFEST_FP) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"dataset_fingerprint": fingerprint, "sample_count": 64}),
                 encoding="utf-8")
    return p


def test_structural_sensitive_without_label_fails(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "structural"}), encoding="utf-8")
    with pytest.raises(GateError, match="structural"):
        check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
                   gate_output_path=None, sensitive=True, labels=[],
                   min_accuracy=0.9)


def test_structural_sensitive_with_dispatch_label_passes(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "structural"}), encoding="utf-8")
    # thesis-label-drift-approved authorizes proceeding without live eval
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=None, sensitive=True,
               labels=["thesis-label-drift-approved"], min_accuracy=0.9)


def test_non_sensitive_structural_passes(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "structural"}), encoding="utf-8")
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=None, sensitive=False, labels=[], min_accuracy=0.9)


def test_fingerprint_mismatch_fails(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"dataset_fingerprint": "deadbeef", "accuracy": 1.0}),
                    encoding="utf-8")
    with pytest.raises(GateError, match="fingerprint"):
        check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
                   gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)


def test_accuracy_below_floor_fails(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"dataset_fingerprint": MANIFEST_FP, "accuracy": 0.5}),
                    encoding="utf-8")
    with pytest.raises(GateError, match="accuracy"):
        check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
                   gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)


def test_live_eval_above_floor_passes(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"dataset_fingerprint": MANIFEST_FP, "accuracy": 0.95}),
                    encoding="utf-8")
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)
```

- [ ] **Step 3.2: Run it to verify it fails**

Run: `python -m pytest tests/ci/test_check_thesis_gate_artifact.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.ci.check_thesis_gate_artifact`.

- [ ] **Step 3.3: Write the minimal implementation**

```python
# scripts/ci/check_thesis_gate_artifact.py
"""Enforce the thesis golden-set gate from resolver decision + gate output."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Label that authorizes merging a thesis-sensitive PR without a live eval.
STRUCTURAL_OVERRIDE_LABEL = "thesis-label-drift-approved"


class GateError(Exception):
    """Raised when the gate must block the PR."""


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_gate(
    *,
    decision_path: Path,
    manifest_path: Path,
    gate_output_path: Path | None,
    sensitive: bool,
    labels: list[str],
    min_accuracy: float,
) -> None:
    decision = _load(decision_path)
    mode = decision.get("mode")

    if mode == "structural":
        if sensitive and STRUCTURAL_OVERRIDE_LABEL not in labels:
            raise GateError(
                "Thesis-sensitive change with structural-only eval (no API key, "
                f"no execute-capable Hermes executor). Apply '{STRUCTURAL_OVERRIDE_LABEL}' "
                "after a maintainer-dispatched live eval, or provide an executor/key.")
        return  # non-sensitive, or approved: structural checks suffice

    # Live eval (gold or hermes): require gate output, fingerprint, accuracy floor.
    if gate_output_path is None:
        raise GateError(f"mode={mode} requires a gate output artifact.")
    gate = _load(gate_output_path)
    manifest = _load(manifest_path)
    if gate.get("dataset_fingerprint") != manifest.get("dataset_fingerprint"):
        raise GateError("dataset_fingerprint mismatch between gate output and manifest.")
    accuracy = gate.get("accuracy")
    if accuracy is None or accuracy < min_accuracy:
        raise GateError(f"accuracy {accuracy} below floor {min_accuracy}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gate-output", type=Path, default=None)
    parser.add_argument("--sensitive", choices=["true", "false"], required=True)
    parser.add_argument("--labels", default="", help="Comma-separated live GitHub labels.")
    parser.add_argument("--min-accuracy", type=float, default=0.9)
    args = parser.parse_args(argv)
    labels = [s.strip() for s in args.labels.split(",") if s.strip()]
    try:
        check_gate(
            decision_path=args.decision,
            manifest_path=args.manifest,
            gate_output_path=args.gate_output,
            sensitive=args.sensitive == "true",
            labels=labels,
            min_accuracy=args.min_accuracy,
        )
    except GateError as exc:
        print(f"THESIS GATE BLOCK: {exc}", file=sys.stderr)
        return 1
    print("THESIS GATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `python -m pytest tests/ci/test_check_thesis_gate_artifact.py -q`
Expected: PASS (6 passed).

- [ ] **Step 3.5: Commit**

```powershell
git add scripts/ci/check_thesis_gate_artifact.py tests/ci/test_check_thesis_gate_artifact.py
git commit -m "feat(ci): enforce thesis gate from resolver decision"
```

---

### Task 4: Advisory Hermes Deliberation Cross-Check

Robustness layer (RedTeam F2): when a live eval runs and an executor panel is available, ask a Hermes deliberation panel to cross-check borderline classifications. **Advisory only in v1** — it writes a summary and always exits 0. It graduates to a blocking signal in the control-plane stack's Task 6.

**Files:**
- Create: `scripts/ci/thesis_deliberation_check.py`
- Test: `tests/ci/test_thesis_deliberation_check.py`

- [ ] **Step 4.1: Write the failing test**

```python
# tests/ci/test_thesis_deliberation_check.py
from scripts.ci.thesis_deliberation_check import build_deliberation_argv, summarize


def test_build_argv_uses_panel_and_synthesizer():
    argv = build_deliberation_argv(panel="codex,kimi", rounds=2, synthesizer="codex",
                                   task_text="cross-check borderline thesis rows")
    assert argv[:4] == ["-m", "ops.cli", "hermes", "task"]
    assert "deliberate" in argv
    assert "--panel" in argv and "codex,kimi" in argv
    assert "--synthesizer" in argv and "codex" in argv


def test_summarize_reports_advisory_and_never_raises():
    s = summarize(returncode=1, stdout="", stderr="panel unavailable")
    assert s["advisory"] is True
    assert s["ran"] is False


def test_summarize_marks_ran_on_success():
    s = summarize(returncode=0, stdout="consensus reached", stderr="")
    assert s["ran"] is True
    assert s["advisory"] is True
```

- [ ] **Step 4.2: Run it to verify it fails**

Run: `python -m pytest tests/ci/test_thesis_deliberation_check.py -q`
Expected: FAIL — `ModuleNotFoundError: scripts.ci.thesis_deliberation_check`.

- [ ] **Step 4.3: Write the minimal implementation**

```python
# scripts/ci/thesis_deliberation_check.py
"""Advisory Hermes deliberation cross-check for the thesis gate (v1: never blocks)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def build_deliberation_argv(*, panel: str, rounds: int, synthesizer: str,
                            task_text: str) -> list[str]:
    return [
        "-m", "ops.cli", "hermes", "task", "deliberate",
        "--task-text", task_text,
        "--panel", panel,
        "--rounds", str(rounds),
        "--synthesizer", synthesizer,
    ]


def summarize(*, returncode: int, stdout: str, stderr: str) -> dict:
    return {
        "advisory": True,
        "ran": returncode == 0,
        "returncode": returncode,
        "stdout_tail": (stdout or "")[-500:],
        "stderr_tail": (stderr or "")[-500:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", default="codex,kimi")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--synthesizer", default="codex")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    cmd = [sys.executable] + build_deliberation_argv(
        panel=args.panel, rounds=args.rounds, synthesizer=args.synthesizer,
        task_text="cross-check borderline thesis golden-set classifications")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        summary = summarize(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    except Exception as exc:  # advisory: never fail the gate
        summary = summarize(returncode=1, stdout="", stderr=str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"advisory": True, "ran": summary["ran"]}))
    return 0  # advisory: always succeed


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `python -m pytest tests/ci/test_thesis_deliberation_check.py -q`
Expected: PASS (3 passed).

- [ ] **Step 4.5: Commit**

```powershell
git add scripts/ci/thesis_deliberation_check.py tests/ci/test_thesis_deliberation_check.py
git commit -m "feat(ci): add advisory Hermes deliberation cross-check"
```

---

### Task 5: Always-Visible Gate Workflow

**Files:**
- Create: `.github/workflows/thesis-golden-gate.yml`
- Test: `tests/ci/test_thesis_golden_gate_workflow.py`

- [ ] **Step 5.1: Write the failing test (workflow contract)**

```python
# tests/ci/test_thesis_golden_gate_workflow.py
from pathlib import Path

import yaml

WF = Path(".github/workflows/thesis-golden-gate.yml")


def test_workflow_exists_and_parses():
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    assert data["name"] == "Thesis Golden Set Gate"


def test_no_top_level_path_filters():
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    # `on` is parsed by PyYAML as boolean True key; handle both.
    triggers = data.get("on") or data.get(True)
    pr = triggers["pull_request"]
    assert pr is None or "paths" not in pr  # must run on every PR
    assert "workflow_dispatch" in triggers


def test_runs_resolver_detector_and_checker():
    text = WF.read_text(encoding="utf-8")
    assert "resolve_thesis_eval_mode.py" in text
    assert "detect_thesis_sensitive_changes.py" in text
    assert "check_thesis_gate_artifact.py" in text
    assert "hermes providers doctor" in text
```

- [ ] **Step 5.2: Run it to verify it fails**

Run: `python -m pytest tests/ci/test_thesis_golden_gate_workflow.py -q`
Expected: FAIL — `FileNotFoundError` for the workflow.

- [ ] **Step 5.3: Write the workflow**

```yaml
# .github/workflows/thesis-golden-gate.yml
name: Thesis Golden Set Gate

on:
  pull_request:
  workflow_dispatch:

permissions:
  contents: read
  pull-requests: read

jobs:
  thesis-gate:
    name: Thesis Golden Set Gate
    runs-on: ubuntu-latest
    env:
      MANIFEST: tests/fixtures/thesis_llm_golden_set.manifest.json
      DECISION: artifacts/thesis_diagnostics/eval-mode.json
      GATE_OUT: artifacts/thesis_diagnostics/pr-gate.json
      GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pyyaml

      - name: Hermes provider doctor (read-only preflight)
        run: python -m ops.cli hermes providers doctor --json || true

      - name: Detect thesis-sensitive changes
        id: detect
        run: |
          CHANGED=$(git diff --name-only --diff-filter=ACMR origin/${{ github.base_ref }}...HEAD || true)
          SENSITIVE=$(python scripts/ci/detect_thesis_sensitive_changes.py --changed-files "$CHANGED")
          echo "sensitive=$SENSITIVE" >> "$GITHUB_OUTPUT"

      - name: Resolve eval mode (auditable)
        run: python scripts/ci/resolve_thesis_eval_mode.py --out "$DECISION"

      - name: Read current PR labels (live GitHub state)
        id: labels
        if: github.event_name == 'pull_request'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          LABELS=$(gh api "repos/${GITHUB_REPOSITORY}/issues/${{ github.event.number }}/labels" --jq '[.[].name] | join(",")' || echo "")
          echo "labels=$LABELS" >> "$GITHUB_OUTPUT"

      - name: Run live eval (gold path, key present)
        if: ${{ env.GOOGLE_API_KEY != '' && steps.detect.outputs.sensitive == 'true' }}
        run: |
          python -m scripts.run_thesis_llm_eval_gate \
            --dataset tests/fixtures/thesis_llm_golden_set.jsonl \
            --output "$GATE_OUT" \
            --rebaseline-output artifacts/thesis_diagnostics/pr-rebaseline.json \
            --baseline-summary artifacts/thesis_diagnostics/candidate_v3.summary.json

      - name: Enforce gate
        run: |
          GATE_ARG=""
          if [ -f "$GATE_OUT" ]; then GATE_ARG="--gate-output $GATE_OUT"; fi
          python scripts/ci/check_thesis_gate_artifact.py \
            --decision "$DECISION" \
            --manifest "$MANIFEST" \
            --sensitive "${{ steps.detect.outputs.sensitive }}" \
            --labels "${{ steps.labels.outputs.labels }}" \
            $GATE_ARG

      - name: Upload gate artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: thesis-golden-gate
          path: artifacts/thesis_diagnostics/
          if-no-files-found: warn
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `python -m pytest tests/ci/test_thesis_golden_gate_workflow.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5.5: Full local composition smoke**

```powershell
python -m pytest tests/ci/ -q
python scripts/ci/resolve_thesis_eval_mode.py --out artifacts/thesis_diagnostics/eval-mode.json
python scripts/ci/check_thesis_gate_artifact.py --decision artifacts/thesis_diagnostics/eval-mode.json --manifest tests/fixtures/thesis_llm_golden_set.manifest.json --sensitive false --labels ""
git diff --check
```
Expected: tests pass; checker prints `THESIS GATE OK` for a non-sensitive PR.

- [ ] **Step 5.6: Commit**

```powershell
git add .github/workflows/thesis-golden-gate.yml tests/ci/test_thesis_golden_gate_workflow.py
git commit -m "ci: add always-visible thesis golden set gate workflow"
```

---

### Task 6: Runbook, Baseline Doc, and Baseline Re-Validation (F6)

**Files:**
- Create: `docs/runbooks/thesis-golden-gate.md`
- Create: `docs/evals/thesis-golden-gate-baseline.md`

- [ ] **Step 6.1: Re-validate the baseline is still current (do NOT assume)**

`candidate_v3` is the 2026-04-03 (thesis v1.6.0) baseline, pre-incident/pre-restore. Confirm it still reflects current behavior before trusting it as the gate baseline.

Run (requires `GOOGLE_API_KEY`; otherwise record that re-validation is pending a maintainer dispatch):

```powershell
python scripts/thesis_diagnostic_runner.py `
  --dataset tests/fixtures/thesis_llm_golden_set.jsonl `
  --output-dir artifacts/thesis_diagnostics `
  --run-id candidate_v3_revalidate_20260603 `
  --compare-against artifacts/thesis_diagnostics/candidate_v3.jsonl `
  --temperature 0
```
Expected: drift vs `candidate_v3` is within tolerance, OR a follow-up to re-baseline (`baseline-promotion-approved`) is filed. Record the result in the baseline doc.

- [ ] **Step 6.2: Write the runbook**

Create `docs/runbooks/thesis-golden-gate.md` documenting: the three eval modes and how `resolve_thesis_eval_mode.py` chooses them; that `structural` on a sensitive PR is BLOCKED and how a maintainer clears it (dispatch a live eval, then apply `thesis-label-drift-approved`); the Hermes route/doctor/deliberate commands used; the accuracy floor and how to change it; and that approval is read from live GitHub labels, not the PR body. Include a rollback section (mark the workflow advisory by removing it from branch protection).

- [ ] **Step 6.3: Write the baseline doc**

Create `docs/evals/thesis-golden-gate-baseline.md` recording: current baseline (`candidate_v3`, manifest `dataset_fingerprint` `536e081d…`, `benchmark_version 2026-04-05.v2`, 64 samples), the Step 6.1 re-validation result, the accuracy floor (default `0.9`), and the promotion flow (`baseline-promotion-approved` + CODEOWNER review).

- [ ] **Step 6.4: Verify docs and commit**

```powershell
python scripts/ci/check_doc_artifacts.py docs
git diff --check
git add docs/runbooks/thesis-golden-gate.md docs/evals/thesis-golden-gate-baseline.md artifacts/thesis_diagnostics/
git commit -m "docs: thesis golden gate runbook and baseline"
```

---

## Global Rules (every task)

- [ ] Branch from `main` with a `codex/` prefix (e.g. `codex/thesis-golden-gate`); do not work on `main`.
- [ ] `git status --short --branch` before editing and before staging. Pre-existing dirty files (`state/collectors.json`, untracked `artifacts/keepalive/*`, `.omx/*`) must NOT be swept into commits — `git add` only the listed files.
- [ ] Failing test before implementation; verify RED; then minimal code; verify GREEN; commit.
- [ ] Module-form execution for scripts under packages: `python -m scripts.run_thesis_llm_eval_gate`.
- [ ] Approval = live GitHub labels via `gh api repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/labels`. If a label is applied after CI starts, rerun the job.
- [ ] No top-level path filters on this required workflow; it appears on every PR and no-ops cheaply when not sensitive.
- [ ] `git diff --check` before each commit. Commit footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- [ ] Create labels if missing: `gh label create thesis-label-drift-approved …` and `gh label create baseline-promotion-approved …` (see the 2026-05-27 stack plan's "Manual Setup").

## Hermes Integration Summary (how this leverages existing capability)

| Capability (existing) | Used by | Effect |
|---|---|---|
| `hermes providers doctor --json` (read-only, no network) | Workflow preflight | Fail-fast visibility on available executors; cannot flake on network. |
| `hermes route --json` (`recommendedExecutor` + `executorMetadata.supportsExecute`) | `resolve_thesis_eval_mode.py` | Turns the keyless-CI skip into an **auditable** gold/hermes/structural decision. |
| `hermes task deliberate` (panel/rounds/synthesizer) | `thesis_deliberation_check.py` | Multi-model robustness cross-check (advisory v1; blocking later). |
| Hermes ledger + `Hermes Ledger Audit` workflow | Follow-up (see below) | Record gate runs for reconciliation once the route/run path is execute-graduated. |

**Deferred to the control-plane stack's Task 6 (not in v1):** graduate the Hermes deliberation cross-check from advisory to blocking; route the *behavioral* eval execution through a Hermes executor (Codex/Gemini-CLI) so keyless CI runs a real classification eval, not just an auditable structural decision — this needs a thesis-eval executor adapter and is scoped separately to avoid coupling this gate to in-flux Hermes execute paths (RedTeam F5).

## Definition of Done

- `Thesis Golden Set Gate` appears on every PR; non-thesis PRs pass cheaply.
- Thesis-sensitive PRs with no live eval are **blocked** (auditable `structural` decision), clearable only by maintainer dispatch + `thesis-label-drift-approved`.
- Live eval (gold or hermes) enforces `dataset_fingerprint` match + accuracy floor.
- Hermes doctor preflight + routed decision are recorded as uploaded artifacts.
- Baseline re-validation (F6) is recorded; deliberation cross-check is advisory.
- Every task: failing test first, real code, `git diff --check`, scoped commit.

## Self-Review

- **Spec coverage:** F1 (silent skip) → Tasks 2+3 (auditable decision + blocking structural-on-sensitive). F2 (single-model flap) → Task 4 (advisory deliberation; blocking deferred). F6 (stale baseline) → Task 6.1. Original Task-2 acceptance (required check, cheap on non-thesis, fixture relabel needs approval, dispatch runs full gate) → Tasks 1/3/5. F3/F4/F5 are cross-plan (resequence 4a; route mutations through Hermes; advisory-first) — noted in the supersedes/Hermes-summary sections, not coded here (correctly out of this subsystem's scope).
- **Placeholder scan:** none — every code/test step has runnable bodies; field names (`dataset_fingerprint`, `recommendedExecutor`, `executorMetadata.supportsExecute`) verified against source.
- **Type consistency:** `decide(env, plan)` and `route_executor(runner=)` signatures match between Task 2 impl and its tests; `check_gate(...)` keyword args match between Task 3 impl and tests; `build_deliberation_argv`/`summarize` match in Task 4. `GateError` is defined in Task 3 and imported by its test.
- **Known execution caveat:** `hermes route`/`doctor` JSON shapes were read from `integrations/hermes/router.py` and `providers.py` on 2026-06-03; re-confirm before implementing, since Hermes is under active Track-A hardening.
