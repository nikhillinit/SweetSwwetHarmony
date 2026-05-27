from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, RoutingConfig, load_config
from .gates import GateBatch, run_gates
from .ledger import HermesLedger, HermesRun
from .router import RoutingPlan, score_task_for_lane

EXIT_OK = 0
EXIT_GATE_FAILURE = 4
EXIT_INVALID = 2


@dataclass(frozen=True)
class HermesRunResult:
    mode: str
    exit_code: int
    plan: RoutingPlan
    run_id: str | None = None
    run_dir: str | None = None
    preflight: GateBatch | None = None
    postflight: GateBatch | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "exitCode": self.exit_code,
            "runId": self.run_id,
            "runDir": self.run_dir,
            "plan": self.plan.to_dict(),
            "preflight": self.preflight.to_dict() if self.preflight else None,
            "postflight": self.postflight.to_dict() if self.postflight else None,
        }


async def run_hermes(
    task: str,
    phase: str,
    mode: str,
    config_path: Path | str | None = None,
    manual_model: str | None = None,
) -> HermesRunResult:
    config = load_config(config_path)
    if mode not in config.modes:
        raise ValueError(f"unsupported Hermes mode {mode!r}")

    plan = score_task_for_lane(
        task_text=task,
        phase=phase,
        config=config,
        manual_model=manual_model,
    )

    if mode == "plan-only":
        return HermesRunResult(mode=mode, exit_code=EXIT_OK, plan=plan)

    ledger = HermesLedger(config.ledger, root=_resolve_repo_path(config.ledger.root))
    run = ledger.create_run(
        plan=plan.to_dict(),
        prompt=build_prompt(plan),
        metadata={"mode": mode, "phase": phase},
    )
    _write_initial_states(ledger, run, mode, phase, task, plan)

    preflight = await run_gates(config.gates.preflight, phase="preflight", run_dir=run.run_dir)
    ledger.write_state(run, "S2_preflight", preflight.to_dict())
    if not preflight.success:
        write_summary(ledger, run, plan, mode, preflight, None, EXIT_GATE_FAILURE)
        return HermesRunResult(
            mode=mode,
            exit_code=EXIT_GATE_FAILURE,
            plan=plan,
            run_id=run.run_id,
            run_dir=str(run.run_dir),
            preflight=preflight,
        )

    if mode == "preflight-only":
        write_summary(ledger, run, plan, mode, preflight, None, EXIT_OK)
        return HermesRunResult(
            mode=mode,
            exit_code=EXIT_OK,
            plan=plan,
            run_id=run.run_id,
            run_dir=str(run.run_dir),
            preflight=preflight,
        )

    postflight = await run_gates(config.gates.postflight, phase="postflight", run_dir=run.run_dir)
    ledger.write_state(run, "S3_postflight", postflight.to_dict())
    exit_code = EXIT_OK if postflight.success else EXIT_GATE_FAILURE
    write_summary(ledger, run, plan, mode, preflight, postflight, exit_code)

    return HermesRunResult(
        mode=mode,
        exit_code=exit_code,
        plan=plan,
        run_id=run.run_id,
        run_dir=str(run.run_dir),
        preflight=preflight,
        postflight=postflight,
    )


def build_prompt(plan: RoutingPlan) -> str:
    return "\n".join(
        [
            "Hermes routing plan",
            f"Task: {plan.task_text}",
            f"Phase: {plan.phase}",
            f"Recommended executor: {plan.recommended_executor}",
            f"Risk: {plan.risk}",
            f"Specialist: {plan.specialist or 'none'}",
        ]
    )


def write_summary(
    ledger: HermesLedger,
    run: HermesRun,
    plan: RoutingPlan,
    mode: str,
    preflight: GateBatch | None,
    postflight: GateBatch | None,
    exit_code: int,
) -> Path:
    title = "Dry Run" if mode == "dry-run" else mode.replace("-", " ").title()
    lines = [
        f"# Hermes {title} Summary",
        "",
        f"- Run ID: {run.run_id}",
        f"- Task: {plan.task_text}",
        f"- Phase: {plan.phase}",
        f"- Recommended executor: {plan.recommended_executor}",
        f"- Risk: {plan.risk}",
        f"- Exit code: {exit_code}",
    ]
    if preflight is not None:
        lines.append(f"- Preflight: {'passed' if preflight.success else 'failed'}")
    if postflight is not None:
        lines.append(f"- Postflight: {'passed' if postflight.success else 'failed'}")
    return ledger.write_text_artifact(run, "summary.md", "\n".join(lines) + "\n")


def _write_initial_states(
    ledger: HermesLedger,
    run: HermesRun,
    mode: str,
    phase: str,
    task: str,
    plan: RoutingPlan,
) -> None:
    ledger.write_state(
        run,
        "S0_initial",
        {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "phase": phase,
            "task": task,
        },
    )
    ledger.write_state(run, "S1_routing", plan.to_dict())


def _resolve_repo_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path
