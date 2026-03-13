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

from ops.quality.db import quality_conn
from ops.quality.labels import label_signal_manual
from ops.quality.stats import get_overall_stats, get_stats_by_source_api
from ops.quality.status_events import sync_and_capture_status_events
from ops.quality.outcomes import backfill_outcomes_from_events, backfill_from_snapshot_status
from ops.quality.export import export_dataset_csv, export_dataset_jsonl
from ops.quality.patterns import PatternConfig, detect_patterns
from ops.quality.tuning import generate_tuning_proposal, apply_tuning_proposal
from ops.quality.thesis import (
    classify_signal_llm,
    batch_classify_missing_thesis,
    generate_disagreement_report,
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
    from ops.quality.stats import _iso_days_ago

    since = _iso_days_ago(args.days)
    with quality_conn(args.db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                sqm.signal_id,
                s.company_name,
                s.source_api,
                s.confidence,
                s.canonical_key,
                sqm.labeled_at,
                sqm.labeled_by,
                sqm.notes,
                json_extract(sqm.metadata, '$.reason') AS reason
            FROM signal_quality_metrics sqm
            JOIN signals s ON s.id = sqm.signal_id
            WHERE sqm.human_label = 'ADJ'
              AND sqm.labeled_at >= ?
            ORDER BY sqm.labeled_at DESC
            LIMIT ?
            """,
            (since, args.limit),
        ).fetchall()

        if args.out_format == "json":
            data = [dict(r) for r in rows]
            print(json.dumps(data, indent=2))
        else:
            if not rows:
                print("No ADJ-labeled signals found in the last {} days.".format(args.days))
                print("Tip: re-label with 'quality label <id> TP|FP' after review.")
                return
            print(f"{'ID':>6}  {'Company':30s}  {'Source':16s}  {'Conf':>5}  {'Labeled At':25s}  {'By':10s}  {'Reason'}")
            print("-" * 120)  # table-width separator
            for r in rows:
                reason = r["reason"] or r["notes"] or ""
                print(
                    f"{r['signal_id']:>6}  {(r['company_name'] or '')[:30]:30s}  "
                    f"{r['source_api']:16s}  {r['confidence']:5.2f}  "
                    f"{(r['labeled_at'] or '')[:25]:25s}  {(r['labeled_by'] or '')[:10]:10s}  "
                    f"{reason[:50]}"
                )
            print(f"\n{len(rows)} ADJ signal(s). Re-label with: quality label <id> TP|FP")
