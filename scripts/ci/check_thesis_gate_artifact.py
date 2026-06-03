# scripts/ci/check_thesis_gate_artifact.py
"""Enforce the thesis golden-set gate from resolver decision + gate output."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Label that authorizes merging a thesis-sensitive PR without a live eval.
STRUCTURAL_OVERRIDE_LABEL = "thesis-label-drift-approved"


class GateError(Exception):
    """Raised when the gate must block the PR."""


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_gate(
    *,
    decision_path: Path,
    manifest_path: Path,
    gate_output_path: Path | None,
    sensitive: bool,
    labels: list[str],
    min_accuracy: float,
) -> None:
    decision = _load(decision_path)
    mode = decision.get("mode")

    # A non-thesis PR has no thesis code to verify; pass cheaply regardless of
    # resolver mode (the workflow produces no gate output for non-sensitive PRs).
    if not sensitive:
        return

    if mode == "structural":
        if STRUCTURAL_OVERRIDE_LABEL not in labels:
            raise GateError(
                "Thesis-sensitive change with structural-only eval (no API key, "
                f"no execute-capable Hermes executor). Apply '{STRUCTURAL_OVERRIDE_LABEL}' "
                "after a maintainer-dispatched live eval, or provide an executor/key.")
        return  # approved: structural checks suffice

    # Live eval (gold or hermes): require gate output, fingerprint, accuracy floor.
    if gate_output_path is None:
        raise GateError(f"mode={mode} requires a gate output artifact.")
    gate = _load(gate_output_path)
    manifest = _load(manifest_path)
    if gate.get("dataset_fingerprint") != manifest.get("dataset_fingerprint"):
        raise GateError("dataset_fingerprint mismatch between gate output and manifest.")
    accuracy = gate.get("accuracy")
    if accuracy is None or accuracy < min_accuracy:
        raise GateError(f"accuracy {accuracy} below floor {min_accuracy}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gate-output", type=Path, default=None)
    parser.add_argument("--sensitive", choices=["true", "false"], required=True)
    parser.add_argument("--labels", default="", help="Comma-separated live GitHub labels.")
    parser.add_argument("--min-accuracy", type=float, default=0.9)
    args = parser.parse_args(argv)
    labels = [s.strip() for s in args.labels.split(",") if s.strip()]
    try:
        check_gate(
            decision_path=args.decision,
            manifest_path=args.manifest,
            gate_output_path=args.gate_output,
            sensitive=args.sensitive == "true",
            labels=labels,
            min_accuracy=args.min_accuracy,
        )
    except GateError as exc:
        print(f"THESIS GATE BLOCK: {exc}", file=sys.stderr)
        return 1
    print("THESIS GATE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
