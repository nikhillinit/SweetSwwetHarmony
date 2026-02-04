#!/usr/bin/env python3
"""
Shadow/Ground-Truth Join Utility

Phase 0C: Data-Driven Tuning

Joins shadow logs (v1/v2 comparison data) with ground truth labels
from Notion CRM to enable precision/recall evaluation.

Join strategy:
1. Extract domain from ground truth website URLs
2. Match against shadow log canonical_keys (domain:xxx format)
3. Output joined records for evaluation

Usage:
    python scripts/shadow_ground_truth_join.py \\
        --shadow shadow.jsonl \\
        --ground-truth ground_truth.jsonl \\
        --out joined.jsonl

    # With stats only (no output file)
    python scripts/shadow_ground_truth_join.py \\
        --shadow shadow.jsonl \\
        --ground-truth ground_truth.jsonl \\
        --stats-only
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def extract_domain(url: str) -> Optional[str]:
    """
    Extract normalized domain from URL.

    - Removes www. prefix
    - Lowercases
    - Returns None for invalid URLs

    Examples:
        https://www.example.com/path -> example.com
        http://Example.COM -> example.com
    """
    if not url:
        return None

    try:
        # Add scheme if missing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Remove www. prefix
        if domain.startswith('www.'):
            domain = domain[4:]

        # Remove port if present
        if ':' in domain:
            domain = domain.split(':')[0]

        return domain if domain else None
    except Exception:
        return None


def extract_domain_from_canonical_key(ck: str) -> Optional[str]:
    """
    Extract domain from canonical_key if it's domain-based.

    Formats:
        domain:example.com -> example.com
        name:company-abc123 -> None (not domain-based)
    """
    if not ck:
        return None

    if ck.startswith('domain:'):
        return ck[7:]  # Remove "domain:" prefix

    return None


def load_shadow_logs(path: str) -> List[Dict]:
    """Load shadow logs from JSONL file."""
    records = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_ground_truth(path: str) -> List[Dict]:
    """Load ground truth from JSONL file."""
    records = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_ground_truth_index(ground_truth: List[Dict]) -> Dict[str, Dict]:
    """
    Build lookup index from ground truth records.

    Keys by normalized domain extracted from website URL.
    """
    index = {}
    duplicates = 0

    for gt in ground_truth:
        website = gt.get('website', '')
        domain = extract_domain(website)

        if domain:
            if domain in index:
                duplicates += 1
                # Keep the one with more info or higher status
                existing = index[domain]
                # Prefer records with descriptions or funded status
                if gt.get('description') and not existing.get('description'):
                    index[domain] = gt
                elif gt.get('status') == 'Funded' and existing.get('status') != 'Funded':
                    index[domain] = gt
            else:
                index[domain] = gt

    if duplicates:
        logger.info(f"Ground truth: {duplicates} duplicate domains (kept first/best)")

    return index


def join_records(
    shadow_logs: List[Dict],
    gt_index: Dict[str, Dict],
) -> Tuple[List[Dict], Dict]:
    """
    Join shadow logs with ground truth.

    Returns:
        Tuple of (joined_records, stats_dict)
    """
    joined = []
    stats = {
        'shadow_total': len(shadow_logs),
        'shadow_with_domain': 0,
        'matched': 0,
        'unmatched_shadow': 0,
        'gt_total': len(gt_index),
        'gt_matched': set(),
    }

    for shadow in shadow_logs:
        ck = shadow.get('canonical_key', '')
        domain = extract_domain_from_canonical_key(ck)

        if domain:
            stats['shadow_with_domain'] += 1

            gt = gt_index.get(domain)
            if gt:
                stats['matched'] += 1
                stats['gt_matched'].add(domain)

                # Build joined record
                joined.append({
                    # Shadow fields
                    'canonical_key': ck,
                    'domain': domain,
                    'signal_id': shadow.get('signal_id'),
                    'v1_score': shadow.get('v1_score'),
                    'v1_routing': shadow.get('v1_routing'),
                    'v2_score': shadow.get('v2_score'),
                    'v2_routing': shadow.get('v2_routing'),
                    'delta_score': shadow.get('delta_score'),
                    'keyword_category': shadow.get('keyword_category'),
                    # Ground truth fields
                    'gt_label': gt.get('label'),
                    'gt_status': gt.get('status'),
                    'gt_sector': gt.get('sector'),
                    'gt_company_name': gt.get('company_name'),
                })
            else:
                stats['unmatched_shadow'] += 1
        else:
            stats['unmatched_shadow'] += 1

    stats['gt_matched_count'] = len(stats['gt_matched'])
    stats['gt_unmatched'] = stats['gt_total'] - stats['gt_matched_count']
    del stats['gt_matched']  # Remove set for JSON serialization

    return joined, stats


def print_stats(stats: Dict, joined: List[Dict]):
    """Print join statistics."""
    print("\n=== Join Statistics ===")
    print(f"Shadow logs total:       {stats['shadow_total']}")
    print(f"Shadow with domain key:  {stats['shadow_with_domain']}")
    print(f"Ground truth total:      {stats['gt_total']}")
    print()
    print(f"Matched:                 {stats['matched']}")
    print(f"Shadow unmatched:        {stats['unmatched_shadow']}")
    print(f"Ground truth matched:    {stats['gt_matched_count']}")
    print(f"Ground truth unmatched:  {stats['gt_unmatched']}")

    if joined:
        # Label distribution
        labels = defaultdict(int)
        for rec in joined:
            labels[rec['gt_label']] += 1

        print("\n=== Joined Record Labels ===")
        for label, count in sorted(labels.items()):
            print(f"  {label}: {count}")

        # Routing distribution by label
        print("\n=== V1 Routing by Label ===")
        routing_by_label = defaultdict(lambda: defaultdict(int))
        for rec in joined:
            routing_by_label[rec['gt_label']][rec['v1_routing']] += 1

        for label in sorted(routing_by_label.keys()):
            print(f"  {label}:")
            for routing, count in sorted(routing_by_label[label].items()):
                print(f"    {routing}: {count}")


def main():
    parser = argparse.ArgumentParser(
        description="Join shadow logs with ground truth labels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--shadow",
        type=str,
        required=True,
        help="Path to shadow logs JSONL file",
    )
    parser.add_argument(
        "--ground-truth",
        type=str,
        required=True,
        help="Path to ground truth JSONL file",
    )
    parser.add_argument(
        "--out",
        type=str,
        help="Output JSONL file path (omit for stats only)",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Print stats only, don't write output file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load data
    logger.info(f"Loading shadow logs from {args.shadow}")
    shadow_logs = load_shadow_logs(args.shadow)
    logger.info(f"Loaded {len(shadow_logs)} shadow logs")

    logger.info(f"Loading ground truth from {args.ground_truth}")
    ground_truth = load_ground_truth(args.ground_truth)
    logger.info(f"Loaded {len(ground_truth)} ground truth records")

    # Build index and join
    logger.info("Building ground truth index...")
    gt_index = build_ground_truth_index(ground_truth)
    logger.info(f"Indexed {len(gt_index)} unique domains")

    logger.info("Joining records...")
    joined, stats = join_records(shadow_logs, gt_index)

    # Print stats
    print_stats(stats, joined)

    # Write output
    if args.out and not args.stats_only:
        with open(args.out, 'w', encoding='utf-8') as f:
            for rec in joined:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        print(f"\nWritten {len(joined)} joined records to {args.out}")
    elif not args.stats_only and not args.out:
        print("\nNo output file specified. Use --out to save joined records.")


if __name__ == "__main__":
    main()
