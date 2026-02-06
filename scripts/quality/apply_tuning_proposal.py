#!/usr/bin/env python3
"""
Apply auto-applicable actions from a tuning proposal YAML.

By default this is a dry-run. Use --apply to write files.

Usage:
  python scripts/quality/apply_tuning_proposal.py --proposal /tmp/proposal.yaml
  python scripts/quality/apply_tuning_proposal.py --proposal /tmp/proposal.yaml --apply
"""
from __future__ import annotations

import argparse
import json

from ops.quality.tuning import apply_tuning_proposal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal", required=True)
    ap.add_argument("--apply", action="store_true", default=False)
    args = ap.parse_args()

    summary = apply_tuning_proposal(proposal_path=args.proposal, repo_root=".", dry_run=(not bool(args.apply)))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
