"""
Canonical key remediation helpers.

Scope:
- Detect "weak" canonical keys (name_loc:...) that correlate with duplicates / FPs
- Suggest stronger keys by extracting domains/websites from raw_data

This module is conservative:
- It does NOT automatically rewrite existing canonical keys (that is a high-risk migration)
- It produces a suggestion report that a human (or a dedicated migration) can apply
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _extract_domain(raw_data: Any) -> Optional[str]:
    if not raw_data:
        return None
    data = raw_data
    if isinstance(raw_data, str):
        try:
            data = json.loads(raw_data)
        except Exception:
            return None
    if not isinstance(data, dict):
        return None

    url = None
    for k in ("domain", "website", "url", "company_url", "link"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            url = v.strip()
            break
    if not url:
        return None

    # If already looks like a domain, return it.
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", url.lower()):
        return url.lower()

    # Extract hostname from URL
    m = re.search(r"://([^/]+)", url)
    if m:
        host = m.group(1).lower()
        host = host.split("@")[-1]  # remove userinfo
        host = host.split(":")[0]   # remove port
        # basic sanity
        if "." in host and len(host) < 255:
            return host
    return None


@dataclass(frozen=True)
class KeyStrengtheningSuggestion:
    weak_canonical_key: str
    suggested_domain: str
    suggested_canonical_key: str
    supporting_signal_ids: List[int]
    notes: str


def suggest_key_strengthening(
    conn: sqlite3.Connection,
    *,
    min_signals: int = 5,
    limit: int = 100,
    fp_only: bool = False,
) -> List[KeyStrengtheningSuggestion]:
    """
    Find name_loc canonical keys and suggest a domain-based canonical key when possible.
    """
    where = "WHERE s.canonical_key LIKE 'name_loc:%'"
    if fp_only:
        where += " AND sqm.human_label = 'FP'"

    rows = conn.execute(
        f"""
        SELECT
            s.canonical_key,
            COUNT(*) AS n_signals
        FROM signals s
        {"JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id" if fp_only else ""}
        {where}
        GROUP BY s.canonical_key
        HAVING COUNT(*) >= ?
        ORDER BY n_signals DESC
        LIMIT ?
        """,
        (min_signals, limit),
    ).fetchall()

    suggestions: List[KeyStrengtheningSuggestion] = []

    for r in rows:
        key = str(r["canonical_key"])
        # Pull sample signals for this key, attempt to extract domain
        sig_rows = conn.execute(
            "SELECT id, raw_data FROM signals WHERE canonical_key = ? ORDER BY detected_at DESC LIMIT 50",
            (key,),
        ).fetchall()

        domain_to_ids: Dict[str, List[int]] = {}
        for sr in sig_rows:
            dom = _extract_domain(sr["raw_data"])
            if not dom:
                continue
            domain_to_ids.setdefault(dom, []).append(int(sr["id"]))

        if not domain_to_ids:
            continue

        # Choose the most common extracted domain
        best_domain = max(domain_to_ids.items(), key=lambda kv: len(kv[1]))[0]
        ids = domain_to_ids[best_domain][:20]

        suggestions.append(
            KeyStrengtheningSuggestion(
                weak_canonical_key=key,
                suggested_domain=best_domain,
                suggested_canonical_key=f"domain:{best_domain}",
                supporting_signal_ids=ids,
                notes="Domain extracted from raw_data; consider migrating this canonical_key to reduce duplicates.",
            )
        )

    return suggestions


def suggestions_to_markdown(suggestions: List[KeyStrengtheningSuggestion]) -> str:
    lines: List[str] = []
    lines.append("# Canonical Key Strengthening Suggestions")
    lines.append("")
    if not suggestions:
        lines.append("_No suggestions generated._")
        return "\n".join(lines) + "\n"

    for s in suggestions:
        lines.append(f"## {s.weak_canonical_key}")
        lines.append(f"- suggested_domain: `{s.suggested_domain}`")
        lines.append(f"- suggested_canonical_key: `{s.suggested_canonical_key}`")
        lines.append(f"- supporting_signal_ids: {', '.join(map(str, s.supporting_signal_ids))}")
        lines.append(f"- notes: {s.notes}")
        lines.append("")

    return "\n".join(lines) + "\n"
