#!/usr/bin/env python3
"""
Generate a tuning proposal YAML from a pattern JSON file.

Usage:
  python scripts/quality/generate_tuning_proposal.py --patterns /tmp/patterns.json --out /tmp/proposal.yaml
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ops.quality.tuning import generate_tuning_proposal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    args = ap.parse_args()

    doc = json.loads(Path(args.patterns).read_text(encoding="utf-8"))
    patterns = doc.get("patterns", []) if isinstance(doc, dict) else []

    proposal = generate_tuning_proposal(patterns=patterns, window_days=args.window_days, out_path=args.out)
    print(json.dumps({"actions": len(proposal.get("actions", [])), "notes": len(proposal.get("notes", [])), "out": args.out}, indent=2))


if __name__ == "__main__":
    main()
