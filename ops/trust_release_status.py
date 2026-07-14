"""Trust-release milestone status table generator.

Single structured source of truth for the milestone-status table in
``docs/plans/2026-06-15-trust-release/00-strategy.md``. The table used to be
hand-edited (violating the doc's own do-not-hand-edit instruction); now every
row is declared once in :data:`MILESTONES` with explicit provenance, and the
table is emitted by ``python -m ops.cli trust status``.

Provenance model
----------------
- ``manual`` rows carry a status/evidence pair that cannot be derived from
  local artifacts (e.g. operator ratifications, CI-run evidence bundles kept
  in untracked ``.tmp/`` notes). Hand-edits belong HERE, in the config block,
  never in the rendered table.
- ``derived`` rows are corroborated at generation time by cheap, read-only
  local checks: repo-file existence/absence, source-symbol presence, and
  import-level contract checks (e.g. ``REPORT_SCHEMA_VERSION == 2`` and the
  M7 ``TrustStatusCLI`` schema gate). A failed check is surfaced loudly in
  the Provenance column, never silently dropped.

Evidence inputs are strictly read-only:
- hermes ledger-audit artifacts under ``ai-logs/hermes/runs/*/`` are only
  ever opened for reading (or supplied explicitly via ``--evidence``);
- no network access happens by default — GitHub run-conclusion verification
  runs only behind the explicit ``--live-gh`` flag;
- the generator never touches signals.db (any copy).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

GENERATOR_COMMAND = "python -m ops.cli trust status"
CONFIG_MODULE_PATH = "ops/trust_release_status.py"


# ---------------------------------------------------------------------------
# Declarative milestone config (the ONE place hand-edits are allowed)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Milestone:
    key: str
    milestone: str
    status: str
    evidence: str
    verified: str = ""  # when/at-what-SHA a manual row was last verified
    file_checks: Tuple[str, ...] = ()    # repo-relative paths that must exist
    absent_checks: Tuple[str, ...] = ()  # repo-relative paths that must NOT exist
    symbol_checks: Tuple[Tuple[str, str], ...] = ()  # (path, required substring)
    import_checks: Tuple[str, ...] = ()  # names in IMPORT_CHECKS registry
    gh_runs: Tuple[str, ...] = ()        # cited GitHub Actions run ids (verified only with --live-gh)

    @property
    def is_derived(self) -> bool:
        return bool(
            self.file_checks or self.absent_checks
            or self.symbol_checks or self.import_checks
        )


MILESTONES: Tuple[Milestone, ...] = (
    Milestone(
        key="p0-0-gemini-paid-tier",
        milestone="P0-0 Gemini paid tier",
        status="✅ DONE",
        evidence="F6 = 0.9375",
        verified="2026-06-18 @4f19d66",
    ),
    Milestone(
        key="p0-2-gate-hardening",
        milestone="P0-2 Gate hardening PR #271",
        status="✅ DONE",
        evidence="merged `275cded`",
        verified="2026-06-18 @4f19d66",
    ),
    Milestone(
        key="p0-1-db-untrack",
        milestone="P0-1 DB untrack + resolver + Daily Pipeline repoint",
        status="✅ DONE",
        evidence="PRs #272/#273/#275",
        verified="2026-06-18 @4f19d66",
    ),
    Milestone(
        key="db-path-hardening",
        milestone="DB-path hardening: canonical resolution, in-tree guard, Hermes task paths",
        status="\U0001f7e2 MERGED",
        evidence=(
            "PR #281 (`da48563`) + PR #282 (`4f19d66`); local tree contains "
            "`guard_db_path`, `resolve_task_db_path`, `InTreeDatabaseError`, and "
            "follow-up script hardening. CI-green-on-merge remains unconfirmed "
            "because `gh pr view` currently returns HTTP 401 in this environment."
        ),
        symbol_checks=(
            ("storage/db_paths.py", "def guard_db_path"),
            ("storage/db_paths.py", "class InTreeDatabaseError"),
            ("integrations/hermes/tasks/base.py", "def resolve_task_db_path"),
        ),
    ),
    Milestone(
        key="p0-1-recovery",
        milestone="P0-1 bounded recovery + anomaly check + close #149",
        status="\U0001f7e2 RATIFIED (T3-A, 2026-07-14)",
        evidence=(
            "Artifact-based ratification produced by the 2026-07-10 queue: Daily "
            "Pipeline restore-from-replica bootstrap run 29205726663 (manual) + "
            "29231587906 (scheduled) passed restore -> integrity -> watermark-init "
            "-> anomaly gates in production CI and republished `signals-db-latest` "
            "(artifact 8271756749, 90d retention); Litestream Restore Verify green "
            "3x (manual 29205727787, scheduled 29247089443 + 29327958072 "
            "consecutive), each summary.json = integrity ok / schema 53 / 612 rows "
            "/ min 500. Issue #149 was closed 2026-06-17; full bundle in "
            "`.tmp/queue-exec-20260710/execution-status.md` "
            "(\"T3-A RECOVERY-COMPLETE GATE: CLOSED 2026-07-14\")."
        ),
        verified="2026-07-14 @e6ed3e2",
        gh_runs=(
            "29205726663", "29231587906",
            "29205727787", "29247089443", "29327958072",
        ),
    ),
    Milestone(
        key="p0-3-dry-run",
        milestone="P0-3 Dry-run immutability",
        status="\U0001f534 OPEN",
        evidence=(
            "Still needs proof that `process_pending(dry_run=True)` and related "
            "CLI paths do not persist or mutate routed rows."
        ),
        verified="2026-06-18 @4f19d66",
    ),
    Milestone(
        key="m1a-db-anomaly",
        milestone="M1A `db_anomaly.py`",
        status="\U0001f7e1 CODE ON MAIN",
        evidence=(
            "`scripts/db_anomaly.py` exists; current scope is minimal "
            "sha/row-count/size/known-bad/watermark checking. Hot/deep semantic "
            "anomaly checks are follow-on hardening, not a prerequisite to the "
            "minimal trust release."
        ),
        file_checks=("scripts/db_anomaly.py",),
    ),
    Milestone(
        key="m1b-restore-litestream",
        milestone="M1B `restore_db.py` Litestream lifecycle position",
        status="\U0001f7e2 CLOSED (Mode B — intentionally out of scope)",
        evidence=(
            "`restore_backup_with_lock_and_ledger()` performs artifact/local-file "
            "restore only and records `litestream_mode=\"off\"` in every ledger "
            "row; `scripts/litestream_ctrl.py` is quarantined because its "
            "pinned-0.5.2 stop/generations commands cannot safely drive the "
            "lifecycle. S3/R2 cloud-restore durability is now PROVEN by "
            "`.github/workflows/litestream-restore-verify-nightly.yml`: bucket "
            "`harmonic-signals-backup-prod` provisioned + seeded 2026-07-12 (seed "
            "verified by restore-from-S3), two consecutive scheduled green "
            "verifies 2026-07-13/07-14 (runs 29247089443, 29327958072; integrity "
            "ok / schema 53 / 612 rows). Resolved by Phase 5 of the 2026-06-15 "
            "trust-recovery procedure (Litestream capability proof could not "
            "pass: the binary is not installed on the dev host, so Mode A is not "
            "wired). The restore-path maintenance lock-timeout defect (defaulting "
            "to the 5s `LOCK_TIMEOUT_SECONDS` rather than 180s "
            "`MAINTENANCE_LOCK_TIMEOUT_SECONDS`) was already fixed on main by PR "
            "#290 and is covered by regression tests in "
            "`tests/scripts/test_restore_db.py`."
        ),
        verified="2026-07-14 @e6ed3e2",
        gh_runs=("29247089443", "29327958072"),
    ),
    Milestone(
        key="m2-migration-runner",
        milestone="M2 migration with writer coordination",
        status="✅ DONE (removed as unnecessary)",
        evidence=(
            "`scripts/run_migration.py` and `tests/scripts/test_run_migration.py` "
            "deleted on branch `codex/migration-runner-main-audit` after audit + "
            "code review confirmed no live consumer imports the runner (no "
            "module, CLI, CI workflow, Makefile, or scheduler reference). The "
            "runner used a fabricated `schema_version` table and a synthetic v52 "
            "that diverged from production's real v52 (`classification_status` "
            "on `thesis_classifications`). Migration truth on `main` is "
            "`storage.signal_store.MIGRATIONS` + `schema_migrations` with "
            "`CURRENT_SCHEMA_VERSION == max(MIGRATIONS) == 53`; no consumer "
            "needed the runner's columns, so it was dropped per this gate's "
            "\"removed as unnecessary\" branch. Verified: "
            "`tests/storage/test_schema_version_parity.py` + "
            "`tests/api/test_health_schema_version.py` = 7 passed."
        ),
        absent_checks=(
            "scripts/run_migration.py",
            "tests/scripts/test_run_migration.py",
        ),
    ),
    Milestone(
        key="m3-collector-health-v2",
        milestone="M3 collector health v2 + circuit breaker",
        status="\U0001f7e1 CODE ON MAIN",
        evidence=(
            "`ops.collector_health.REPORT_SCHEMA_VERSION = 2` and "
            "`storage/collector_suspension.py` exist. Remaining risks: JSON "
            "store concurrency, env-var bypass semantics, and release "
            "ratification from real collector artifacts."
        ),
        file_checks=("ops/collector_health.py", "storage/collector_suspension.py"),
        import_checks=("collector_health_v2",),
    ),
    Milestone(
        key="m4-cassette-policy",
        milestone="M4 vcrpy cassette lifecycle",
        status="\U0001f7e1 CODE ON MAIN",
        evidence=(
            "`tests/support/cassette_policy.py` exists. Regeneration cadence and "
            "storage policy are unratified."
        ),
        file_checks=("tests/support/cassette_policy.py",),
    ),
    Milestone(
        key="m5-parity-gate",
        milestone="M5 parity gate",
        status="\U0001f7e1 CODE ON MAIN",
        evidence=(
            "`scripts/ci/run_thesis_parity_gate.py` exists. Current tests cover "
            "arithmetic/config defaults; they do not prove the CLI path honors "
            "`temperature=0.0`."
        ),
        file_checks=("scripts/ci/run_thesis_parity_gate.py",),
    ),
    Milestone(
        key="m6-pr-evidence",
        milestone="M6 PR evidence enforcement",
        status="\U0001f7e1 CODE ON MAIN, hardening first",
        evidence=(
            "`scripts/check_pr_evidence.py` exists but accepts any GitHub repo "
            "and `/actions/runs/0` style URLs. This is the first patch because "
            "future evidence depends on it."
        ),
        file_checks=("scripts/check_pr_evidence.py",),
    ),
    Milestone(
        key="m7-trust-status-cli",
        milestone="M7 trust status CLI",
        status="\U0001f7e1 CODE ON MAIN",
        evidence=(
            "`ops/trust_status.py` exists and requires schema v2. Max-age/expiry "
            "semantics and release-readiness truth table are follow-on "
            "hardening. Milestone-table generation now lives in "
            "`ops.cli trust status` (this generator)."
        ),
        file_checks=("ops/trust_status.py",),
        import_checks=("trust_status_schema_gate",),
    ),
)


# ---------------------------------------------------------------------------
# Import-level contract checks (return None on pass, error string on failure)
# ---------------------------------------------------------------------------

def _check_collector_health_v2() -> Optional[str]:
    try:
        from ops.collector_health import REPORT_SCHEMA_VERSION
    except Exception as exc:  # pragma: no cover - environment failure path
        return f"import ops.collector_health failed: {exc}"
    if REPORT_SCHEMA_VERSION != 2:
        return f"REPORT_SCHEMA_VERSION={REPORT_SCHEMA_VERSION} (expected 2)"
    return None


def _check_trust_status_schema_gate() -> Optional[str]:
    try:
        from ops.collector_health import REPORT_SCHEMA_VERSION
        from ops.trust_status import TrustStatusCLI

        TrustStatusCLI().load_reports(schema_version=REPORT_SCHEMA_VERSION)
    except Exception as exc:  # pragma: no cover - environment failure path
        return f"trust_status schema gate failed: {exc}"
    return None


IMPORT_CHECKS: Dict[str, Callable[[], Optional[str]]] = {
    "collector_health_v2": _check_collector_health_v2,
    "trust_status_schema_gate": _check_trust_status_schema_gate,
}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class RowResult:
    milestone: Milestone
    status: str
    evidence: str
    provenance_label: str
    checks_ok: bool
    check_failures: List[str] = field(default_factory=list)


@dataclass
class StatusReport:
    generated_at: str
    repo_root: str
    rows: List[RowResult]
    ledger: Optional[Dict[str, Any]] = None
    gh_verified: Optional[Dict[str, str]] = None


def evaluate_milestone(m: Milestone, repo_root: Path) -> RowResult:
    failures: List[str] = []
    for rel in m.file_checks:
        if not (repo_root / rel).exists():
            failures.append(f"missing {rel}")
    for rel in m.absent_checks:
        if (repo_root / rel).exists():
            failures.append(f"unexpectedly present: {rel}")
    for rel, needle in m.symbol_checks:
        fp = repo_root / rel
        if not fp.exists():
            failures.append(f"missing {rel}")
        elif needle not in fp.read_text(encoding="utf-8", errors="replace"):
            failures.append(f"{rel} lacks {needle!r}")
    for name in m.import_checks:
        err = IMPORT_CHECKS[name]()
        if err:
            failures.append(err)

    if not m.is_derived:
        label = f"manual (verified {m.verified})" if m.verified else "manual"
        ok = True
    else:
        kinds = "repo+import" if m.import_checks else "repo"
        if failures:
            label = f"derived: {kinds} — CHECK FAILED: {failures[0]}"
            ok = False
        else:
            label = f"derived: {kinds} — checks OK"
            ok = True

    return RowResult(
        milestone=m,
        status=m.status,
        evidence=m.evidence,
        provenance_label=label,
        checks_ok=ok,
        check_failures=failures,
    )


# ---------------------------------------------------------------------------
# Ledger-audit evidence (READ-ONLY)
# ---------------------------------------------------------------------------

def find_latest_ledger_audit(repo_root: Path) -> Optional[Path]:
    """Newest ledger_audit_report.json under ai-logs/hermes/runs/, by run-dir name.

    Run directories are timestamp-named (hermes_YYYYMMDD_HHMMSS_xxxx), so a
    lexical sort on the parent directory name yields chronological order.
    """
    runs_dir = Path(repo_root) / "ai-logs" / "hermes" / "runs"
    if not runs_dir.exists():
        return None
    candidates = sorted(
        runs_dir.glob("*/ledger_audit_report.json"),
        key=lambda p: p.parent.name,
    )
    return candidates[-1] if candidates else None


def load_ledger_audit_summary(
    repo_root: Optional[Path] = None,
    evidence_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    if evidence_path is not None:
        path: Optional[Path] = Path(evidence_path)
    elif repo_root is not None:
        path = find_latest_ledger_audit(Path(repo_root))
    else:
        path = None
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "auditId": data.get("auditId"),
        "generatedAt": data.get("generatedAt"),
        "operatorSummary": data.get("operatorSummary", {}) or {},
        "source_path": str(path),
    }


# ---------------------------------------------------------------------------
# Optional gh verification (network; only behind --live-gh)
# ---------------------------------------------------------------------------

def verify_gh_runs(run_ids: List[str], repo_root: Path) -> Dict[str, str]:
    results: Dict[str, str] = {}
    for rid in run_ids:
        try:
            proc = subprocess.run(
                ["gh", "run", "view", str(rid), "--json", "conclusion"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(repo_root),
            )
            if proc.returncode == 0:
                results[str(rid)] = str(
                    json.loads(proc.stdout).get("conclusion", "unknown")
                )
            else:
                results[str(rid)] = f"error(rc={proc.returncode})"
        except Exception as exc:
            results[str(rid)] = f"error({type(exc).__name__})"
    return results


# ---------------------------------------------------------------------------
# Report assembly + rendering
# ---------------------------------------------------------------------------

def build_status_report(
    repo_root: Path,
    evidence_path: Optional[Path] = None,
    live_gh: bool = False,
) -> StatusReport:
    repo_root = Path(repo_root)
    rows = [evaluate_milestone(m, repo_root) for m in MILESTONES]
    ledger = load_ledger_audit_summary(repo_root=repo_root, evidence_path=evidence_path)

    gh_verified: Optional[Dict[str, str]] = None
    if live_gh:
        run_ids: List[str] = []
        for m in MILESTONES:
            for rid in m.gh_runs:
                if rid not in run_ids:
                    run_ids.append(rid)
        gh_verified = verify_gh_runs(run_ids, repo_root=repo_root)
        for row in rows:
            if row.milestone.gh_runs:
                row.provenance_label += "; cited runs gh-verified (see appendix)"

    return StatusReport(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        repo_root=str(repo_root),
        rows=rows,
        ledger=ledger,
        gh_verified=gh_verified,
    )


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_status_markdown(report: StatusReport) -> str:
    lines: List[str] = []
    lines.append("## Trust Release — milestone status")
    lines.append("")
    lines.append(
        f"> Generated by `{GENERATOR_COMMAND}` at {report.generated_at}."
    )
    lines.append(
        f"> Row statuses and evidence are declared once in `{CONFIG_MODULE_PATH}` "
        "(MILESTONES) with explicit provenance;"
    )
    lines.append(
        "> derived rows are corroborated by local read-only checks at generation "
        "time. Do not hand-edit this table —"
    )
    lines.append("> edit the config block and regenerate.")
    lines.append("")
    lines.append("| Milestone | Status | Evidence / caveat | Provenance |")
    lines.append("|-----------|--------|-------------------|------------|")
    for row in report.rows:
        lines.append(
            f"| {_cell(row.milestone.milestone)} | {_cell(row.status)} "
            f"| {_cell(row.evidence)} | {_cell(row.provenance_label)} |"
        )
    lines.append("")
    lines.append("### Live evidence appendix")
    lines.append("")
    if report.ledger:
        op = report.ledger.get("operatorSummary", {})
        sev = op.get("severityCounts", {}) or {}
        sev_s = ", ".join(f"{k}={v}" for k, v in sev.items()) or "n/a"
        subs = ", ".join(op.get("subsystemsWithFindings", []) or []) or "none"
        lines.append(
            f"- Hermes ledger-audit `{report.ledger.get('auditId')}` "
            f"(generated {report.ledger.get('generatedAt')}): status "
            f"`{op.get('status')}`, blocking findings {op.get('blockingFindings')} "
            f"({sev_s}); subsystems with findings: {subs}. "
            f"Source (read-only): `{report.ledger.get('source_path')}`."
        )
    else:
        lines.append(
            "- No hermes ledger-audit artifacts found under `ai-logs/hermes/runs/` "
            "(the ledger is local/untracked; pass `--evidence "
            "<ledger_audit_report.json>` to include one)."
        )
    if report.gh_verified is None:
        lines.append(
            "- GitHub run verification: skipped (offline default). Pass `--live-gh` "
            "to query cited run conclusions via the gh CLI."
        )
    else:
        pairs = ", ".join(f"{rid}={c}" for rid, c in report.gh_verified.items()) or "none cited"
        lines.append(
            f"- GitHub run conclusions gh-verified via `gh run view --json conclusion`: {pairs}."
        )
    lines.append("")
    return "\n".join(lines)
