from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from integrations.hermes.adapters import ExecutorResult
from integrations.hermes.tasks.registry import run_registered_task
from tests.ops.hermes.conftest import minimal_config_dict
from utils.thesis_benchmark import (
    compute_dataset_fingerprint,
    scenario_counts_for_samples,
)


class FakeExecutor:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    async def execute(
        self,
        prompt: str,
        context_files: list[str] | None = None,
    ) -> ExecutorResult:
        self.prompts.append(prompt)
        return ExecutorResult(
            executor="codex",
            success=True,
            exit_code=0,
            content=self.content,
            duration_ms=17,
            token_usage={"input_tokens": 11, "output_tokens": 7},
        )


def _write_config(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_dataset(tmp_path: Path) -> tuple[Path, Path, list[dict[str, Any]]]:
    samples = [
        {
            "id": "sample_qualified",
            "input": "Company: GlowCup\nDescription: Consumer hydration tracker.\nSector: consumer_health_tech",
            "target": "QUALIFIED",
            "metadata": {
                "scenario": "clear_consumer",
                "sector": "consumer_health_tech",
                "target": "QUALIFIED",
                "nested": {"target": "REJECTED", "public": "kept"},
            },
        },
        {
            "id": "sample_held",
            "input": "Company: WorkWell\nDescription: Employer-paid wellness benefit.\nSector: consumer_health_tech",
            "target": "HELD",
            "metadata": {"scenario": "employer_sponsored", "sector": "consumer_health_tech"},
        },
        {
            "id": "sample_rejected",
            "input": "Company: DevInfra\nDescription: Enterprise developer observability platform.\nSector: developer_tools",
            "target": "REJECTED",
            "metadata": {"scenario": "clear_b2b", "sector": "developer_tools"},
        },
    ]
    dataset = tmp_path / "golden.jsonl"
    dataset.write_text(
        "".join(json.dumps(sample, sort_keys=True) + "\n" for sample in samples),
        encoding="utf-8",
    )
    manifest = tmp_path / "golden.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "benchmark_id": "unit-thesis",
                "benchmark_version": "2026-06-03.test",
                "dataset_path": str(dataset),
                "dataset_fingerprint": compute_dataset_fingerprint(samples),
                "sample_count": len(samples),
                "scenario_counts": scenario_counts_for_samples(samples),
                "ambiguous_scenarios": ["employer_sponsored"],
                "changelog": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dataset, manifest, samples


def _prediction_payload(samples: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "predictions": [
                {
                    "sample_id": sample["id"],
                    "prediction": sample["target"],
                    "rationale": "matches local fixture target for deterministic test",
                }
                for sample in samples
            ]
        }
    )


def _args(
    tmp_path: Path,
    *,
    dataset: Path | None = None,
    manifest: Path | None = None,
    output: Path | None = None,
    rebaseline_output: Path | None = None,
    mode: str = "plan-only",
    executor: str = "codex",
) -> argparse.Namespace:
    if dataset is None or manifest is None:
        dataset, manifest, _samples = _write_dataset(tmp_path)
    return argparse.Namespace(
        task_name="thesis-eval",
        config=str(_write_config(tmp_path)),
        plan_only=mode == "plan-only",
        preflight_only=mode == "preflight-only",
        dry_run=mode == "dry-run",
        execute=mode == "execute",
        ack_risk=None,
        lock_ttl_seconds=900,
        actor_type="operator",
        actor_id="tester",
        json_output=False,
        dataset=str(dataset),
        manifest=str(manifest),
        output=str(output or tmp_path / "artifacts" / "pr-gate.json"),
        rebaseline_output=str(
            rebaseline_output or tmp_path / "artifacts" / "pr-rebaseline.json"
        ),
        baseline_summary=None,
        min_accuracy=0.9,
        executor=executor,
    )


def test_plan_only_declares_inputs_artifacts_routing_and_non_mutation(tmp_path: Path) -> None:
    dataset, manifest, _samples = _write_dataset(tmp_path)

    result = run_registered_task(
        _args(tmp_path, dataset=dataset, manifest=manifest, mode="plan-only")
    )

    assert result.exit_code == 0
    assert result.status == "planned"
    assert result.plan["task"] == "thesis-eval"
    assert result.plan["dataset"]["path"] == str(dataset)
    assert result.plan["manifest"]["path"] == str(manifest)
    assert result.plan["benchmark"]["benchmark_fingerprint"]
    assert result.plan["routing"]["manual_executor"] == "codex"
    assert result.plan["artifacts"]["gate"] == str(tmp_path / "artifacts" / "pr-gate.json")
    assert result.plan["mutation"]["allowed"] is False


def test_preflight_fails_when_dataset_or_manifest_is_missing(tmp_path: Path) -> None:
    dataset, manifest, _samples = _write_dataset(tmp_path)
    dataset.unlink()

    result = run_registered_task(
        _args(tmp_path, dataset=dataset, manifest=manifest, mode="preflight-only")
    )

    assert result.exit_code == 4
    assert result.status == "preflight_failed"
    failed = {check.name for check in result.checks if not check.passed}
    assert "dataset_exists" in failed


def test_execute_uses_fake_executor_and_writes_gate_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from integrations.hermes.tasks import thesis_eval

    dataset, manifest, samples = _write_dataset(tmp_path)
    fake = FakeExecutor(_prediction_payload(samples))
    monkeypatch.setattr(thesis_eval, "build_executor", lambda *_args, **_kwargs: fake)

    output = tmp_path / "artifacts" / "pr-gate.json"
    result = run_registered_task(
        _args(
            tmp_path,
            dataset=dataset,
            manifest=manifest,
            output=output,
            mode="execute",
        )
    )

    assert result.exit_code == 0
    assert result.status == "executed"
    gate = json.loads(output.read_text(encoding="utf-8"))
    assert gate["decision"] == "go"
    assert gate["benchmark_fingerprint"] == compute_dataset_fingerprint(samples)
    assert gate["llm_accuracy"] == 1.0
    assert result.outputs["executor"] == "codex"
    assert result.outputs["sampleCount"] == len(samples)
    assert result.outputs["accuracy"] == 1.0
    assert (Path(result.run_dir or "") / "thesis_eval_gate.json").exists()
    assert (Path(result.run_dir or "") / "thesis_eval_predictions.json").exists()
    assert (Path(result.run_dir or "") / "thesis_eval_executor.json").exists()


def test_executor_prompt_does_not_include_target_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from integrations.hermes.tasks import thesis_eval

    dataset, manifest, samples = _write_dataset(tmp_path)
    fake = FakeExecutor(_prediction_payload(samples))
    monkeypatch.setattr(thesis_eval, "build_executor", lambda *_args, **_kwargs: fake)

    result = run_registered_task(
        _args(tmp_path, dataset=dataset, manifest=manifest, mode="execute")
    )

    assert result.exit_code == 0
    prompt = fake.prompts[-1]
    assert '"target"' not in prompt
    assert "sample_qualified" in prompt
    assert "GlowCup" in prompt


def test_fenced_json_executor_output_is_accepted(tmp_path: Path, monkeypatch) -> None:
    from integrations.hermes.tasks import thesis_eval

    dataset, manifest, samples = _write_dataset(tmp_path)
    fake = FakeExecutor(f"```json\n{_prediction_payload(samples)}\n```")
    monkeypatch.setattr(thesis_eval, "build_executor", lambda *_args, **_kwargs: fake)

    result = run_registered_task(
        _args(tmp_path, dataset=dataset, manifest=manifest, mode="execute")
    )

    assert result.exit_code == 0
    assert result.outputs["decision"] == "go"


def test_execute_fails_closed_on_bad_executor_predictions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from integrations.hermes.tasks import thesis_eval

    dataset, manifest, samples = _write_dataset(tmp_path)
    payload = {
        "predictions": [
            {"sample_id": samples[0]["id"], "prediction": "QUALIFIED"},
            {"sample_id": samples[0]["id"], "prediction": "QUALIFIED"},
            {"sample_id": samples[1]["id"], "prediction": "MAYBE"},
        ]
    }
    fake = FakeExecutor(json.dumps(payload))
    monkeypatch.setattr(thesis_eval, "build_executor", lambda *_args, **_kwargs: fake)

    result = run_registered_task(
        _args(tmp_path, dataset=dataset, manifest=manifest, mode="execute")
    )

    assert result.exit_code == 1
    assert result.status == "failed"
    assert "invalid thesis-eval predictions" in (result.error or "")
    failure = Path(result.run_dir or "") / "execute_failure.json"
    assert failure.exists()
    evidence = json.loads(failure.read_text(encoding="utf-8"))["evidence"]
    assert evidence["errors"]
