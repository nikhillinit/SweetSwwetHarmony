"""Repair agent for automated collector maintenance.

Orchestrates automated collector repairs:
1. Load incident from capsule folder
2. Generate repair prompt with diagnostic context
3. Call Claude Code CLI for patch generation
4. Validate and apply patch

Requires claude CLI to be installed.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from ops.maintenance.incident import (
    MaintenanceIncident,
    load_incident,
    update_incident_status,
    list_incidents,
)
from ops.maintenance.claude_code_cli import ClaudeCodeCLI
from ops.utils import InputSanitizer

logger = logging.getLogger(__name__)


class RepairAgent:
    """Orchestrates automated collector repairs."""

    def __init__(self, timeout: int = 300):
        self.cli = ClaudeCodeCLI(timeout=timeout)

    @property
    def available(self) -> bool:
        return self.cli.available

    def _build_repair_prompt(self, incident: MaintenanceIncident) -> str:
        """Build a repair prompt from an incident capsule."""
        safe_error = InputSanitizer.sanitize_for_llm(
            incident.error_message, max_length=500
        )
        safe_traceback = InputSanitizer.sanitize_for_llm(
            incident.traceback_text, max_length=1500
        )
        safe_context = json.dumps(incident.context, indent=2, default=str)[:1000]

        return f"""Diagnose and suggest a fix for this collector failure:

Component: {incident.component}
Error Type: {incident.error_type}
Error Message: {safe_error}

Traceback:
{safe_traceback}

Context:
{safe_context}

Please:
1. Identify the root cause
2. Suggest a minimal, safe fix
3. Explain what changed and why
"""

    def repair_incident(
        self, incident_id: str
    ) -> Dict[str, Any]:
        """Attempt to repair a specific incident."""
        if not self.available:
            return {
                "success": False,
                "error": "claude CLI not available",
            }

        incident = load_incident(incident_id)
        if not incident:
            return {
                "success": False,
                "error": f"Incident {incident_id} not found",
            }

        if incident.status == "resolved":
            return {
                "success": True,
                "message": "Incident already resolved",
            }

        # Update status to investigating
        update_incident_status(incident_id, "investigating", "Repair agent started")

        prompt = self._build_repair_prompt(incident)
        result = self.cli.call(prompt, output_format="text")

        if result["success"]:
            update_incident_status(
                incident_id,
                "resolved",
                f"Repair suggestion generated:\n{result['output'][:500]}",
            )
        else:
            update_incident_status(
                incident_id,
                "open",
                f"Repair attempt failed: {result.get('error', 'unknown')}",
            )

        return result

    def repair_latest(self) -> Dict[str, Any]:
        """Attempt to repair the most recent open incident."""
        open_incidents = list_incidents(status_filter="open")
        if not open_incidents:
            return {
                "success": True,
                "message": "No open incidents",
            }

        latest = open_incidents[0]
        return self.repair_incident(latest.incident_id)
