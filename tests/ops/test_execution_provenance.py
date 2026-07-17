"""Sealed execution-provenance contract for Q10 wrapper responses."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from integrations.execution_provenance import (
    ExecutionOrigin,
    ExecutionProvenance,
    LaunchForm,
    is_provider_not_established_attested,
    provenance_from_process_result,
    unresolved_provider_provenance,
)
from integrations.process_runtime import ProcessOutcome, ProcessRunResult


def test_direct_exec_establishment_failure_is_sealed_and_non_mutating() -> None:
    result = ProcessRunResult(
        outcome=ProcessOutcome.PROVIDER_NOT_ESTABLISHED,
        exit_code=None,
        stdout=b"",
        stderr=b"",
        establishment_error="secret-bearing raw OS error",
        establishment_error_code="errno_2",
        launch_form=LaunchForm.DIRECT_EXEC,
    )

    provenance = provenance_from_process_result(result)

    assert provenance.origin is ExecutionOrigin.PROVIDER_NOT_ESTABLISHED
    assert provenance.launch_form is LaunchForm.DIRECT_EXEC
    assert provenance.mutation_possible is False
    assert is_provider_not_established_attested(provenance) is True
    assert provenance.to_dict() == {
        "version": 1,
        "origin": "provider_not_established",
        "mutationPossible": False,
        "launchForm": "direct_exec",
        "exitCode": None,
        "diagnosticCode": "errno_2",
    }
    assert "secret-bearing" not in str(provenance.to_dict())


def test_shell_launch_cannot_claim_provider_not_established() -> None:
    with pytest.raises(ValueError, match="shell"):
        ExecutionProvenance(
            origin=ExecutionOrigin.PROVIDER_NOT_ESTABLISHED,
            launch_form=LaunchForm.SHELL,
        )


def test_bare_exit_127_is_runtime_and_mutation_possible() -> None:
    provenance = provenance_from_process_result(
        ProcessRunResult(
            outcome=ProcessOutcome.COMPLETED,
            exit_code=127,
            stdout=b"",
            stderr=b"inner tool exited 127",
            launch_form=LaunchForm.DIRECT_EXEC,
        )
    )

    assert provenance.origin is ExecutionOrigin.RUNTIME
    assert provenance.exit_code == 127
    assert provenance.mutation_possible is True
    assert is_provider_not_established_attested(provenance) is False


def test_timeout_precedes_text_and_never_becomes_spawn_attestation() -> None:
    provenance = provenance_from_process_result(
        ProcessRunResult(
            outcome=ProcessOutcome.TIMED_OUT,
            exit_code=None,
            stdout=b"executable not found",
            stderr=b"rate limited",
            launch_form=LaunchForm.DIRECT_EXEC,
        )
    )

    assert provenance.origin is ExecutionOrigin.TIMEOUT
    assert provenance.mutation_possible is True
    assert is_provider_not_established_attested(provenance) is False


def test_pre_resolver_missing_binary_is_safe_but_not_launch_attested() -> None:
    provenance = unresolved_provider_provenance("binary_not_found")

    assert provenance.origin is ExecutionOrigin.PROVIDER_NOT_ESTABLISHED
    assert provenance.launch_form is LaunchForm.UNKNOWN
    assert provenance.mutation_possible is False
    assert is_provider_not_established_attested(provenance) is False


def test_provenance_rejects_contradictory_and_unbounded_states() -> None:
    with pytest.raises(ValueError, match="exit_code"):
        ExecutionProvenance(
            origin=ExecutionOrigin.TIMEOUT,
            launch_form=LaunchForm.DIRECT_EXEC,
            exit_code=1,
        )
    with pytest.raises(ValueError, match="diagnostic"):
        ExecutionProvenance(
            origin=ExecutionOrigin.UNKNOWN,
            launch_form=LaunchForm.UNKNOWN,
            diagnostic_code="raw secret with spaces",
        )
    with pytest.raises(ValueError, match="version"):
        ExecutionProvenance(
            version=99,
            origin=ExecutionOrigin.UNKNOWN,
            launch_form=LaunchForm.UNKNOWN,
        )


def test_provenance_is_immutable_and_mutation_flag_is_not_caller_supplied() -> None:
    provenance = ExecutionProvenance(
        origin=ExecutionOrigin.RUNTIME,
        launch_form=LaunchForm.DIRECT_EXEC,
        exit_code=0,
    )

    with pytest.raises(FrozenInstanceError):
        provenance.exit_code = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        ExecutionProvenance(  # type: ignore[call-arg]
            origin=ExecutionOrigin.PROVIDER_NOT_ESTABLISHED,
            launch_form=LaunchForm.DIRECT_EXEC,
            mutation_possible=True,
        )
