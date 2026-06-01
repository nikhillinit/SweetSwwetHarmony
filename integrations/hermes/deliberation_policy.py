from __future__ import annotations

from typing import Any

REVIEWER_POLICY_VERSION = 1
TRUSTED_REVIEWERS = ("codex", "gemini", "kimi")
APPROVAL_SCHEMA = "structured_json_v1"
DEFAULT_HIGH_RISK_QUORUM = 2
PERSISTED_QUORUM_KEYS = (
    "status",
    "required",
    "countedApprovals",
    "trustedReviewers",
    "untrustedApprovals",
    "nonCompliantApprovals",
    "malformedReviewers",
)


def default_reviewer_policy(
    *,
    task: str = "deliberate",
    risk_level: str = "high",
) -> dict[str, Any]:
    return {
        "policyVersion": REVIEWER_POLICY_VERSION,
        "task": task,
        "riskLevel": risk_level,
        "trustedReviewers": list(TRUSTED_REVIEWERS),
        "requiredQuorum": _required_quorum(risk_level),
        "approvalCriteria": {
            "verdict": "approve",
            "success": True,
            "parsed": True,
            "schema": APPROVAL_SCHEMA,
        },
    }


def reviewer_policy_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return default_reviewer_policy(
        task=str(plan.get("task") or "deliberate"),
        risk_level=str(plan.get("risk_level") or plan.get("riskLevel") or "high"),
    )


def evaluate_reviewer_policy(
    panel: list[dict[str, Any]],
    policy: dict[str, Any] | None,
    *,
    require_recorded_quorum: bool = False,
    recorded_quorum: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not policy:
        return _missing_policy_evidence(
            quorum_evidence_present=recorded_quorum is not None
            if require_recorded_quorum
            else True
        )

    trusted_reviewers = _string_list(policy.get("trustedReviewers"))
    required_quorum = _positive_int(policy.get("requiredQuorum"))
    criteria = policy.get("approvalCriteria")
    criteria_valid = (
        isinstance(criteria, dict)
        and criteria.get("verdict") == "approve"
        and criteria.get("success") is True
        and criteria.get("parsed") is True
        and criteria.get("schema") == APPROVAL_SCHEMA
    )
    policy_valid = (
        bool(trusted_reviewers)
        and required_quorum is not None
        and criteria_valid
    )
    required = required_quorum or 0

    counted_approvals: list[str] = []
    untrusted_approvals: list[str] = []
    non_compliant_approvals: list[dict[str, Any]] = []
    malformed_reviewers: list[str] = []

    for item in panel:
        executor = str(item.get("executor") or "")
        verdict = str(item.get("verdict") or "")
        parsed = item.get("parsed") is True
        success = item.get("success") is True

        if verdict != "skip" and not parsed:
            malformed_reviewers.append(executor)

        if verdict != "approve":
            continue

        reasons: list[str] = []
        if executor not in trusted_reviewers:
            reasons.append("untrusted_reviewer")
            untrusted_approvals.append(executor)
        if not success:
            reasons.append("success_false")
        if not parsed:
            reasons.append("parsed_false")

        if reasons:
            non_compliant_approvals.append(
                {"executor": executor, "reasons": reasons}
            )
        else:
            counted_approvals.append(executor)

    quorum_evidence_present = (
        recorded_quorum is not None if require_recorded_quorum else True
    )
    if not policy_valid:
        computed_status = "invalid_policy"
    elif not quorum_evidence_present:
        computed_status = "missing_quorum_evidence"
    elif malformed_reviewers:
        computed_status = "malformed_reviewer_output"
    elif len(counted_approvals) >= required:
        computed_status = "satisfied"
    else:
        computed_status = "insufficient_quorum"

    recorded_quorum_matches = (
        _recorded_quorum_matches(
            recorded_quorum,
            {
                "status": computed_status,
                "required": required,
                "countedApprovals": counted_approvals,
                "trustedReviewers": trusted_reviewers,
                "untrustedApprovals": untrusted_approvals,
                "nonCompliantApprovals": non_compliant_approvals,
                "malformedReviewers": malformed_reviewers,
            },
        )
        if require_recorded_quorum and recorded_quorum is not None
        else True
    )
    if require_recorded_quorum and quorum_evidence_present and not recorded_quorum_matches:
        status = "quorum_evidence_mismatch"
    else:
        status = computed_status

    return {
        "status": status,
        "required": required,
        "countedApprovals": counted_approvals,
        "trustedReviewers": trusted_reviewers,
        "untrustedApprovals": untrusted_approvals,
        "nonCompliantApprovals": non_compliant_approvals,
        "malformedReviewers": malformed_reviewers,
        "policyPresent": True,
        "quorumEvidencePresent": quorum_evidence_present,
        "quorumEvidenceMatches": recorded_quorum_matches,
    }


def quorum_record_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {key: evidence[key] for key in PERSISTED_QUORUM_KEYS if key in evidence}


def evaluate_record_reviewer_policy(record: dict[str, Any]) -> dict[str, Any]:
    consensus = (
        record.get("consensus") if isinstance(record.get("consensus"), dict) else {}
    )
    return evaluate_reviewer_policy(
        _panel_items(record.get("panel")),
        record.get("reviewerPolicy")
        if isinstance(record.get("reviewerPolicy"), dict)
        else None,
        require_recorded_quorum=True,
        recorded_quorum=consensus.get("quorum") if isinstance(consensus, dict) else None,
    )


def _missing_policy_evidence(*, quorum_evidence_present: bool) -> dict[str, Any]:
    return {
        "status": "missing_policy",
        "required": 0,
        "countedApprovals": [],
        "trustedReviewers": [],
        "untrustedApprovals": [],
        "nonCompliantApprovals": [],
        "malformedReviewers": [],
        "policyPresent": False,
        "quorumEvidencePresent": quorum_evidence_present,
        "quorumEvidenceMatches": False,
    }


def _recorded_quorum_matches(
    recorded_quorum: dict[str, Any] | None,
    expected: dict[str, Any],
) -> bool:
    if not isinstance(recorded_quorum, dict):
        return False
    return all(recorded_quorum.get(key) == value for key, value in expected.items())


def _required_quorum(risk_level: str) -> int:
    return DEFAULT_HIGH_RISK_QUORUM if risk_level in {"high", "critical"} else 1


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _panel_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
