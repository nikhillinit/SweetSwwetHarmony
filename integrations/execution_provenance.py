"""Versioned, sealed execution provenance shared by provider wrappers.

The envelope records structural process evidence only.  It never classifies
provider text and never decides Hermes fallback policy; that separation keeps a
post-mutation error message from being mistaken for pre-execution proof.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .process_runtime import ProcessRunResult

CURRENT_EXECUTION_PROVENANCE_VERSION = 1
_DIAGNOSTIC_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class ExecutionOrigin(str, Enum):
    """Sealed origin of a wrapper result."""

    PROVIDER_NOT_ESTABLISHED = "provider_not_established"
    PROVIDER_REJECTED_PRE_SESSION = "provider_rejected_pre_session"
    RUNTIME = "runtime"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class LaunchForm(str, Enum):
    """Resolved launch mechanism used at the owned process boundary."""

    DIRECT_EXEC = "direct_exec"
    SHELL = "shell"
    UNKNOWN = "unknown"


_NON_MUTATING_ORIGINS = frozenset(
    {
        ExecutionOrigin.PROVIDER_NOT_ESTABLISHED,
        ExecutionOrigin.PROVIDER_REJECTED_PRE_SESSION,
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionProvenance:
    """Immutable execution evidence with mutation safety derived from origin."""

    origin: ExecutionOrigin
    launch_form: LaunchForm
    exit_code: int | None = None
    diagnostic_code: str | None = None
    version: int = CURRENT_EXECUTION_PROVENANCE_VERSION

    def __post_init__(self) -> None:
        if self.version != CURRENT_EXECUTION_PROVENANCE_VERSION:
            raise ValueError(f"unsupported execution provenance version: {self.version}")
        if not isinstance(self.origin, ExecutionOrigin):
            raise TypeError("origin must be an ExecutionOrigin")
        if not isinstance(self.launch_form, LaunchForm):
            raise TypeError("launch_form must be a LaunchForm")
        if isinstance(self.exit_code, bool) or (
            self.exit_code is not None and not isinstance(self.exit_code, int)
        ):
            raise TypeError("exit_code must be an integer or None")
        if self.exit_code is not None and not -(2**31) <= self.exit_code < 2**31:
            raise ValueError("exit_code is outside the signed 32-bit range")
        if self.diagnostic_code is not None and (
            not isinstance(self.diagnostic_code, str)
            or _DIAGNOSTIC_CODE.fullmatch(self.diagnostic_code) is None
        ):
            raise ValueError("diagnostic_code must be a bounded non-secret code")

        if (
            self.origin is ExecutionOrigin.PROVIDER_NOT_ESTABLISHED
            and self.launch_form is LaunchForm.SHELL
        ):
            raise ValueError(
                "shell launches cannot attest that the provider was not established"
            )
        if self.origin in {
            ExecutionOrigin.PROVIDER_NOT_ESTABLISHED,
            ExecutionOrigin.PROVIDER_REJECTED_PRE_SESSION,
            ExecutionOrigin.TIMEOUT,
        } and self.exit_code is not None:
            raise ValueError(f"exit_code contradicts origin {self.origin.value}")
        if self.origin is ExecutionOrigin.RUNTIME and self.exit_code is None:
            raise ValueError("runtime provenance requires an exit_code")
        # UNKNOWN remains valid for legacy/synthetic ProcessRunResult values.
        # The real owned boundary always records DIRECT_EXEC or SHELL; UNKNOWN
        # simply fails the structural attestation predicate below.

    @property
    def mutation_possible(self) -> bool:
        """Derived safety property; callers cannot supply or override it."""

        return self.origin not in _NON_MUTATING_ORIGINS

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "origin": self.origin.value,
            "mutationPossible": self.mutation_possible,
            "launchForm": self.launch_form.value,
            "exitCode": self.exit_code,
            "diagnosticCode": self.diagnostic_code,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExecutionProvenance":
        """Validate a serialized envelope and re-derive mutation safety."""

        required = {
            "version",
            "origin",
            "mutationPossible",
            "launchForm",
            "exitCode",
            "diagnosticCode",
        }
        if set(payload) != required:
            raise ValueError("execution provenance fields do not match version 1")
        try:
            provenance = cls(
                version=payload["version"],
                origin=ExecutionOrigin(payload["origin"]),
                launch_form=LaunchForm(payload["launchForm"]),
                exit_code=payload["exitCode"],
                diagnostic_code=payload["diagnosticCode"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid execution provenance envelope") from exc
        if payload["mutationPossible"] is not provenance.mutation_possible:
            raise ValueError("mutationPossible contradicts the sealed origin")
        return provenance


def provenance_from_process_result(result: "ProcessRunResult") -> ExecutionProvenance:
    """Classify structural process evidence without inspecting output text."""

    outcome = getattr(getattr(result, "outcome", None), "value", None)
    launch_form = getattr(result, "launch_form", LaunchForm.UNKNOWN)
    if not isinstance(launch_form, LaunchForm):
        launch_form = LaunchForm(str(launch_form))

    if outcome == "provider_not_established":
        return ExecutionProvenance(
            origin=ExecutionOrigin.PROVIDER_NOT_ESTABLISHED,
            launch_form=launch_form,
            diagnostic_code=(
                getattr(result, "establishment_error_code", None) or "spawn_oserror"
            ),
        )
    if outcome == "timed_out":
        return ExecutionProvenance(
            origin=ExecutionOrigin.TIMEOUT,
            launch_form=launch_form,
            diagnostic_code="deadline_exceeded",
        )
    if outcome == "completed":
        return ExecutionProvenance(
            origin=ExecutionOrigin.RUNTIME,
            launch_form=launch_form,
            exit_code=getattr(result, "exit_code", None),
            diagnostic_code="process_exit",
        )
    return unknown_execution_provenance("unclassified_outcome")


def unresolved_provider_provenance(
    diagnostic_code: str = "binary_not_found",
) -> ExecutionProvenance:
    """Evidence for a provider rejected before a launch form was resolved."""

    return ExecutionProvenance(
        origin=ExecutionOrigin.PROVIDER_NOT_ESTABLISHED,
        launch_form=LaunchForm.UNKNOWN,
        diagnostic_code=diagnostic_code,
    )


def unknown_execution_provenance(
    diagnostic_code: str = "unclassified",
) -> ExecutionProvenance:
    """Fail-closed provenance for legacy/synthetic responses and exceptions."""

    return ExecutionProvenance(
        origin=ExecutionOrigin.UNKNOWN,
        launch_form=LaunchForm.UNKNOWN,
        diagnostic_code=diagnostic_code,
    )


def is_provider_not_established_attested(
    provenance: ExecutionProvenance,
) -> bool:
    """Pure structural attestation only; Hermes policy eligibility is separate."""

    return (
        provenance.origin is ExecutionOrigin.PROVIDER_NOT_ESTABLISHED
        and provenance.launch_form is LaunchForm.DIRECT_EXEC
        and not provenance.mutation_possible
    )
