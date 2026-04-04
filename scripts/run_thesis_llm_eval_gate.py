"""Run the thesis LLM evaluation gate and write a go/no-go artifact."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from utils.thesis_eval_gate import (
    build_eval_gate_artifact,
    default_proposed_changes,
    write_eval_gate_artifact,
)
from utils.thesis_evaluator import ThesisEvaluator


DEFAULT_DATASET = Path("tests/fixtures/thesis_llm_golden_set.jsonl")
DEFAULT_OUTPUT = Path(".omx/specs/thesis-llm-eval-gate.json")


def _load_project_env() -> None:
    """Best-effort load of the repo's .env file, matching project entrypoints."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv()


def _resolve_llm_api_key() -> str | None:
    _load_project_env()
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


async def run_eval_gate(
    dataset: Path,
    output: Path,
    *,
    skip_llm: bool,
) -> Path:
    api_key = _resolve_llm_api_key()
    effective_skip_llm = skip_llm or not api_key
    evaluator = ThesisEvaluator(
        llm_api_key=api_key,
    )
    comparison = await evaluator.evaluate_both(dataset, skip_llm=effective_skip_llm)
    artifact = build_eval_gate_artifact(
        comparison,
        proposed_changes=list(default_proposed_changes()),
    )
    if not api_key:
        artifact.setdefault("blocked_reasons", []).insert(
            0,
            "GOOGLE_API_KEY/GEMINI_API_KEY not available after loading the project environment.",
        )
        artifact["decision"] = "no_go"
    return write_eval_gate_artifact(output, artifact)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Only run the keyword baseline and emit a blocked eval-gate artifact.",
    )
    args = parser.parse_args()

    output_path = asyncio.run(run_eval_gate(args.dataset, args.output, skip_llm=args.skip_llm))
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
