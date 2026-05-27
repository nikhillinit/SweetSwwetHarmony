from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .adapters import HermesExecutor, build_executor
from .config import PROJECT_ROOT, RoutingConfig, load_config
from .gates import GateBatch, run_gates
from .ledger import HermesLedger, HermesRun
from .locks import HermesLock
from .router import RoutingPlan, score_task_for_lane

EXIT_OK = 0
EXIT_GATE_FAILURE = 4
EXIT_LOCK_HELD = 6
EXIT_LEDGER_FAILURE = 7
EXIT_HIGH_RISK_ACK_REQUIRED = 75
EXIT_INVALID = 2
RISK_ACK = "I-ACK-RISK"


@dataclass(frozen=True)
class HermesRunResult:
    mode: str
    exit_code: int
    plan: RoutingPlan
    run_id: str | None = None
    run_dir: str | None = None
    preflight: GateBatch | None = None
    postflight: GateBatch | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "exitCode": self.exit_code,
            "runId": self.run_id,
            "runDir": self.run_dir,
            "plan": self.plan.to_dict(),
            "preflight": self.preflight.to_dict() if self.preflight else None,
            "postflight": self.postflight.to_dict() if self.postflight else None,
            "error": self.error,
        }


async def run_hermes(
    task: str,
    phase: str,
    mode: str,
    config_path: Path | str | None = None,
    manual_model: str | None = None,
    ack_risk: str | None = None,
    executor_factory: Callable[[str, RoutingConfig], HermesExecutor] | None = None,
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

    try:
        ledger = HermesLedger(config.ledger, root=_resolve_repo_path(config.ledger.root))
        run = ledger.create_run(
            plan=plan.to_dict(),
            prompt=build_prompt(plan),
            metadata={"mode": mode, "phase": phase},
        )
    except OSError as exc:
        return HermesRunResult(
            mode=mode,
            exit_code=EXIT_LEDGER_FAILURE,
            plan=plan,
            error=str(exc),
        )

    _write_initial_states(ledger, run, mode, phase, task, plan)

    if mode == "execute":
        return await _execute_with_gates(
            config=config,
            ledger=ledger,
            run=run,
            plan=plan,
            mode=mode,
            ack_risk=ack_risk,
            executor_factory=executor_factory,
        )

    preflight = await run_gates(config.gates.preflight, phase="preflight", run_dir=run.run_dir)
    ledger.write_state(run, "S2_preflight", preflight.to_dict())
    if not preflight.success:
        _write_gate_repair_prompt(
            ledger,
            run,
            plan,
            preflight,
            failure_type="preflight",
            state_paths=_state_paths(run),
            next_action="Fix the failing preflight gate and rerun Hermes.",
        )
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
    if not postflight.success:
        _write_gate_repair_prompt(
            ledger,
            run,
            plan,
            postflight,
            failure_type="postflight",
            state_paths=_state_paths(run),
            next_action="Fix the failing postflight gate and rerun Hermes.",
        )
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


async def _execute_with_gates(
    config: RoutingConfig,
    ledger: HermesLedger,
    run: HermesRun,
    plan: RoutingPlan,
    mode: str,
    ack_risk: str | None,
    executor_factory: Callable[[str, RoutingConfig], HermesExecutor] | None,
) -> HermesRunResult:
    lock = HermesLock(
        _resolve_repo_path(config.ledger.lock_path),
        mode=mode,
        run_id=run.run_id,
    )
    if not lock.acquire(timeout_seconds=0):
        ledger.write_state(run, "S2_lock_held", {"holder": lock.get_holder_info()})
        write_summary(ledger, run, plan, mode, None, None, EXIT_LOCK_HELD)
        return HermesRunResult(
            mode=mode,
            exit_code=EXIT_LOCK_HELD,
            plan=plan,
            run_id=run.run_id,
            run_dir=str(run.run_dir),
        )

    try:
        preflight = await run_gates(
            config.gates.preflight,
            phase="preflight",
            run_dir=run.run_dir,
        )
        ledger.write_state(run, "S2_preflight", preflight.to_dict())
        if not preflight.success:
            _write_gate_repair_prompt(
                ledger,
                run,
                plan,
                preflight,
                failure_type="preflight",
                state_paths=_state_paths(run),
                next_action="Fix the failing preflight gate before executing a provider.",
            )
            write_summary(ledger, run, plan, mode, preflight, None, EXIT_GATE_FAILURE)
            return HermesRunResult(
                mode=mode,
                exit_code=EXIT_GATE_FAILURE,
                plan=plan,
                run_id=run.run_id,
                run_dir=str(run.run_dir),
                preflight=preflight,
            )

        if plan.risk == "high" and ack_risk != RISK_ACK:
            ledger.write_state(
                run,
                "S3_approval_required",
                {
                    "risk": plan.risk,
                    "requiredAck": RISK_ACK,
                    "providedAck": ack_risk,
                },
            )
            write_summary(
                ledger,
                run,
                plan,
                mode,
                preflight,
                None,
                EXIT_HIGH_RISK_ACK_REQUIRED,
            )
            return HermesRunResult(
                mode=mode,
                exit_code=EXIT_HIGH_RISK_ACK_REQUIRED,
                plan=plan,
                run_id=run.run_id,
                run_dir=str(run.run_dir),
                preflight=preflight,
            )

        factory = executor_factory or build_executor
        executor = factory(plan.recommended_executor, config)
        execution = await executor.execute(build_prompt(plan), context_files=None)
        ledger.write_state(run, "S3_execution", execution.to_dict())
        if not execution.success:
            exit_code = execution.exit_code or 1
            ledger.write_repair_prompt(
                run,
                failure_type="executor",
                executor=execution.executor,
                arguments={"error": execution.error},
                exit_code=exit_code,
                routing_plan=plan.to_dict(),
                state_paths=_state_paths(run),
                next_action="Inspect the executor failure state, fix the cause, and rerun with the same routing plan.",
            )
            write_summary(ledger, run, plan, mode, preflight, None, exit_code)
            return HermesRunResult(
                mode=mode,
                exit_code=exit_code,
                plan=plan,
                run_id=run.run_id,
                run_dir=str(run.run_dir),
                preflight=preflight,
                error=execution.error,
            )

        postflight = await run_gates(
            config.gates.postflight,
            phase="postflight",
            run_dir=run.run_dir,
        )
        ledger.write_state(run, "S4_postflight", postflight.to_dict())
        exit_code = EXIT_OK if postflight.success else EXIT_GATE_FAILURE
        if not postflight.success:
            _write_gate_repair_prompt(
                ledger,
                run,
                plan,
                postflight,
                failure_type="postflight",
                state_paths=_state_paths(run),
                next_action="Fix the failing postflight gate and review executor output before retrying.",
            )
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
    finally:
        lock.release()


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


def _write_gate_repair_prompt(
    ledger: HermesLedger,
    run: HermesRun,
    plan: RoutingPlan,
    batch: GateBatch,
    *,
    failure_type: str,
    state_paths: list[Path],
    next_action: str,
) -> Path:
    failed = next((result for result in batch.results if not result.success), None)
    return ledger.write_repair_prompt(
        run,
        failure_type=failure_type,
        command=list(failed.command) if failed else None,
        exit_code=failed.return_code if failed else EXIT_GATE_FAILURE,
        routing_plan=plan.to_dict(),
        state_paths=state_paths,
        stdout_path=run.run_dir / "gates" / f"{batch.phase}.json",
        stderr_path=None,
        next_action=next_action,
    )


def _state_paths(run: HermesRun) -> list[Path]:
    state_dir = run.run_dir / "state"
    if not state_dir.exists():
        return []
    return sorted(state_dir.glob("*.json"))
