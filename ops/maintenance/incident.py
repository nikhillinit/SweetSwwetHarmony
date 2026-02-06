"""Incident capsule management for self-healing infrastructure.

Creates structured incident capsules when collectors fail, capturing
traceback, context, and artifacts for automated repair.
"""

import json
import logging
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from ops.utils import InputSanitizer

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path("ops/artifacts/maintenance")


@dataclass
class MaintenanceIncident:
    incident_id: str
    component: str
    error_type: str
    error_message: str
    status: str = "open"  # open, investigating, resolved, wont_fix
    created_at: str = ""
    updated_at: str = ""
    artifact_dir: str = ""
    traceback_text: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    repair_attempts: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at


def create_incident(
    component: str,
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
) -> MaintenanceIncident:
    """Create an incident capsule from a collector/component failure."""
    now = datetime.now(timezone.utc)
    incident_id = f"{component}_{now.strftime('%Y%m%d_%H%M%S')}"

    # Sanitize error message
    error_msg = InputSanitizer.sanitize_for_display(str(error), max_length=500)
    tb_text = traceback.format_exception(type(error), error, error.__traceback__)
    tb_str = "".join(tb_text)[-2000:]  # Last 2000 chars of traceback

    artifact_dir = ARTIFACTS_DIR / incident_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    incident = MaintenanceIncident(
        incident_id=incident_id,
        component=component,
        error_type=type(error).__name__,
        error_message=error_msg,
        artifact_dir=str(artifact_dir),
        traceback_text=tb_str,
        context=context or {},
    )

    # Write incident file
    incident_path = artifact_dir / "incident.json"
    with open(incident_path, "w") as f:
        json.dump(asdict(incident), f, indent=2, default=str)

    logger.info(f"Created incident capsule: {incident_id} at {artifact_dir}")
    return incident


def load_incident(incident_id: str) -> Optional[MaintenanceIncident]:
    """Load an incident capsule by ID."""
    artifact_dir = ARTIFACTS_DIR / incident_id
    incident_path = artifact_dir / "incident.json"

    if not incident_path.exists():
        return None

    with open(incident_path) as f:
        data = json.load(f)

    return MaintenanceIncident(**data)


def update_incident_status(
    incident_id: str,
    status: str,
    notes: str = "",
) -> Optional[MaintenanceIncident]:
    """Update an incident's status."""
    incident = load_incident(incident_id)
    if not incident:
        return None

    incident.status = status
    incident.updated_at = datetime.now(timezone.utc).isoformat()
    if notes:
        incident.repair_attempts.append({
            "timestamp": incident.updated_at,
            "status": status,
            "notes": notes,
        })

    artifact_dir = Path(incident.artifact_dir)
    incident_path = artifact_dir / "incident.json"
    with open(incident_path, "w") as f:
        json.dump(asdict(incident), f, indent=2, default=str)

    return incident


def list_incidents(status_filter: Optional[str] = None) -> List[MaintenanceIncident]:
    """List all incident capsules, optionally filtered by status."""
    incidents = []
    if not ARTIFACTS_DIR.exists():
        return incidents

    for incident_dir in sorted(ARTIFACTS_DIR.iterdir(), reverse=True):
        if not incident_dir.is_dir():
            continue
        incident_path = incident_dir / "incident.json"
        if not incident_path.exists():
            continue

        try:
            with open(incident_path) as f:
                data = json.load(f)
            incident = MaintenanceIncident(**data)
            if status_filter and incident.status != status_filter:
                continue
            incidents.append(incident)
        except (json.JSONDecodeError, TypeError):
            continue

    return incidents
