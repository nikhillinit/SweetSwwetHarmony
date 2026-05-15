"""Read-only source-yield probe for Greenhouse and Ashby job posting signals.

This diagnostic script intentionally stays outside BaseCollector.run() and
SignalStore.initialize(). It reuses JobPostingsCollector's source-specific async
fetch/normalization methods, then mirrors save-path attribution with read-only
SQLite queries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from collectors.http_client import CollectorHttpClient, RunContext
from collectors.job_postings import (
    ASHBY_API,
    GREENHOUSE_API,
    JobPostingSignal,
    JobPostingsCollector,
)
from utils.evidence_key import compute_evidence_key
from verification.verification_gate_v2 import Signal

SOURCE_TO_API = {
    "greenhouse": "greenhouse_jobs",
    "ashby": "ashby_jobs",
}
ORDERED_SOURCES = ("greenhouse", "ashby")
RUNTIME_ORDER_SCOPE = "greenhouse_ashby_only"


class ProbeError(Exception):
    """Operator-facing probe configuration or execution error."""


@dataclass(frozen=True)
class FetchAttempt:
    source: str
    board_id: str
    domain: str
    status: str
    job_count: int = 0
    error: str | None = None


@dataclass
class SourceCandidate:
    domain: str
    source: str
    source_api: str
    board_id: str
    signal: Signal
    evidence_key: str
    canonical_key: str
    runtime_order_scope: str = RUNTIME_ORDER_SCOPE
    runtime_first_match: str | None = None
    would_short_circuit: bool | str = "unknown"
    stage: str = "normalized"
    attribution_detail: str | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.canonical_key, self.signal.signal_type, self.signal.source_api)


@dataclass
class SourceProbeResult:
    domain: str
    source: str
    attempts: list[FetchAttempt] = field(default_factory=list)
    candidate: SourceCandidate | None = None

    @property
    def complete(self) -> bool:
        return all(attempt.status not in {"fetch_error", "parse_error"} for attempt in self.attempts)


class FetchRecorder:
    """Records HTTP outcomes hidden by JobPostingsCollector's private methods."""

    def __init__(self, fixture_dir: Path | None = None):
        self.fixture_dir = fixture_dir
        self.attempts: list[FetchAttempt] = []
        self.current_domain: str | None = None

    async def wrap_http_get(
        self,
        original: Callable[..., Any],
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        source, board_id = source_and_board_from_url(url)
        domain = self.current_domain or board_id
        try:
            if self.fixture_dir:
                data = load_fixture_payload(self.fixture_dir, source, board_id)
            else:
                data = await original(url, params=params, timeout=timeout)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            if status == 404:
                self.attempts.append(FetchAttempt(source, board_id, domain, "not_found"))
                raise
            self.attempts.append(
                FetchAttempt(source, board_id, domain, "fetch_error", error=f"http_{status}")
            )
            raise
        except Exception as exc:
            self.attempts.append(
                FetchAttempt(source, board_id, domain, "fetch_error", error=type(exc).__name__)
            )
            raise

        job_count = count_jobs(data)
        status = "fetched" if job_count > 0 else "no_jobs"
        if data is not None and not isinstance(data, dict):
            status = "parse_error"
        self.attempts.append(FetchAttempt(source, board_id, domain, status, job_count=job_count))
        return data

    def attempts_for(self, source: str, domain: str, board_ids: Iterable[str]) -> list[FetchAttempt]:
        wanted = set(board_ids)
        return [
            attempt
            for attempt in self.attempts
            if attempt.source == source and attempt.domain == domain and attempt.board_id in wanted
        ]


class ReadOnlySignalStoreProbe:
    """Read-only mirror of SignalStore duplicate and suppression checks."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ReadOnlySignalStoreProbe":
        if not self.db_path.exists():
            raise ProbeError(f"DB file does not exist: {self.db_path}")
        self.conn = sqlite3.connect(sqlite_ro_uri(self.db_path), uri=True)
        self.conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.conn:
            self.conn.close()

    def duplicate_stage(self, candidate: SourceCandidate) -> tuple[str | None, str | None]:
        if not self.conn:
            return None, None

        if candidate.evidence_key:
            row = self.conn.execute(
                "SELECT 1 FROM signals WHERE evidence_key = ? LIMIT 1",
                (candidate.evidence_key,),
            ).fetchone()
            if row:
                return "db_duplicate", "evidence_key"

        detected_at = isoformat(candidate.signal.detected_at)
        if candidate.canonical_key and candidate.signal.signal_type and candidate.signal.source_api and detected_at:
            row = self.conn.execute(
                """
                SELECT 1 FROM signals
                WHERE canonical_key = ?
                  AND signal_type = ?
                  AND source_api = ?
                  AND detected_at = ?
                LIMIT 1
                """,
                (
                    candidate.canonical_key,
                    candidate.signal.signal_type,
                    candidate.signal.source_api,
                    detected_at,
                ),
            ).fetchone()
            if row:
                return "db_duplicate", "exact_tuple"
        elif candidate.canonical_key:
            row = self.conn.execute(
                "SELECT 1 FROM signals WHERE canonical_key = ? LIMIT 1",
                (candidate.canonical_key,),
            ).fetchone()
            if row:
                return "db_duplicate", "canonical_key_legacy"

        return None, None

    def suppression_stage(self, candidate: SourceCandidate) -> tuple[str | None, str | None]:
        if not self.conn:
            return None, None

        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            """
            SELECT notion_page_id, status
            FROM suppression_cache
            WHERE canonical_key = ? AND expires_at > ?
            LIMIT 1
            """,
            (candidate.canonical_key, now),
        ).fetchone()
        if row:
            return "notion_suppressed", f"{row['status']}:{row['notion_page_id']}"
        return None, None


def sqlite_ro_uri(db_path: str | Path) -> str:
    path = Path(db_path)
    if path.is_absolute():
        return f"{path.resolve().as_uri()}?mode=ro"
    normalized = Path(str(path).replace("\\", "/")).as_posix()
    return f"file:{quote(normalized, safe='/._-')}?mode=ro"


def source_and_board_from_url(url: str) -> tuple[str, str]:
    if url.startswith(f"{GREENHOUSE_API}/"):
        return "greenhouse", url.removeprefix(f"{GREENHOUSE_API}/").removesuffix("/jobs").strip("/")
    if url.startswith(f"{ASHBY_API}/"):
        return "ashby", url.removeprefix(f"{ASHBY_API}/").strip("/")
    return "unknown", Path(url).name


def count_jobs(data: Any) -> int:
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return len(data["jobs"])
    return 0


def load_fixture_payload(fixture_dir: Path, source: str, board_id: str) -> Any:
    candidates = [
        fixture_dir / source / f"{board_id}.json",
        fixture_dir / f"{source}_{board_id}.json",
        fixture_dir / f"{source}-{board_id}.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {"jobs": []}


def resolve_domains(
    *,
    domains_file: str | None,
    domains: str | None,
    env: dict[str, str] | None = None,
) -> tuple[list[str], str]:
    env = env or os.environ
    if domains_file:
        path = Path(domains_file)
        if not path.exists():
            raise ProbeError(f"--domains-file does not exist: {path}")
        values = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return normalize_domains(values), "domains_file"
    if domains:
        return normalize_domains(domains.split(",")), "domains"
    env_value = env.get("JOB_POSTING_DOMAINS", "")
    if env_value:
        return normalize_domains(env_value.split(",")), "JOB_POSTING_DOMAINS"
    raise ProbeError(
        "No domains provided. Pass --domains-file, --domains, or set JOB_POSTING_DOMAINS."
    )


def normalize_domains(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    domains: list[str] = []
    for value in values:
        domain = value.lower().replace("www.", "").strip().strip(",")
        if domain and domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def parse_sources(value: str) -> list[str]:
    sources = [source.strip().lower() for source in value.split(",") if source.strip()]
    invalid = [source for source in sources if source not in SOURCE_TO_API]
    if invalid:
        raise ProbeError(
            f"Unsupported source(s): {', '.join(invalid)}. Supported: greenhouse, ashby."
        )
    return sources or list(ORDERED_SOURCES)


async def fetch_source_candidate(
    collector: JobPostingsCollector,
    source: str,
    board_id: str,
    domain: str,
) -> JobPostingSignal | None:
    if source == "greenhouse":
        return await collector._check_greenhouse(board_id, domain)
    if source == "ashby":
        return await collector._check_ashby(board_id, domain)
    raise ProbeError(f"Unsupported source: {source}")


async def fetch_domain_sources(
    collector: JobPostingsCollector,
    domain: str,
    sources: list[str],
    recorder: FetchRecorder,
) -> dict[str, SourceProbeResult]:
    board_ids = collector._generate_board_ids(domain)
    results = {source: SourceProbeResult(domain=domain, source=source) for source in sources}

    for source in sources:
        for board_id in board_ids:
            recorder.current_domain = domain
            try:
                job_signal = await fetch_source_candidate(collector, source, board_id, domain)
            finally:
                recorder.current_domain = None
            results[source].attempts = recorder.attempts_for(source, domain, board_ids)
            if job_signal:
                signal = job_signal.to_signal()
                canonical_key = signal.raw_data.get("canonical_key") or signal.id
                evidence_key = compute_evidence_key(signal.source_api, signal.source_url or "")
                results[source].candidate = SourceCandidate(
                    domain=domain,
                    source=source,
                    source_api=SOURCE_TO_API[source],
                    board_id=board_id,
                    signal=signal,
                    evidence_key=evidence_key,
                    canonical_key=canonical_key,
                )
                break

    apply_runtime_ordering(results)
    return results


def apply_runtime_ordering(results: dict[str, SourceProbeResult]) -> None:
    greenhouse_candidate = results.get("greenhouse").candidate if "greenhouse" in results else None
    ashby_candidate = results.get("ashby").candidate if "ashby" in results else None

    first_match = None
    if greenhouse_candidate:
        first_match = "greenhouse_jobs"
    elif ashby_candidate:
        first_match = "ashby_jobs"

    if greenhouse_candidate:
        greenhouse_candidate.runtime_first_match = first_match
        greenhouse_candidate.would_short_circuit = False

    if ashby_candidate:
        ashby_candidate.runtime_first_match = first_match
        greenhouse_result = results.get("greenhouse")
        if greenhouse_candidate:
            ashby_candidate.would_short_circuit = True
        elif greenhouse_result and greenhouse_result.complete:
            ashby_candidate.would_short_circuit = False
        else:
            ashby_candidate.would_short_circuit = "unknown"


def attribute_candidates(
    domain_results: list[dict[str, SourceProbeResult]],
    db: ReadOnlySignalStoreProbe | None,
) -> None:
    seen_identities: set[tuple[str, str, str]] = set()
    for results in domain_results:
        for source in ORDERED_SOURCES:
            result = results.get(source)
            if not result or not result.candidate:
                continue
            candidate = result.candidate
            if candidate.identity in seen_identities:
                candidate.stage = "same_run_duplicate"
                candidate.attribution_detail = "identity"
                continue

            if db:
                stage, detail = db.duplicate_stage(candidate)
                if stage:
                    candidate.stage = stage
                    candidate.attribution_detail = detail
                    seen_identities.add(candidate.identity)
                    continue

                stage, detail = db.suppression_stage(candidate)
                if stage:
                    candidate.stage = stage
                    candidate.attribution_detail = detail
                    seen_identities.add(candidate.identity)
                    continue

            candidate.stage = "would_insert"
            candidate.attribution_detail = "no_duplicate_or_suppression"
            seen_identities.add(candidate.identity)


async def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    domains, domain_source = resolve_domains(
        domains_file=args.domains_file,
        domains=args.domains,
    )
    if args.mode != "source-isolated":
        raise ProbeError("Only --mode source-isolated is implemented in this diagnostic v1.")
    if not domains:
        raise ProbeError("Resolved domain list is empty.")
    if args.max_domains > 0 and len(domains) > args.max_domains:
        raise ProbeError(
            f"Resolved {len(domains)} domains, above --max-domains={args.max_domains}. "
            "Pass a higher --max-domains value if this is intentional."
        )

    sources = parse_sources(args.sources)
    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else None
    if fixture_dir and not fixture_dir.exists():
        raise ProbeError(f"--fixture-dir does not exist: {fixture_dir}")

    recorder = FetchRecorder(fixture_dir=fixture_dir)
    execution_id = f"job-postings-source-yield-probe-{uuid.uuid4().hex[:12]}"

    async with httpx.AsyncClient(timeout=args.timeout) as httpx_client:
        http = CollectorHttpClient(
            httpx_client,
            run_context=RunContext(execution_id=execution_id, dry_run=True),
            collector_name="job_postings_source_yield_probe",
        )
        collector = JobPostingsCollector(
            domains=[],
            store=None,
            asset_store=None,
            http=http,
            timeout=args.timeout,
        )
        original_http_get = collector._http_get

        async def recording_http_get(
            url: str,
            headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None,
            timeout: float | None = None,
            operation: Any = None,
        ) -> Any:
            del headers, operation
            return await recorder.wrap_http_get(original_http_get, url, params=params, timeout=timeout)

        collector._http_get = recording_http_get  # type: ignore[method-assign]

        domain_results = [
            await fetch_domain_sources(collector, domain, sources, recorder)
            for domain in domains
        ]

    if args.db:
        with ReadOnlySignalStoreProbe(args.db) as db:
            attribute_candidates(domain_results, db)
    else:
        attribute_candidates(domain_results, None)

    payload = build_payload(
        execution_id=execution_id,
        mode=args.mode,
        domains=domains,
        domain_source=domain_source,
        sources=sources,
        db_path=args.db,
        state_path=args.state,
        keepalive_artifact=args.keepalive_artifact,
        fixture_dir=str(fixture_dir) if fixture_dir else None,
        domain_results=domain_results,
    )
    return payload


def build_payload(
    *,
    execution_id: str,
    mode: str,
    domains: list[str],
    domain_source: str,
    sources: list[str],
    db_path: str | None,
    state_path: str | None,
    keepalive_artifact: str | None,
    fixture_dir: str | None,
    domain_results: list[dict[str, SourceProbeResult]],
) -> dict[str, Any]:
    source_summaries = {
        SOURCE_TO_API[source]: {
            "attempts": 0,
            "fetched": 0,
            "normalized_candidates": 0,
            "same_run_duplicates": 0,
            "db_duplicates": 0,
            "notion_suppressed": 0,
            "would_insert": 0,
            "fetch_errors": 0,
            "parse_errors": 0,
            "no_candidates": 0,
            "examples": [],
        }
        for source in sources
    }

    domains_payload: list[dict[str, Any]] = []
    for results in domain_results:
        domain = next(iter(results.values())).domain if results else ""
        domain_entry: dict[str, Any] = {"domain": domain, "sources": {}}
        for source in sources:
            result = results[source]
            source_api = SOURCE_TO_API[source]
            summary = source_summaries[source_api]
            summary["attempts"] += len(result.attempts)
            summary["fetched"] += sum(1 for attempt in result.attempts if attempt.status == "fetched")
            summary["fetch_errors"] += sum(1 for attempt in result.attempts if attempt.status == "fetch_error")
            summary["parse_errors"] += sum(1 for attempt in result.attempts if attempt.status == "parse_error")
            if not result.candidate:
                summary["no_candidates"] += 1

            source_entry: dict[str, Any] = {
                "attempts": [attempt_to_dict(attempt) for attempt in result.attempts],
                "candidate": None,
            }
            if result.candidate:
                candidate = candidate_to_dict(result.candidate)
                source_entry["candidate"] = candidate
                summary["normalized_candidates"] += 1
                increment_summary_stage(summary, result.candidate.stage)
                if len(summary["examples"]) < 3:
                    summary["examples"].append(candidate)
            domain_entry["sources"][source_api] = source_entry
        domains_payload.append(domain_entry)

    return {
        "execution_id": execution_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "runtime_order_scope": RUNTIME_ORDER_SCOPE,
        "inputs": {
            "domain_source": domain_source,
            "domains": domains,
            "sources": [SOURCE_TO_API[source] for source in sources],
            "db": db_path,
            "state": state_path,
            "keepalive_artifact": keepalive_artifact,
            "fixture_dir": fixture_dir,
        },
        "summary": source_summaries,
        "domains": domains_payload,
    }


def increment_summary_stage(summary: dict[str, Any], stage: str) -> None:
    if stage == "same_run_duplicate":
        summary["same_run_duplicates"] += 1
    elif stage == "db_duplicate":
        summary["db_duplicates"] += 1
    elif stage == "notion_suppressed":
        summary["notion_suppressed"] += 1
    elif stage == "would_insert":
        summary["would_insert"] += 1


def attempt_to_dict(attempt: FetchAttempt) -> dict[str, Any]:
    return {
        "source": attempt.source,
        "board_id": attempt.board_id,
        "status": attempt.status,
        "job_count": attempt.job_count,
        "error": attempt.error,
    }


def candidate_to_dict(candidate: SourceCandidate) -> dict[str, Any]:
    return {
        "domain": candidate.domain,
        "source_api": candidate.source_api,
        "board_id": candidate.board_id,
        "signal_type": candidate.signal.signal_type,
        "canonical_key": candidate.canonical_key,
        "evidence_key": candidate.evidence_key,
        "source_url": candidate.signal.source_url,
        "detected_at": isoformat(candidate.signal.detected_at),
        "confidence": candidate.signal.confidence,
        "runtime_order_scope": candidate.runtime_order_scope,
        "runtime_first_match": candidate.runtime_first_match,
        "would_short_circuit": candidate.would_short_circuit,
        "stage": candidate.stage,
        "attribution_detail": candidate.attribution_detail,
        "company_name": candidate.signal.raw_data.get("company_name"),
        "total_positions": candidate.signal.raw_data.get("total_positions"),
        "engineering_positions": candidate.signal.raw_data.get("engineering_positions"),
        "sample_titles": candidate.signal.raw_data.get("sample_titles", []),
    }


def isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def emit_terminal_summary(payload: dict[str, Any]) -> str:
    lines = [
        f"job_postings source-yield probe {payload['execution_id']}",
        f"mode={payload['mode']} runtime_order_scope={payload['runtime_order_scope']}",
    ]
    for source_api, summary in payload["summary"].items():
        lines.append(
            "{source}: attempts={attempts} normalized={normalized} "
            "same_run_dup={same_run} db_dup={db_dup} suppressed={suppressed} "
            "would_insert={would_insert} fetch_errors={fetch_errors}".format(
                source=source_api,
                attempts=summary["attempts"],
                normalized=summary["normalized_candidates"],
                same_run=summary["same_run_duplicates"],
                db_dup=summary["db_duplicates"],
                suppressed=summary["notion_suppressed"],
                would_insert=summary["would_insert"],
                fetch_errors=summary["fetch_errors"],
            )
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Greenhouse/Ashby source-yield probe")
    parser.add_argument("--domains-file")
    parser.add_argument("--domains")
    parser.add_argument("--sources", default="greenhouse,ashby")
    parser.add_argument("--db")
    parser.add_argument("--state")
    parser.add_argument("--keepalive-artifact")
    parser.add_argument("--fixture-dir")
    parser.add_argument("--max-domains", type=int, default=20)
    parser.add_argument("--mode", choices=["source-isolated", "runtime-mirror"], default="source-isolated")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--out")
    return parser


async def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = await run_probe(args)
    except ProbeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    output = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        out_path = Path(args.out)
        if "artifacts/keepalive" in out_path.as_posix():
            print("ERROR: --out must not target artifacts/keepalive", file=sys.stderr)
            return 2
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output + "\n", encoding="utf-8")

    print(output if args.json_output else emit_terminal_summary(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
