"""
Async enrichment runner (stub).

The repo currently contains placeholder clients:
- enrichment/brand_sentiment.py
- enrichment/community_metrics.py

This module provides a consistent runner interface so a future Phase 3 can:
- enqueue async enrichment tasks
- persist enrichment results
- trigger enrichment based on quality/pattern findings

For now:
- We can run enrichments in-process and output JSON.
- Storage is not implemented (depends on schema decisions).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from enrichment.brand_sentiment import BrandSentimentClient
from enrichment.community_metrics import CommunityMetricsClient


@dataclass(frozen=True)
class EnrichmentResult:
    signal_id: int
    canonical_key: str
    brand_sentiment: Optional[Dict[str, Any]]
    community_metrics: Optional[Dict[str, Any]]


async def enrich_signal(
    *,
    signal_id: int,
    canonical_key: str,
    text: str,
    domain: Optional[str] = None,
    enable_brand_sentiment: bool = True,
    enable_community_metrics: bool = True,
) -> EnrichmentResult:
    brand = None
    community = None

    if enable_brand_sentiment:
        brand_client = BrandSentimentClient()
        score, labels = await brand_client.analyze_brand_sentiment(text)
        brand = {"score": score, "labels": labels}

    if enable_community_metrics and domain:
        comm_client = CommunityMetricsClient()
        community = await comm_client.get_community_metrics(domain)

    return EnrichmentResult(
        signal_id=signal_id,
        canonical_key=canonical_key,
        brand_sentiment=brand,
        community_metrics=community,
    )


def enrich_signals_best_effort(
    conn: sqlite3.Connection,
    *,
    signal_ids: List[int],
    domain_fallback: bool = True,
) -> List[Dict[str, Any]]:
    """
    Enrich a list of signals and return JSON-serializable results.
    """
    rows = conn.execute(
        "SELECT id, canonical_key, company_name, raw_data FROM signals WHERE id IN ({})".format(",".join(["?"] * len(signal_ids))),
        signal_ids,
    ).fetchall()

    async def _run() -> List[Dict[str, Any]]:
        tasks = []
        for r in rows:
            text = ""
            if r["company_name"]:
                text += str(r["company_name"])
            if r["raw_data"]:
                text += "\n" + str(r["raw_data"])[:2000]  # bounded
            tasks.append(
                enrich_signal(
                    signal_id=int(r["id"]),
                    canonical_key=str(r["canonical_key"]),
                    text=text,
                    domain=None,
                )
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: List[Dict[str, Any]] = []
        for res in results:
            if isinstance(res, Exception):
                out.append({"error": str(res)})
                continue
            out.append(
                {
                    "signal_id": res.signal_id,
                    "canonical_key": res.canonical_key,
                    "brand_sentiment": res.brand_sentiment,
                    "community_metrics": res.community_metrics,
                }
            )
        return out

    return asyncio.run(_run())
