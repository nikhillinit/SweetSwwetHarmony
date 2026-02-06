"""Alert notifier — dedup via audit_log, Slack with retry.

Uses the existing audit_log table as persistent state to suppress
repeated notifications within a configurable cooldown period.
"""

import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from ops.monitoring.alerts import Alert
from ops.storage import OpsStorage

logger = logging.getLogger(__name__)


class OpsAlertNotifier:
    """Send alert notifications to Slack with dedup and retry."""

    def __init__(self, storage: OpsStorage,
                 slack_notifier=None,
                 cooldown_minutes: int = 60):
        self.storage = storage
        self._slack = slack_notifier
        self.cooldown_minutes = int(os.environ.get(
            "OPS_ALERT_NOTIFY_COOLDOWN_MINUTES", cooldown_minutes))
        self._notify_resolved = os.environ.get(
            "OPS_ALERT_NOTIFY_RESOLVED", "false").lower() == "true"
        self._include_details = os.environ.get(
            "OPS_ALERT_INCLUDE_DETAILS", "false").lower() == "true"

    @property
    def slack(self):
        """Lazy-load SlackNotifier."""
        if self._slack is None:
            try:
                from utils.slack_notifier import SlackNotifier
                self._slack = SlackNotifier()
            except Exception:
                self._slack = None
        return self._slack

    def send_alerts(self, alerts: list,
                    previous_alerts: Optional[list] = None) -> dict:
        """Send new alerts, suppress duplicates, optionally resolve cleared.

        Returns dict with sent/suppressed/failed counts.
        """
        sent = 0
        suppressed = 0
        failed = 0

        for alert in alerts:
            if self._is_in_cooldown(alert.fingerprint):
                suppressed += 1
                continue

            success = self._send_to_slack(alert)
            if success:
                self._record_sent(alert)
                sent += 1
            else:
                self._record_failed(alert)
                failed += 1

        # Optionally send resolved notifications
        if self._notify_resolved and previous_alerts is not None:
            current_fps = {a.fingerprint for a in alerts}
            for prev in previous_alerts:
                if prev.fingerprint not in current_fps:
                    if not self._is_in_cooldown(f"resolved:{prev.fingerprint}"):
                        self._send_resolved(prev)
                        self._record_resolved(prev.fingerprint)

        return {"sent": sent, "suppressed": suppressed, "failed": failed}

    def _is_in_cooldown(self, fingerprint: str) -> bool:
        """Check if alert was sent within cooldown period."""
        with self.storage.read_transaction() as conn:
            row = conn.execute(
                """
                SELECT timestamp FROM audit_log
                WHERE operation = 'ALERT_SENT'
                AND reason LIKE ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (f"%{fingerprint}%",),
            ).fetchone()

        if row is None:
            return False

        try:
            last_sent = datetime.fromisoformat(row[0])
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            elapsed_minutes = (now - last_sent).total_seconds() / 60
            return elapsed_minutes < self.cooldown_minutes
        except (ValueError, TypeError):
            return False

    def _record_sent(self, alert: Alert) -> None:
        """Record alert send in audit_log."""
        self.storage.log_audit(
            operation="ALERT_SENT",
            target_type="alert",
            user="system",
            after_state=json.dumps({
                "rule_name": alert.rule_name,
                "severity": alert.severity,
                "message": self._sanitize_message(alert.message),
            }),
            reason=alert.fingerprint,
        )

    def _record_failed(self, alert: Alert) -> None:
        """Record failed notification attempt."""
        self.storage.log_audit(
            operation="ALERT_NOTIFY_FAILED",
            target_type="alert",
            user="system",
            reason=alert.fingerprint,
        )

    def _record_resolved(self, fingerprint: str) -> None:
        """Record resolved alert in audit_log."""
        self.storage.log_audit(
            operation="ALERT_RESOLVED",
            target_type="alert",
            user="system",
            reason=f"resolved:{fingerprint}",
        )

    def _send_to_slack(self, alert: Alert) -> bool:
        """Send alert to Slack with bounded retry (3 attempts)."""
        if self.slack is None or not getattr(self.slack, "is_configured", False):
            logger.debug("Slack not configured, skipping notification")
            return True  # Treat as success to record audit entry

        message = self._format_message(alert)
        delays = [1, 2, 4]  # Exponential backoff

        for attempt, delay in enumerate(delays):
            try:
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    # Can't await in sync context — create task
                    future = asyncio.ensure_future(
                        self.slack.notify_text(message)
                    )
                    return True  # Fire-and-forget
                else:
                    result = asyncio.run(self.slack.notify_text(message))
                    if result:
                        return True
            except Exception as e:
                logger.warning(
                    "Slack attempt %d/%d failed: %s",
                    attempt + 1, len(delays), e
                )
                if attempt < len(delays) - 1:
                    time.sleep(delay)

        return False

    def _send_resolved(self, alert: Alert) -> None:
        """Send resolved notification (best-effort)."""
        if self.slack is None or not getattr(self.slack, "is_configured", False):
            return

        message = f"[RESOLVED] {alert.rule_name}: {self._sanitize_message(alert.message)}"
        try:
            import asyncio
            asyncio.run(self.slack.notify_text(message))
        except Exception as e:
            logger.warning("Failed to send resolved notification: %s", e)

    def _format_message(self, alert: Alert) -> str:
        """Format alert for Slack — aggregates only by default."""
        sev = alert.severity.upper()
        msg = self._sanitize_message(alert.message)
        return f"[{sev}] {alert.rule_name}: {msg}"

    @staticmethod
    def _sanitize_message(text: str, max_length: int = 500) -> str:
        """Strip sensitive data and truncate."""
        import re
        # Strip email addresses
        sanitized = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '[REDACTED]', text)
        # Truncate
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length - 3] + "..."
        return sanitized
