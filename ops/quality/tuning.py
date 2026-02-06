"""
Tuning proposal generation + application.

This module intentionally distinguishes:
- "Suggestions" (human action items)
- "Auto-applicable" patches (safe, deterministic file edits)

We keep auto-apply scope narrow:
- Currently supported: add/update keywords in config/v2/negative_keyword_policy.yaml

Everything else stays as a suggestion in the proposal file.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ops.quality.db import utc_now_iso
from utils.negative_keyword_policy import validate_negative_keyword_policy


def _stable_id(*parts: str) -> str:
    h = hashlib.sha256("::".join([p or "" for p in parts]).encode("utf-8")).hexdigest()
    return h[:12]


def generate_tuning_proposal(
    *,
    patterns: List[Dict[str, Any]],
    window_days: int,
    out_path: str | Path,
    negative_policy_path: str | Path = "config/v2/negative_keyword_policy.yaml",
) -> Dict[str, Any]:
    """
    Generate a YAML tuning proposal document from detected patterns.

    Returns proposal dict (also written to out_path).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing policy so we don't propose duplicates
    policy_path = Path(negative_policy_path)
    existing_policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    existing_keywords = set((existing_policy or {}).get("negative_keywords", {}).keys())

    actions: List[Dict[str, Any]] = []
    notes: List[str] = []

    # Heuristic keyword candidates for duplicate descriptions
    known_negative_candidates = [
        ("enterprise", 0.5, "B2B_ENTERPRISE"),
        ("b2b", 0.5, "B2B_ENTERPRISE"),
        ("consulting", 0.4, "SERVICES"),
        ("agency", 0.4, "SERVICES"),
        ("services firm", 0.4, "SERVICES"),
        ("blockchain", 0.5, "CRYPTO_WEB3"),
        ("crypto", 0.5, "CRYPTO_WEB3"),
        ("web3", 0.5, "CRYPTO_WEB3"),
        ("template", 0.5, "EDUCATIONAL"),
        ("tutorial", 0.5, "EDUCATIONAL"),
        ("course", 0.4, "EDUCATIONAL"),
        ("framework", 0.4, "DEVTOOLS"),
        ("plugin", 0.4, "DEVTOOLS"),
        ("sdk", 0.4, "DEVTOOLS"),
    ]

    for p in patterns:
        ptype = p.get("type")

        if ptype == "duplicate_fp_description":
            nd = str(p.get("normalized_description", ""))
            # Look for any known negative candidate token in the description.
            for kw, weight, cat in known_negative_candidates:
                if kw in nd and kw not in existing_keywords:
                    actions.append(
                        {
                            "id": _stable_id("negkw", kw, cat),
                            "type": "add_negative_keyword_v2",
                            "file": str(policy_path),
                            "keyword": kw,
                            "weight": float(weight),
                            "category": cat,
                            "rationale": f"Keyword '{kw}' appears in repeated FP descriptions (n={p.get('count')}).",
                            "evidence": {
                                "pattern_type": ptype,
                                "count": p.get("count"),
                                "example_signal_ids": p.get("example_signal_ids", []),
                            },
                            "requires_review": True,
                        }
                    )

            # Always add a note for the exact phrase — too risky to auto-apply.
            notes.append(
                f"Repeated FP description cluster (n={p.get('count')}): '{nd[:120]}…' "
                f"Consider adding a more precise exclusion phrase or improving spam parsing."
            )

        elif ptype in {"source_api_fp_rate", "source_api_category_fp_rate"}:
            notes.append(
                f"High FP rate pattern: {json.dumps(p, ensure_ascii=False)} "
                f"Consider collector tuning (thresholds, parsing fixes, or disable slice)."
            )

        elif ptype == "fp_temporal_hotspot":
            notes.append(
                f"Temporal hotspot detected: source_api={p.get('source_api')} hour_utc={p.get('hour_utc')} "
                f"fp_count={p.get('fp_count')}. Check rate limits / cron behavior."
            )

        elif ptype == "weak_canonical_keys_in_fp":
            notes.append(
                f"Weak canonical keys overrepresented in FP (share={p.get('share'):.2%}). "
                f"Consider strengthening keys (domain/org) and re-dedupe."
            )

    proposal = {
        "version": 1,
        "generated_at": utc_now_iso(),
        "window_days": int(window_days),
        "actions": actions,
        "notes": notes,
    }

    out_path.write_text(yaml.safe_dump(proposal, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return proposal


def apply_tuning_proposal(
    *,
    proposal_path: str | Path,
    repo_root: str | Path = ".",
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Apply the auto-applicable actions in a proposal.

    Currently supported:
    - add_negative_keyword_v2 -> config/v2/negative_keyword_policy.yaml

    Returns summary dict.
    """
    proposal_path = Path(proposal_path)
    repo_root = Path(repo_root)

    proposal = yaml.safe_load(proposal_path.read_text(encoding="utf-8")) or {}
    actions = proposal.get("actions", []) or []

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for a in actions:
        atype = a.get("type")
        if atype != "add_negative_keyword_v2":
            skipped.append({"action": a, "reason": "unsupported_action_type"})
            continue

        rel_file = Path(a.get("file", ""))
        target = (repo_root / rel_file).resolve()
        if not target.exists():
            skipped.append({"action": a, "reason": f"target_missing: {rel_file}"})
            continue

        kw = str(a.get("keyword", "")).strip().lower()
        weight = a.get("weight")
        cat = a.get("category")

        if not kw or weight is None or not cat:
            skipped.append({"action": a, "reason": "missing_fields"})
            continue

        # Load YAML and update
        policy = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        neg = policy.get("negative_keywords")
        if neg is None or not isinstance(neg, dict):
            policy["negative_keywords"] = {}
            neg = policy["negative_keywords"]

        before = copy.deepcopy(neg.get(kw))

        neg[kw] = {"weight": float(weight), "category": str(cat)}

        # Validate policy schema after change
        validation = validate_negative_keyword_policy(policy)
        if not validation.valid:
            skipped.append(
                {
                    "action": a,
                    "reason": "policy_validation_failed",
                    "errors": validation.errors,
                    "warnings": validation.warnings,
                }
            )
            # revert in-memory (do not write)
            if before is None:
                neg.pop(kw, None)
            else:
                neg[kw] = before
            continue

        if not dry_run:
            target.write_text(yaml.safe_dump(policy, sort_keys=False, allow_unicode=True), encoding="utf-8")

        applied.append(
            {
                "file": str(rel_file),
                "keyword": kw,
                "before": before,
                "after": neg.get(kw),
                "dry_run": dry_run,
            }
        )

    return {
        "proposal": str(proposal_path),
        "dry_run": dry_run,
        "applied": applied,
        "skipped": skipped,
    }
