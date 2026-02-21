"""CI governance ratchet: enforce centralized CollectorHttpClient usage.

Collectors must not construct httpx.AsyncClient directly — construction
should go through CollectorHttpClient (collectors/http_client.py).

Rules:
  A. No new file with direct AsyncClient usage outside the baseline.
  B. No per-file callsite count increase vs the baseline.
  C. Baseline updates only in migration PRs; total files and total
     occurrences must monotonically decrease or stay equal.

Permanent exemption: http_client.py (the wrapper itself).

Baseline: tests/collectors/asyncclient_baseline.json
"""

import json
import os
import re

import pytest

# Root of the repository
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COLLECTORS_DIR = os.path.join(REPO_ROOT, "collectors")
BASELINE_PATH = os.path.join(os.path.dirname(__file__), "asyncclient_baseline.json")

# Regex from the governance spec — matches httpx.AsyncClient( with optional whitespace
ASYNCCLIENT_RE = re.compile(r"httpx\.\s*AsyncClient\s*\(")


def _load_baseline():
    """Load the governance baseline JSON."""
    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _scan_collectors():
    """Scan collectors/ for direct httpx.AsyncClient usage.

    Returns:
        dict mapping filename -> occurrence count (only files with matches)
    """
    baseline = _load_baseline()
    exempt = set(baseline.get("exempt", []))
    results = {}

    for fname in sorted(os.listdir(COLLECTORS_DIR)):
        if not fname.endswith(".py"):
            continue
        if fname in exempt:
            continue

        fpath = os.path.join(COLLECTORS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        count = len(ASYNCCLIENT_RE.findall(content))
        if count > 0:
            results[fname] = count

    return results


class TestNoDirectAsyncClient:
    """Governance ratchet for httpx.AsyncClient migration."""

    def test_rule_a_no_new_files(self):
        """Rule A: No new file with direct AsyncClient outside the baseline."""
        baseline = _load_baseline()
        baseline_files = set(baseline["files"].keys())
        actual = _scan_collectors()
        actual_files = set(actual.keys())

        new_files = actual_files - baseline_files
        if new_files:
            details = ", ".join(sorted(new_files))
            pytest.fail(
                f"New files with direct httpx.AsyncClient() found outside baseline: "
                f"{details}. Migrate to CollectorHttpClient or add to baseline "
                f"in a migration PR."
            )

    def test_rule_b_no_callsite_increase(self):
        """Rule B: No per-file callsite count increase vs baseline."""
        baseline = _load_baseline()
        actual = _scan_collectors()
        violations = []

        for fname, baseline_count in baseline["files"].items():
            actual_count = actual.get(fname, 0)
            if actual_count > baseline_count:
                violations.append(
                    f"  {fname}: baseline={baseline_count}, actual={actual_count}"
                )

        if violations:
            pytest.fail(
                "Per-file callsite count increased vs baseline:\n"
                + "\n".join(violations)
                + "\n\nMigrate to CollectorHttpClient instead of adding "
                "new direct httpx.AsyncClient() calls."
            )

    def test_baseline_metadata_consistent(self):
        """Baseline metadata matches the file entries."""
        baseline = _load_baseline()
        files = baseline["files"]
        metadata = baseline["metadata"]

        assert len(files) == metadata["total_files"], (
            f"Baseline metadata total_files={metadata['total_files']} "
            f"but {len(files)} file entries found"
        )

        total_occ = sum(files.values())
        assert total_occ == metadata["total_occurrences"], (
            f"Baseline metadata total_occurrences={metadata['total_occurrences']} "
            f"but sum of file counts={total_occ}"
        )

    def test_baseline_monotonic_decrease(self):
        """Rule C: Actual counts must be <= baseline counts (monotonic decrease)."""
        baseline = _load_baseline()
        actual = _scan_collectors()

        actual_total_files = len(actual)
        actual_total_occ = sum(actual.values())

        baseline_total_files = baseline["metadata"]["total_files"]
        baseline_total_occ = baseline["metadata"]["total_occurrences"]

        assert actual_total_files <= baseline_total_files, (
            f"Total files with AsyncClient increased: "
            f"baseline={baseline_total_files}, actual={actual_total_files}. "
            f"Update baseline only in migration PRs."
        )

        assert actual_total_occ <= baseline_total_occ, (
            f"Total AsyncClient occurrences increased: "
            f"baseline={baseline_total_occ}, actual={actual_total_occ}. "
            f"Update baseline only in migration PRs."
        )

    def test_http_client_is_exempt(self):
        """http_client.py is permanently exempt from the ratchet."""
        baseline = _load_baseline()
        assert "http_client.py" in baseline.get("exempt", [])

    def test_regex_pattern_matches_variants(self):
        """The regex catches whitespace variants like httpx. AsyncClient(."""
        assert ASYNCCLIENT_RE.search("httpx.AsyncClient(")
        assert ASYNCCLIENT_RE.search("httpx.AsyncClient (")
        assert ASYNCCLIENT_RE.search("httpx. AsyncClient(")
        assert ASYNCCLIENT_RE.search("httpx.\n  AsyncClient(")
        assert not ASYNCCLIENT_RE.search("httpx.Client(")
        assert not ASYNCCLIENT_RE.search("# httpx.AsyncClient")  # no paren
