"""Pin the *production* routing config (.claude/hermes/model-routing.json) intent.

The router unit tests use a synthetic minimal config; these load the real
checked-in config so regressions in lane keywords / risk escalation are caught.
"""

from __future__ import annotations

from integrations.hermes.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_config
from integrations.hermes.router import score_task_for_lane


def _production_config():
    return load_config(PROJECT_ROOT / DEFAULT_CONFIG_PATH)


def test_restore_task_routes_to_high_risk_durability_lane():
    # A naturally-phrased restore task (no schema keywords) must not under-route.
    plan = score_task_for_lane(
        "wire litestream restore from backup",
        phase="production",
        config=_production_config(),
    )
    assert plan.specialist == "durability"
    assert plan.risk == "high"
    assert plan.recommended_executor == "codex"


def test_delete_dead_code_is_not_escalated_to_high():
    # Benign code cleanup must not trip the destructive-deletion escalation.
    plan = score_task_for_lane(
        "delete dead code",
        phase="development",
        config=_production_config(),
    )
    assert plan.specialist is None
    assert plan.risk == "medium"


def test_destructive_sql_delete_still_high_risk():
    # Genuine data destruction must still escalate.
    plan = score_task_for_lane(
        "drop table signals_archive",
        phase="production",
        config=_production_config(),
    )
    assert plan.risk == "high"
