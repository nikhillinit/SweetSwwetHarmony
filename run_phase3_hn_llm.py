"""Phase 3: HN-only LLM thesis validation on scratch DB.

Bypasses the pipeline's hardcoded ThesisFilterConfig to set
THESIS_SKIP_LLM_BELOW=0.0, forcing LLM to run on keyword_score=0 signals.
"""
import asyncio
import json
import os
import sqlite3
import sys

from dotenv import load_dotenv
load_dotenv()

# Force overrides AFTER dotenv
os.environ["THESIS_SKIP_LLM_BELOW"] = "0.0"
os.environ["LLM_THESIS_MODE"] = "active"
os.environ["DISCOVERY_DB_PATH"] = "scratch_phase3.db"

from utils.thesis_filter import ThesisFilter, ThesisFilterConfig


async def main():
    # Create config from env (reads THESIS_SKIP_LLM_BELOW=0.0)
    config = ThesisFilterConfig.from_env()
    print(f"skip_llm_if_keyword_below = {config.skip_llm_if_keyword_below}")
    print(f"LLM_THESIS_MODE = {os.environ.get('LLM_THESIS_MODE')}")
    print(f"GOOGLE_API_KEY present = {bool(os.environ.get('GOOGLE_API_KEY'))}")
    print()

    tf = ThesisFilter(config=config)

    # Fetch pending HN signals from scratch DB
    db = sqlite3.connect("scratch_phase3.db")
    db.row_factory = sqlite3.Row
    rows = db.execute("""
        SELECT DISTINCT s.id, s.company_name, s.raw_data, s.confidence, s.canonical_key
        FROM signals s
        INNER JOIN signal_processing p ON s.id = p.signal_id
        WHERE p.status = 'pending' AND s.source_api = 'hacker_news'
        ORDER BY s.detected_at DESC
    """).fetchall()
    print(f"Pending HN signals: {len(rows)}")
    print("=" * 80)

    results = []
    for i, r in enumerate(rows):
        raw = json.loads(r["raw_data"]) if r["raw_data"] and isinstance(r["raw_data"], str) else {}
        title = raw.get("title", raw.get("hn_title", ""))
        description = raw.get("description", raw.get("text", title))

        print(f"\n[{i+1}/{len(rows)}] {r['company_name']}: {title[:60]}")

        result = await tf.classify(
            description or title,
            company_name=r["company_name"],
            skip_llm=False,
        )

        entry = {
            "signal_id": r["id"],
            "company_name": r["company_name"],
            "title": title[:80],
            "canonical_key": r["canonical_key"],
            "routing": result.routing.value,
            "keyword_score": result.keyword_score,
            "keyword_category": result.keyword_category,
            "llm_skipped": result.llm_skipped,
            "llm_category": getattr(result, "llm_category", None),
            "llm_score": getattr(result, "llm_score", None),
            "llm_rationale": getattr(result, "llm_rationale", None),
        }
        results.append(entry)

        status = "REJECTED" if result.routing.value == "rejected" else "HELD" if result.routing.value == "held" else "PASSED"
        llm_info = f"llm={entry['llm_category']}" if not result.llm_skipped else "llm=SKIPPED"
        print(f"  -> {status} | kw={result.keyword_score:.2f} | {llm_info}")
        if entry["llm_rationale"]:
            print(f"     rationale: {entry['llm_rationale'][:100]}")

    db.close()

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    rejected = sum(1 for r in results if r["routing"] == "rejected")
    held = sum(1 for r in results if r["routing"] == "held")
    passed = sum(1 for r in results if r["routing"] in ("qualified", "passed"))
    llm_ran = sum(1 for r in results if not r["llm_skipped"])
    print(f"Total: {len(results)}")
    print(f"  Rejected: {rejected}")
    print(f"  Held:     {held}")
    print(f"  Passed:   {passed}")
    print(f"  LLM ran:  {llm_ran}/{len(results)}")

    # Save results
    out_path = "artifacts/activation/phase3_hn_llm_results_2026-03-25.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
