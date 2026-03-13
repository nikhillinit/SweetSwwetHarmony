"""Shared CLI output formatting constants and helpers.

Provides a unified design system for all CLI output across
run_pipeline.py, ops/cli.py, and ops/quality_cli.py.
"""

# ---------------------------------------------------------------------------
# Separators --standardised widths
# ---------------------------------------------------------------------------

BANNER_WIDTH = 70
BANNER_SEP = "=" * BANNER_WIDTH
SECTION_SEP = "-" * BANNER_WIDTH


# ---------------------------------------------------------------------------
# Status symbols --single vocabulary for all commands
# ---------------------------------------------------------------------------

STATUS_OK = "[OK]"
STATUS_FAIL = "[FAIL]"
STATUS_WARN = "[WARN]"
STATUS_SKIP = "[SKIP]"
STATUS_DRY = "[DRY]"
STATUS_CRIT = "[CRIT]"
STATUS_INFO = "[INFO]"
STATUS_UNKNOWN = "[??]"

# Mapping from common status strings to symbols.
# Callers can do: STATUS_MAP.get(status_str, STATUS_UNKNOWN)
STATUS_MAP = {
    # collector results
    "success": STATUS_OK,
    "skipped": STATUS_SKIP,
    "partial_success": STATUS_WARN,
    "dry_run": STATUS_DRY,
    "error": STATUS_FAIL,
    "not_found": STATUS_FAIL,
    # health / activation
    "pass": STATUS_OK,
    "fail": STATUS_FAIL,
    "warn": STATUS_WARN,
    "skip": STATUS_SKIP,
    "ready": STATUS_OK,
    "blocked": STATUS_FAIL,
    # severity
    "critical": STATUS_CRIT,
    "warning": STATUS_WARN,
    "info": STATUS_INFO,
    # config validator
    "ok": STATUS_OK,
}


# ---------------------------------------------------------------------------
# Jargon hints --brief inline explanations for domain terms
# ---------------------------------------------------------------------------

JARGON = {
    "SPC": "Statistical Process Control",
    "canonical key": "unique company identifier (e.g. domain:acme.ai)",
    "evidence family": "signal source category (fundraise, public_buzz, etc.)",
    "Phase G": "entity identity resolution system",
    "evidence key": "content-hash for signal deduplication",
    "ADJ": "adjudication (human review label)",
}


def explain(term: str) -> str:
    """Return 'term (explanation)' if term has a jargon entry, else just term."""
    hint = JARGON.get(term)
    return f"{term} ({hint})" if hint else term


# ---------------------------------------------------------------------------
# Banner / section helpers
# ---------------------------------------------------------------------------

def print_banner(title: str) -> None:
    """Print a top-level banner: ===... / TITLE / ===..."""
    print(BANNER_SEP)
    print(title)
    print(BANNER_SEP)


def print_section(title: str) -> None:
    """Print a section header: TITLE / ---..."""
    print(title)
    print(SECTION_SEP)


# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------

def format_verdict(
    label: str,
    *,
    ok: bool,
    summary_parts: list[str] | None = None,
    error_count: int = 0,
) -> str:
    """Build a one-line verdict string.

    Examples:
        PIPELINE RESULTS -- OK (8 collected, 3 new, 0 errors, 27.5s)
        PIPELINE RESULTS -- 2 ERRORS (see details below)
    """
    if ok:
        detail = ", ".join(summary_parts) if summary_parts else "success"
        return f"{label} -- OK ({detail})"
    else:
        if error_count:
            noun = "ERROR" if error_count == 1 else "ERRORS"
            return f"{label} -- {error_count} {noun} (see details below)"
        return f"{label} -- FAILED"


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def print_phase(current: int, total: int, description: str) -> None:
    """Print a pipeline phase progress line: [1/3] Collecting signals..."""
    print(f"[{current}/{total}] {description}")


def print_progress_item(symbol: str, name: str, detail: str = "") -> None:
    """Print an indented progress item under a phase.

    Example:  [OK]  github            12 found, 3 new
    """
    if detail:
        print(f"  {symbol:6s} {name:<18s} {detail}")
    else:
        print(f"  {symbol:6s} {name}")
