"""
Quality Ops CLI registration for ops/cli.py

Usage:
    python -m ops.cli quality --help
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ops.quality.db import quality_conn, utc_now_iso
from ops.quality.labels import label_signal_manual, list_adj_review_candidates
from ops.quality.stats import build_router_diagnostic_summary, get_overall_stats, get_stats_by_source_api
from ops.quality.status_events import sync_and_capture_status_events
from ops.quality.outcomes import backfill_outcomes_from_events, backfill_from_snapshot_status
from ops.quality.export import export_dataset_csv, export_dataset_jsonl
from ops.quality.patterns import PatternConfig, detect_patterns
from ops.quality.tuning import generate_tuning_proposal, apply_tuning_proposal
from ops.quality.thesis import (
    classify_signal_llm,
    batch_classify_missing_thesis,
    batch_refresh_latest_missing_provenance,
    generate_disagreement_report,
    iter_signal_ids_missing_latest_thesis_for_detected_window,
    iter_signal_ids_stale_latest_missing_provenance_for_detected_window,
    list_disagreement_candidates,
    refresh_signal_ids_missing_provenance,
)
from ops.quality.keys import suggest_key_strengthening, suggestions_to_markdown
from ops.quality.enrichment import enrich_signals_best_effort
from ops.quality.proposals import (
    propose_from_patterns,
    list_proposals,
    review_proposal,
    expire_stale_proposals,
)


def _default_db_path() -> str:
    return os.getenv("DISCOVERY_DB_PATH", "signals.db")


def register_quality_commands(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("quality", help="Quality ops (labels, stats, patterns, tuning)")
    p.add_argument("--db", dest="db_path", default=_default_db_path(), help="Path to signals SQLite DB")
    q = p.add_subparsers(dest="quality_cmd")

    # --------------------------------------------------------------------- label
    p_label = q.add_parser("label", help="Label a signal as TP/FP/UNSURE (manual)")
    p_label.add_argument("signal_id", type=int)
    p_label.add_argument("label", choices=["TP", "FP", "UNSURE", "ADJ"])
    p_label.add_argument("--by", dest="created_by", default=os.getenv("USER", "human"))
    p_label.add_argument("--reason", default=None)
    p_label.add_argument("--notes", default=None)
    p_label.set_defaults(func=_cmd_label)

    # --------------------------------------------------------------------- stats
    p_stats = q.add_parser("stats", help="Show quality stats over a time window")
    p_stats.add_argument("--days", type=int, default=30)
    p_stats.add_argument("--min-labeled", type=int, default=10)
    p_stats.set_defaults(func=_cmd_stats)

    # -------------------------------------------------------- sync-status-events
    p_sync = q.add_parser("sync-status-events", help="Run sync_suppression and record Notion status diffs as events")
    p_sync.add_argument("--baseline-new-keys", action="store_true", default=True)
    p_sync.add_argument("--no-baseline-new-keys", action="store_false", dest="baseline_new_keys")
    p_sync.set_defaults(func=_cmd_sync_status_events)

    # ------------------------------------------------------------ backfill-outcomes
    p_out = q.add_parser("backfill-outcomes", help="Infer TP/FP labels from notion_status_events")
    p_out.add_argument("--days-to-count", type=int, default=30)
    p_out.add_argument("--since-days", type=int, default=None)
    p_out.add_argument("--override-manual", action="store_true", default=False)
    p_out.set_defaults(func=_cmd_backfill_outcomes)

    # ------------------------------------------------------------ backfill-snapshot
    p_snap = q.add_parser("backfill-snapshot", help="Bootstrapping labels from suppression_cache snapshot statuses")
    p_snap.add_argument("--since-days", type=int, default=None)
    p_snap.add_argument("--override-manual", action="store_true", default=False)
    p_snap.add_argument("--conservative", action="store_true", default=False,
                        help="Conservative mode: only Passed->FP, Funded->TP (excludes Source/Sourced->TP)")
    p_snap.set_defaults(func=_cmd_backfill_snapshot)

    # --------------------------------------------------------------------- export
    p_exp = q.add_parser("export", help="Export labeled dataset (CSV or JSONL)")
    p_exp.add_argument("--days", type=int, default=90)
    p_exp.add_argument("--format", choices=["csv", "jsonl"], default="csv")
    p_exp.add_argument("--out", required=True)
    p_exp.set_defaults(func=_cmd_export)

    # ----------------------------------------------------------------- find-patterns
    p_pat = q.add_parser("find-patterns", help="Detect FP patterns and write JSON")
    p_pat.add_argument("--days", type=int, default=30)
    p_pat.add_argument("--min-count", type=int, default=10)
    p_pat.add_argument("--fp-rate-threshold", type=float, default=0.70)
    p_pat.add_argument("--out", required=True)
    p_pat.set_defaults(func=_cmd_find_patterns)

    # ------------------------------------------------------------- propose-tuning
    p_prop = q.add_parser("propose-tuning", help="Generate a tuning proposal YAML from pattern JSON")
    p_prop.add_argument("--patterns", required=True, help="Path to patterns JSON file")
    p_prop.add_argument("--out", required=True, help="Path to write proposal YAML")
    p_prop.add_argument("--window-days", type=int, default=30)
    p_prop.set_defaults(func=_cmd_propose_tuning)

    # --------------------------------------------------------------- apply-tuning
    p_apply = q.add_parser("apply-tuning", help="Apply auto-applicable actions from a tuning proposal")
    p_apply.add_argument("--proposal", required=True)
    p_apply.add_argument("--apply", action="store_true", default=False, help="Actually write files (otherwise dry-run)")
    p_apply.set_defaults(func=_cmd_apply_tuning)

    # -------------------------------------------------------------- thesis-classify
    p_tc = q.add_parser("thesis-classify", help="Run keyword + LLM thesis classification for a signal")
    p_tc.add_argument("signal_id", type=int)
    p_tc.add_argument("--model", default="gemini-2.0-flash")
    p_tc.add_argument("--prompt-version", default="quality-ops-v1")
    p_tc.set_defaults(func=_cmd_thesis_classify)

    # ---------------------------------------------------------- thesis-classify-batch
    p_tcb = q.add_parser("thesis-classify-batch", help="Batch classify recent signals missing thesis_classifications")
    p_tcb.add_argument("--days", type=int, default=30)
    p_tcb.add_argument("--limit", type=int, default=200)
    p_tcb.add_argument("--model", default="gemini-2.0-flash")
    p_tcb.add_argument("--prompt-version", default="quality-ops-v1")
    p_tcb.add_argument("--stop-on-error", action="store_true", default=False)
    p_tcb.set_defaults(func=_cmd_thesis_classify_batch)

    # --------------------------------------------------------- thesis-refresh-latest
    p_trl = q.add_parser(
        "thesis-refresh-latest",
        help="Append fresh thesis rows for the fixed 90-day created_at cohort whose latest row is missing model or prompt_version",
    )
    p_trl.add_argument("--limit", type=int, default=200)
    p_trl.add_argument("--model", default="gemini-2.0-flash")
    p_trl.add_argument("--prompt-version", default="quality-ops-v1")
    p_trl.add_argument("--stop-on-error", action="store_true", default=False)
    p_trl.set_defaults(func=_cmd_thesis_refresh_latest)

    # -------------------------------------------------------- thesis-disagreement-report
    p_dis = q.add_parser("thesis-disagreement-report", help="Report keyword vs LLM disagreements")
    p_dis.add_argument("--days", type=int, default=30)
    p_dis.add_argument("--keyword-threshold", type=float, default=0.40)
    p_dis.add_argument("--out", default=None)
    p_dis.set_defaults(func=_cmd_thesis_disagreement_report)

    # ------------------------------------------------------------- key-suggestions
    p_keys = q.add_parser("key-suggestions", help="Suggest strengthening name_loc canonical keys via domain extraction")
    p_keys.add_argument("--min-signals", type=int, default=5)
    p_keys.add_argument("--limit", type=int, default=100)
    p_keys.add_argument("--fp-only", action="store_true", default=False)
    p_keys.add_argument("--out", default=None)
    p_keys.set_defaults(func=_cmd_key_suggestions)

    # --------------------------------------------------------- propose-patterns
    p_pp = q.add_parser("propose-patterns", help="Auto-propose anti-pattern proposals from pattern JSON")
    p_pp.add_argument("--patterns", required=True, help="Path to patterns JSON file")
    p_pp.add_argument("--by", dest="proposed_by", default="system")
    p_pp.set_defaults(func=_cmd_propose_patterns)

    # --------------------------------------------------------- list-proposals
    p_lp = q.add_parser("list-proposals", help="List anti-pattern proposals")
    p_lp.add_argument("--status", choices=["proposed", "approved", "rejected", "expired", "applied"], default=None)
    p_lp.add_argument("--limit", type=int, default=50)
    p_lp.set_defaults(func=_cmd_list_proposals)

    # -------------------------------------------------------- review-proposal
    p_rp = q.add_parser("review-proposal", help="Approve/reject an anti-pattern proposal")
    p_rp.add_argument("proposal_id", type=int)
    p_rp.add_argument("--action", required=True, choices=["approved", "rejected", "expired"])
    p_rp.add_argument("--by", dest="reviewed_by", default=os.getenv("USER", "human"))
    p_rp.add_argument("--notes", default=None)
    p_rp.set_defaults(func=_cmd_review_proposal)

    # -------------------------------------------------------- expire-proposals
    p_ep = q.add_parser("expire-proposals", help="Auto-expire stale proposals past their expiry date")
    p_ep.set_defaults(func=_cmd_expire_proposals)

    # ---------------------------------------------------------------- enrichment
    p_enr = q.add_parser("enrich", help="Run stub enrichments for a list of signal ids")
    p_enr.add_argument("signal_ids", nargs="+", type=int)
    p_enr.set_defaults(func=_cmd_enrich)

    # -------------------------------------------------------------- adj-review
    p_adj = q.add_parser("adj-review", help="List ADJ-labeled signals for periodic review (re-label via 'quality label <id> TP|FP')")
    p_adj.add_argument("--days", type=int, default=90)
    p_adj.add_argument("--limit", type=int, default=50)
    p_adj.add_argument("--format", choices=["table", "json"], default="table", dest="out_format")
    p_adj.set_defaults(func=_cmd_adj_review)

    # --------------------------------------------------------- learning-loop
    p_loop = q.add_parser("learning-loop", help="Thin operator workflow for the learning-loop-only branch")
    loop_subs = p_loop.add_subparsers(dest="learning_loop_cmd")

    p_rs = loop_subs.add_parser("review-set", help="Build canonical review-set artifacts from disagreement + ADJ providers")
    p_rs.add_argument("--days", type=int, default=30)
    p_rs.add_argument("--adj-days", type=int, default=90)
    p_rs.add_argument("--limit", type=int, default=200)
    p_rs.add_argument("--out-json", required=True)
    p_rs.add_argument("--out-md", default=None)
    p_rs.set_defaults(func=_cmd_learning_loop_review_set)

    p_al = loop_subs.add_parser("apply-labels", help="Apply a validated batch of manual labels from canonical JSON")
    p_al.add_argument("--in-json", required=True)
    p_al.set_defaults(func=_cmd_learning_loop_apply_labels)

    p_rd = loop_subs.add_parser("rerun-diagnostic", help="Recompute the frozen router-diagnostic summary contract")
    p_rd.add_argument("--days", type=int, default=90, choices=[90])
    p_rd.add_argument("--out-dir", required=True)
    p_rd.add_argument("--model", default="gemini-2.0-flash")
    p_rd.add_argument("--prompt-version", default="quality-ops-v1")
    p_rd.set_defaults(func=_cmd_learning_loop_rerun_diagnostic)

    # --------------------------------------------------------- explain-score
    p_es = q.add_parser("explain-score", help="Explain confidence score breakdown for a company")
    p_es.add_argument("identifier", help="Canonical key (e.g. domain:acme.ai) or numeric signal ID")
    p_es.add_argument("--history", type=int, default=1, help="Number of historical evaluations to show")
    p_es.add_argument("--json", action="store_true", default=False, dest="json_output")
    p_es.add_argument("--include-dry-runs", action="store_true", default=False)
    p_es.set_defaults(func=_cmd_explain_score)


def _cmd_label(args: argparse.Namespace) -> None:
    if args.label == "ADJ" and not args.reason:
        print("Warning: ADJ labels benefit from a --reason explaining why (e.g. 'consumer hardware, interesting')")
    with quality_conn(args.db_path) as conn:
        feedback_id, upsert = label_signal_manual(
            conn,
            signal_id=args.signal_id,
            label=args.label,
            created_by=args.created_by,
            reason=args.reason,
            notes=args.notes,
        )
        print(f"feedback_id={feedback_id} signal_id={upsert.signal_id} label={upsert.human_label} source={upsert.label_source}")


def _cmd_stats(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        overall = get_overall_stats(conn, days=args.days)
        by_src = get_stats_by_source_api(conn, days=args.days, min_labeled=args.min_labeled)

        print(json.dumps({"overall": overall}, indent=2))
        print("")
        print("By source_api:")
        for s in by_src:
            print(f"- {s.source_api:24s} labeled={s.labeled_signals:5d} fp={s.fp:4d} tp={s.tp:4d} unsure={s.unsure:4d} adj={s.adj:4d} fp_rate={s.fp_rate:.2%}")


def _cmd_sync_status_events(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        stats = asyncio.run(
            sync_and_capture_status_events(
                conn,
                db_path=args.db_path,
                baseline_new_keys=bool(args.baseline_new_keys),
            )
        )
        print(json.dumps(stats.__dict__, indent=2))


def _cmd_backfill_outcomes(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        stats = backfill_outcomes_from_events(
            conn,
            days_to_count=args.days_to_count,
            since_days=args.since_days,
            override_manual=bool(args.override_manual),
        )
        print(json.dumps(stats.__dict__, indent=2))


def _cmd_backfill_snapshot(args: argparse.Namespace) -> None:
    if getattr(args, "conservative", False):
        mapping = {
            "Passed": "FP",
            "Funded": "TP",
        }
    else:
        mapping = {
            # Bootstrapping defaults (aligned with task_plan.md):
            "Passed": "FP",
            "Funded": "TP",
            "Sourced": "TP",
            "Source": "TP",
        }
    with quality_conn(args.db_path) as conn:
        labeled = backfill_from_snapshot_status(
            conn,
            mapping=mapping,
            since_days=args.since_days,
            override_manual=bool(args.override_manual),
        )
        print(json.dumps({"labeled": labeled, "mapping": mapping}, indent=2))


def _cmd_export(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        out_path = Path(args.out)
        if args.format == "csv":
            n = export_dataset_csv(conn, out_path=out_path, days=args.days)
        else:
            n = export_dataset_jsonl(conn, out_path=out_path, days=args.days)
        print(json.dumps({"exported": n, "out": str(out_path), "format": args.format}, indent=2))


def _cmd_find_patterns(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        cfg = PatternConfig(days=args.days, min_count=args.min_count, fp_rate_threshold=args.fp_rate_threshold)
        pats = detect_patterns(conn, config=cfg)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"config": cfg.__dict__, "patterns": pats}, indent=2), encoding="utf-8")
        print(json.dumps({"patterns": len(pats), "out": str(out_path)}, indent=2))


def _cmd_propose_tuning(args: argparse.Namespace) -> None:
    patterns_doc = json.loads(Path(args.patterns).read_text(encoding="utf-8"))
    patterns = patterns_doc.get("patterns", []) if isinstance(patterns_doc, dict) else []
    proposal = generate_tuning_proposal(patterns=patterns, window_days=args.window_days, out_path=args.out)
    print(json.dumps({"actions": len(proposal.get("actions", [])), "notes": len(proposal.get("notes", [])), "out": args.out}, indent=2))


def _cmd_apply_tuning(args: argparse.Namespace) -> None:
    summary = apply_tuning_proposal(proposal_path=args.proposal, repo_root=".", dry_run=(not bool(args.apply)))
    print(json.dumps(summary, indent=2))


def _cmd_thesis_classify(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        r = classify_signal_llm(conn, signal_id=args.signal_id, model=args.model, prompt_version=args.prompt_version)
        print(json.dumps(r.__dict__, indent=2))


def _cmd_thesis_classify_batch(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        summary = batch_classify_missing_thesis(
            conn,
            days=args.days,
            limit=args.limit,
            model=args.model,
            prompt_version=args.prompt_version,
            stop_on_error=bool(args.stop_on_error),
        )
        print(json.dumps(summary, indent=2))


def _cmd_thesis_refresh_latest(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        summary = batch_refresh_latest_missing_provenance(
            conn,
            limit=args.limit,
            model=args.model,
            prompt_version=args.prompt_version,
            stop_on_error=bool(args.stop_on_error),
        )
        print(json.dumps(summary, indent=2))


def _cmd_thesis_disagreement_report(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        report = generate_disagreement_report(
            conn,
            days=args.days,
            keyword_threshold=args.keyword_threshold,
            out_path=args.out,
        )
        if args.out:
            print(json.dumps({"out": args.out}, indent=2))
        else:
            print(report)


def _cmd_key_suggestions(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        suggestions = suggest_key_strengthening(
            conn,
            min_signals=args.min_signals,
            limit=args.limit,
            fp_only=bool(args.fp_only),
        )
        md = suggestions_to_markdown(suggestions)
        if args.out:
            Path(args.out).write_text(md, encoding="utf-8")
            print(json.dumps({"out": args.out, "suggestions": len(suggestions)}, indent=2))
        else:
            print(md)


def _cmd_propose_patterns(args: argparse.Namespace) -> None:
    patterns_doc = json.loads(Path(args.patterns).read_text(encoding="utf-8"))
    patterns = patterns_doc.get("patterns", []) if isinstance(patterns_doc, dict) else []
    with quality_conn(args.db_path) as conn:
        created = propose_from_patterns(conn, patterns, proposed_by=args.proposed_by)
        print(json.dumps({"proposed": created, "total_patterns": len(patterns)}, indent=2))


def _cmd_list_proposals(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        proposals = list_proposals(conn, status=args.status, limit=args.limit)
        for p in proposals:
            status_badge = {"proposed": "?", "approved": "+", "rejected": "-", "expired": "~", "applied": "*"}.get(p.status, " ")
            print(f"  [{status_badge}] #{p.id:4d}  {p.pattern_type:30s}  {p.pattern_key:30s}  conf={p.confidence:.2f}  {p.status}")
        print(f"\n  Total: {len(proposals)}")


def _cmd_review_proposal(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        ok = review_proposal(conn, args.proposal_id, args.action, args.reviewed_by, args.notes)
        if ok:
            print(json.dumps({"proposal_id": args.proposal_id, "new_status": args.action}, indent=2))
        else:
            print(f"Proposal #{args.proposal_id} not found or already decided.")


def _cmd_expire_proposals(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        expired = expire_stale_proposals(conn)
        print(json.dumps({"expired": expired}, indent=2))


def _cmd_enrich(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        results = enrich_signals_best_effort(conn, signal_ids=list(map(int, args.signal_ids)))
        print(json.dumps({"results": results}, indent=2))


def _cmd_adj_review(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        candidates = list_adj_review_candidates(conn, days=args.days, limit=args.limit)

        if args.out_format == "json":
            data = [
                {
                    "signal_id": c.signal_id,
                    "company_name": c.company_name,
                    "source_api": c.source_api,
                    "confidence": c.confidence,
                    "canonical_key": c.canonical_key,
                    "detected_at": c.detected_at,
                    "labeled_at": c.labeled_at,
                    "labeled_by": c.labeled_by,
                    "reason": c.reason_summary,
                }
                for c in candidates
            ]
            print(json.dumps(data, indent=2))
        else:
            if not candidates:
                print("No ADJ-labeled signals found in the last {} days.".format(args.days))
                print("Tip: re-label with 'quality label <id> TP|FP' after review.")
                return
            print(f"{'ID':>6}  {'Company':30s}  {'Source':16s}  {'Conf':>5}  {'Labeled At':25s}  {'By':10s}  {'Reason'}")
            print("-" * 120)  # table-width separator
            for c in candidates:
                print(
                    f"{c.signal_id:>6}  {c.company_name[:30]:30s}  "
                    f"{c.source_api:16s}  {c.confidence:5.2f}  "
                    f"{c.labeled_at[:25]:25s}  {(c.labeled_by or '')[:10]:10s}  "
                    f"{c.reason_summary[:50]}"
                )
            print(f"\n{len(candidates)} ADJ signal(s). Re-label with: quality label <id> TP|FP")


def _build_review_set_payload(
    *,
    db_path: str,
    disagreement_candidates: list[Any],
    adj_candidates: list[Any],
    window_days: int,
) -> Dict[str, Any]:
    items: list[Dict[str, Any]] = []
    for c in disagreement_candidates:
        items.append(
            {
                "signal_id": c.signal_id,
                "queue_type": c.queue_type,
                "canonical_key": c.canonical_key,
                "company_name": c.company_name,
                "source_api": c.source_api,
                "detected_at": c.detected_at,
                "priority_rank": c.priority_rank,
                "reason_code": c.reason_code,
                "reason_summary": c.reason_summary,
            }
        )
    for c in adj_candidates:
        items.append(
            {
                "signal_id": c.signal_id,
                "queue_type": c.queue_type,
                "canonical_key": c.canonical_key,
                "company_name": c.company_name,
                "source_api": c.source_api,
                "detected_at": c.detected_at,
                "priority_rank": c.priority_rank,
                "reason_code": c.reason_code,
                "reason_summary": c.reason_summary,
            }
        )

    items.sort(key=lambda i: i["signal_id"], reverse=True)
    items.sort(key=lambda i: i["detected_at"], reverse=True)
    items.sort(key=lambda i: i["priority_rank"])
    items.sort(key=lambda i: i["queue_type"])
    return {
        "schema_version": "learning_loop_review_set.v1",
        "generated_at": utc_now_iso(),
        "db_path": db_path,
        "window_days": window_days,
        "sort_key": ["queue_type", "priority_rank", "detected_at", "signal_id"],
        "items": items,
    }


def _validate_review_set_payload(payload: Dict[str, Any]) -> None:
    required_top = {"schema_version", "generated_at", "db_path", "window_days", "sort_key", "items"}
    if set(payload.keys()) != required_top:
        raise ValueError("review-set payload keys are invalid")
    if payload["schema_version"] != "learning_loop_review_set.v1":
        raise ValueError("review-set schema_version must be learning_loop_review_set.v1")
    if payload["sort_key"] != ["queue_type", "priority_rank", "detected_at", "signal_id"]:
        raise ValueError("review-set sort_key must be canonical")
    items = payload["items"]
    if not isinstance(items, list):
        raise ValueError("review-set items must be a list")
    required_item = {
        "signal_id",
        "queue_type",
        "canonical_key",
        "company_name",
        "source_api",
        "detected_at",
        "priority_rank",
        "reason_code",
        "reason_summary",
    }
    for item in items:
        if set(item.keys()) != required_item:
            raise ValueError("review-set item keys are invalid")
        if item["queue_type"] not in {"disagreement", "adj"}:
            raise ValueError("review-set queue_type must be disagreement or adj")
    canonical = list(items)
    canonical.sort(key=lambda i: i["signal_id"], reverse=True)
    canonical.sort(key=lambda i: i["detected_at"], reverse=True)
    canonical.sort(key=lambda i: i["priority_rank"])
    canonical.sort(key=lambda i: i["queue_type"])
    if items != canonical:
        raise ValueError("review-set items must be in canonical sort order")


def _render_review_set_markdown(payload: Dict[str, Any]) -> str:
    md = [
        "# Learning Loop Review Set",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- db_path: `{payload['db_path']}`",
        f"- window_days: `{payload['window_days']}`",
        f"- items: `{len(payload['items'])}`",
        "",
        "| signal_id | queue_type | priority_rank | source_api | canonical_key | reason_code | reason_summary |",
        "|---:|---|---:|---|---|---|---|",
    ]
    for item in payload["items"]:
        md.append(
            f"| {item['signal_id']} | {item['queue_type']} | {item['priority_rank']} | "
            f"{item['source_api']} | {item['canonical_key']} | {item['reason_code']} | {item['reason_summary']} |"
        )
    return "\n".join(md) + "\n"


def _validate_apply_labels_payload(payload: Dict[str, Any]) -> None:
    required_top = {"schema_version", "requested_by", "requested_at", "sort_key", "items"}
    if set(payload.keys()) != required_top:
        raise ValueError("apply-labels payload keys are invalid")
    if payload["schema_version"] != "learning_loop_apply_labels.v1":
        raise ValueError("apply-labels schema_version must be learning_loop_apply_labels.v1")
    if payload["sort_key"] != ["signal_id"]:
        raise ValueError("apply-labels sort_key must be canonical")
    items = payload["items"]
    if not isinstance(items, list):
        raise ValueError("apply-labels items must be a list")
    seen: set[int] = set()
    last_signal_id: Optional[int] = None
    for item in items:
        if not {"signal_id", "label", "created_by", "reason"}.issubset(item.keys()):
            raise ValueError("apply-labels item missing required fields")
        sid = int(item["signal_id"])
        if sid in seen:
            raise ValueError("apply-labels contains duplicate signal_id values")
        seen.add(sid)
        if item["label"] not in {"TP", "FP", "UNSURE", "ADJ"}:
            raise ValueError("apply-labels item label is invalid")
        if last_signal_id is not None and sid < last_signal_id:
            raise ValueError("apply-labels items must be sorted by signal_id ASC")
        last_signal_id = sid


def _resolve_gp_runner() -> str:
    runner = os.getenv("GP_RUNNER")
    if runner:
        return runner
    try:
        import getpass

        return getpass.getuser() or "unknown"
    except Exception:
        return "unknown"


def _cmd_learning_loop_review_set(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        disagreements = list_disagreement_candidates(conn, days=args.days, limit=args.limit)
        adjs = list_adj_review_candidates(conn, days=args.adj_days, limit=args.limit)
        payload = _build_review_set_payload(
            db_path=args.db_path,
            disagreement_candidates=disagreements,
            adj_candidates=adjs,
            window_days=max(args.days, args.adj_days),
        )
        _validate_review_set_payload(payload)

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(_render_review_set_markdown(payload), encoding="utf-8")
    # GP workload logging — best-effort, never fail the CLI.
    try:
        from ops.gp_workload import log_review_set_generated

        log_review_set_generated(
            items_count=len(payload["items"]),
            window_days=max(args.days, args.adj_days),
            runner=_resolve_gp_runner(),
        )
    except Exception:
        pass
    print(json.dumps({"out_json": str(out_json), "out_md": args.out_md, "items": len(payload["items"])}, indent=2))


def _cmd_learning_loop_apply_labels(args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.in_json).read_text(encoding="utf-8"))
    _validate_apply_labels_payload(payload)

    applied: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    with quality_conn(args.db_path) as conn:
        for item in payload["items"]:
            try:
                feedback_id, upsert = label_signal_manual(
                    conn,
                    signal_id=int(item["signal_id"]),
                    label=str(item["label"]),
                    created_by=str(item["created_by"]),
                    reason=str(item["reason"]),
                    notes=str(item.get("notes")) if item.get("notes") is not None else None,
                )
                applied.append(
                    {
                        "signal_id": upsert.signal_id,
                        "feedback_id": feedback_id,
                        "label": upsert.human_label,
                        "source": upsert.label_source,
                        "overwritten": upsert.overwritten,
                    }
                )
            except Exception as exc:
                errors.append({"signal_id": int(item["signal_id"]), "error": str(exc)})

    # GP workload logging — best-effort, never fail the CLI.
    try:
        from ops.gp_workload import log_labels_applied

        log_labels_applied(
            attempted=len(payload["items"]),
            succeeded=len(applied),
            failed=len(errors),
            runner=_resolve_gp_runner(),
        )
    except Exception:
        pass
    print(json.dumps({"attempted": len(payload["items"]), "succeeded": len(applied), "failed": len(errors), "applied": applied, "errors": errors}, indent=2))


def _render_router_diagnostic_markdown(summary: Dict[str, Any]) -> str:
    quality = summary["quality_stats"]
    disc = summary["discrimination"]
    branch = summary["branch_recommendation"]
    lines = [
        "# Router Diagnostic Rerun",
        "",
        f"- date: `{summary['date']}`",
        f"- db_path: `{summary['db_path']}`",
        f"- window_days: `{summary['window_days']}`",
        f"- branch: `{branch['name']}`",
        "",
        "## Quality Stats",
        "",
        f"- labeled: `{int(quality['labeled'])}`",
        f"- decided: `{int(quality['decided'])}`",
        f"- tp: `{int(quality['tp'])}`",
        f"- fp: `{int(quality['fp'])}`",
        f"- unsure: `{int(quality['unsure'])}`",
        f"- adj: `{int(quality['adj'])}`",
        f"- fp_rate: `{quality['fp_rate']}`",
        "",
        "## Join Coverage",
        "",
        f"- decisive_joined_rows: `{summary['join_coverage']['decisive_joined_rows']}`",
        f"- tp_rows: `{summary['join_coverage']['tp_rows']}`",
        f"- fp_rows: `{summary['join_coverage']['fp_rows']}`",
        f"- latest_row_mismatches: `{summary['join_coverage']['latest_row_mismatches']}`",
        "",
        "## Discrimination",
        "",
        f"- auc: `{disc['auc']}`",
        f"- tp_mean: `{disc['tp_mean']}`",
        f"- fp_mean: `{disc['fp_mean']}`",
        f"- mean_separation: `{disc['mean_separation']}`",
        f"- score_max: `{disc['score_max']}`",
        f"- threshold_0_7: `{json.dumps(disc['threshold_0_7'])}`",
        "",
        "## Branch Recommendation",
        "",
    ]
    for reason in branch["reason"]:
        lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def _noncomputable_diagnostic_summary(
    *,
    db_path: str,
    days: int,
    quality_stats: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    return {
        "date": utc_now_iso()[:10],
        "db_path": db_path,
        "window_days": days,
        "quality_stats": {
            "labeled": int(quality_stats["labeled"]),
            "decided": int(quality_stats["decided"]),
            "tp": int(quality_stats["tp"]),
            "fp": int(quality_stats["fp"]),
            "unsure": int(quality_stats["unsure"]),
            "adj": int(quality_stats["adj"]),
            "fp_rate": float(quality_stats["fp_rate"]),
        },
        "join_coverage": {
            "decisive_joined_rows": 0,
            "tp_rows": 0,
            "fp_rows": 0,
            "latest_row_mismatches": 0,
        },
        "discrimination": {
            "auc": None,
            "tp_mean": None,
            "fp_mean": None,
            "mean_separation": None,
            "score_max": None,
            "threshold_0_7": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
        },
        "branch_recommendation": {
            "name": "diagnostic_cannot_be_computed",
            "reason": [reason],
        },
        "reproduction": {
            "quality_stats_command": f"python -m ops.cli quality --db {db_path} stats --days {days}",
            "notes": [
                "Diagnostic failed closed before parity computation.",
                "Resolve thesis provenance or integrity issues before inferring another branch.",
            ],
        },
    }


def _cmd_learning_loop_rerun_diagnostic(args: argparse.Namespace) -> None:
    with quality_conn(args.db_path) as conn:
        if args.days != 90:
            raise ValueError("rerun-diagnostic is locked to the approved 90-day parity window")
        missing_ids = iter_signal_ids_missing_latest_thesis_for_detected_window(conn, days=args.days, limit=None)
        stale_ids = iter_signal_ids_stale_latest_missing_provenance_for_detected_window(conn, days=args.days, limit=None)
        refresh_ids = sorted(set(missing_ids + stale_ids))
        if refresh_ids:
            if len(refresh_ids) > 200:
                summary = _noncomputable_diagnostic_summary(
                    db_path=args.db_path,
                    days=args.days,
                    quality_stats=get_overall_stats(conn, days=args.days),
                    reason="missing or stale latest thesis provenance exceeds the bounded refresh set for a parity-safe rerun",
                )
            else:
                try:
                    refresh = refresh_signal_ids_missing_provenance(
                        conn,
                        signal_ids=refresh_ids,
                        model=args.model,
                        prompt_version=args.prompt_version,
                        stop_on_error=False,
                    )
                except Exception as exc:
                    summary = _noncomputable_diagnostic_summary(
                        db_path=args.db_path,
                        days=args.days,
                        quality_stats=get_overall_stats(conn, days=args.days),
                        reason=f"stale latest thesis provenance present and refresh failed: {exc}",
                    )
                else:
                    remaining_stale = iter_signal_ids_stale_latest_missing_provenance_for_detected_window(
                        conn,
                        days=args.days,
                        limit=1,
                    )
                    remaining_missing = iter_signal_ids_missing_latest_thesis_for_detected_window(
                        conn,
                        days=args.days,
                        limit=1,
                    )
                    if refresh["failed"] > 0 or remaining_stale or remaining_missing:
                        summary = _noncomputable_diagnostic_summary(
                            db_path=args.db_path,
                            days=args.days,
                            quality_stats=get_overall_stats(conn, days=args.days),
                            reason="missing or stale latest thesis provenance remained after bounded refresh; rerun failed closed",
                        )
                    else:
                        summary = build_router_diagnostic_summary(conn, db_path=args.db_path, days=args.days)
        else:
            summary = build_router_diagnostic_summary(conn, db_path=args.db_path, days=args.days)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_render_router_diagnostic_markdown(summary), encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), "branch": summary["branch_recommendation"]["name"]}, indent=2))


def _cmd_explain_score(args: argparse.Namespace) -> None:
    import re

    identifier = args.identifier
    with quality_conn(args.db_path) as conn:
        canonical_key = None
        company_id = None
        resolution_note = None

        # Auto-detect numeric signal ID vs canonical key
        if re.match(r"^\d+$", identifier):
            sig_id = int(identifier)
            row = conn.execute(
                "SELECT canonical_key, company_id FROM signals WHERE id = ? LIMIT 1",
                (sig_id,),
            ).fetchone()
            if not row:
                print(f"Signal ID {sig_id} not found")
                return
            if row["company_id"]:
                company_id = row["company_id"]
                resolution_note = f"Resolved signal {sig_id} -> company {company_id} (showing entity history)"
            else:
                canonical_key = row["canonical_key"]
                resolution_note = f"Resolved signal {sig_id} -> {canonical_key} (showing entity history, no company_id)"
        else:
            canonical_key = identifier

        # Query ledger
        conditions = []
        params: list = []
        if canonical_key:
            conditions.append("canonical_key = ?")
            params.append(canonical_key)
        else:
            conditions.append("company_id = ?")
            params.append(company_id)

        if not args.include_dry_runs:
            conditions.append("is_dry_run = 0")

        where = " AND ".join(conditions)
        params.append(args.history)

        rows = conn.execute(
            f"""
            SELECT
                id, execution_id, canonical_key, company_id,
                evaluation_origin, is_dry_run, breakdown_kind,
                gate_score, reported_score,
                base_score, multi_source_boost, convergence_boost,
                founder_boost, velocity_boost, enrichment_boost,
                community_sentiment_boost, recalibration_factor,
                policy_version, breakdown_schema_version,
                signals_contributing, sources_checked,
                decision, verification_status, reason,
                breakdown_json, details_json, signal_ids_json,
                routing_config_json,
                evaluated_at, created_at
            FROM confidence_ledger
            WHERE {where}
            ORDER BY evaluated_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        if not rows:
            label = canonical_key or f"company_id={company_id}"
            print(f"No evaluations recorded for {label}")
            print("Note: this company may pre-date the confidence ledger (v51 migration)")
            return

        if resolution_note:
            print(resolution_note)
            print()

        if args.json_output:
            data = []
            for r in rows:
                entry = dict(r)
                # Parse JSON fields for clean output
                for jf in ("breakdown_json", "details_json", "signal_ids_json", "routing_config_json"):
                    if entry[jf]:
                        entry[jf] = json.loads(entry[jf])
                data.append(entry)
            print(json.dumps(data, indent=2))
            return

        # Table (waterfall) rendering
        for r in rows:
            _render_evaluation(r)
            print()

        print("  [pipeline-only: pusher re-evaluation and operator overrides not captured]")


def _render_evaluation(r: dict) -> None:
    """Render a single ledger row as a waterfall table."""
    row_id = r["id"]
    evaluated_at = r["evaluated_at"]
    decision = r["decision"]
    breakdown_kind = r["breakdown_kind"]
    verification_status = r["verification_status"]
    reason = r["reason"]
    gate_score = r["gate_score"]
    reported_score = r["reported_score"]
    canonical_key = r["canonical_key"]
    company_id = r["company_id"]
    policy_version = r["policy_version"]
    schema_version = r["breakdown_schema_version"]
    signals_contributing = r["signals_contributing"]
    sources_checked = r["sources_checked"]

    print(f"Evaluation #{row_id} ({evaluated_at})")
    print("=" * 50)

    if breakdown_kind == "hard_kill":
        print(f"  Decision:        reject (hard_kill)")
    else:
        print(f"  Decision:        {decision} ({verification_status})")
    print(f"  Reason:          {reason}")

    if breakdown_kind == "hard_kill":
        print(f"  Gate Score:      {gate_score:.3f}")
        company_line = f"  Company:         {canonical_key}"
        if company_id:
            company_line += f" (company_id: {company_id})"
        print(company_line)
        print()
        print("  [No scoring waterfall -- hard kill bypasses confidence calculation]")
        bd = json.loads(r["breakdown_json"])
        kill_signal = bd.get("kill_signal", "unknown")
        print(f"  Kill Signal:     {kill_signal}")
    elif breakdown_kind == "empty_signals":
        print(f"  Gate Score:      {gate_score:.3f}")
        print()
        print("  [No signals provided -- empty evaluation]")
    else:
        # Normal path — show gate_score and reported_score
        if abs(gate_score - reported_score) > 0.001:
            diff = reported_score - gate_score
            sign = "+" if diff >= 0 else ""
            print(f"  Gate Score:      {gate_score:.3f}  <- decision based on this")
            print(f"  Reported Score:  {reported_score:.3f}  <- sent to Notion (LLM adj: {sign}{diff:.3f})")
        else:
            print(f"  Gate Score:      {gate_score:.3f}  <- decision based on this")
            print(f"  Reported Score:  {reported_score:.3f}  <- sent to Notion (post-LLM)")

        company_line = f"  Company:         {canonical_key}"
        if company_id:
            company_line += f" (company_id: {company_id})"
        print(company_line)
        print()

        # Waterfall
        base = r["base_score"]
        msb = r["multi_source_boost"]
        cb = r["convergence_boost"]
        fb = r["founder_boost"]
        vb = r["velocity_boost"]
        eb = r["enrichment_boost"]
        csb = r["community_sentiment_boost"]
        recal = r["recalibration_factor"]

        running = base
        print("  -- Waterfall ------------------------------------")
        print(f"  Base Score:      {base:.3f}")
        running *= msb
        print(f"  Multi-Source:    x{msb:.2f}   -> {running:.3f}")
        running *= cb
        print(f"  Convergence:     x{cb:.2f}   -> {running:.3f}")
        running += fb
        print(f"  Founder Boost:   +{fb:.3f}  -> {running:.3f}")
        running += vb
        print(f"  Velocity Boost:  +{vb:.3f}  -> {running:.3f}")
        running += eb
        print(f"  Enrichment:      +{eb:.3f}  -> {running:.3f}")
        running += csb
        print(f"  Community:       +{csb:.3f}  -> {running:.3f}")
        print(f"  Raw Overall:     {running:.3f}")
        recalibrated = running * recal
        print(f"  Recalibration:   x{recal:.2f}   -> {recalibrated:.3f}")
        capped = min(recalibrated, 1.0)
        if capped < recalibrated:
            print(f"  Cap (1.0):       applied -> {capped:.3f}")
        else:
            print("  Cap (1.0):       not applied")
        print("  -------------------------------------------------")
        print()

        print(f"  Policy Version:  {policy_version}")

        if schema_version != "1.0":
            print(f"  [Schema v{schema_version} evaluation -- current schema is v1.0]")

        print(f"  Signals:         {signals_contributing} types, {sources_checked} sources")

        signal_ids = json.loads(r["signal_ids_json"])
        print(f"  Input Signal IDs: {signal_ids}")

        # Signal details from breakdown_json
        bd = json.loads(r["breakdown_json"])
        sig_details = bd.get("signal_details", [])
        if sig_details:
            print()
            print("  Signal Details:")
            for sd in sig_details:
                stype = sd.get("type", "unknown")
                contrib = sd.get("contribution", 0)
                src = sd.get("source", "unknown")
                print(f"    {stype:24s} contribution={contrib:.4f} (source={src})")
