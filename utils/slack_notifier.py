"""
Slack Notifier for Discovery Engine

Sends notifications to Slack via webhook for:
- High-confidence signals (>= 0.7) pushed to Notion
- Pipeline health alerts (CRITICAL/DEGRADED)
- Daily summary digests

Usage:
    notifier = SlackNotifier(webhook_url="https://hooks.slack.com/...")

    # Notify on high-confidence signal
    await notifier.notify_high_confidence_signal(
        company_name="Acme Corp",
        confidence=0.85,
        signal_types=["github", "sec_edgar"],
        notion_url="https://notion.so/..."
    )

    # Notify on health issue
    await notifier.notify_health_alert(
        status="CRITICAL",
        anomalies=["Volume spike from github", "Stale signals"]
    )

Environment:
    SLACK_WEBHOOK_URL - Slack incoming webhook URL
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class SlackConfig:
    """Slack notifier configuration"""
    webhook_url: Optional[str] = None
    channel: Optional[str] = None  # Override channel (optional)
    username: str = "Discovery Engine"
    icon_emoji: str = ":mag:"

    # Notification settings
    notify_high_confidence: bool = True
    notify_health_alerts: bool = True
    notify_daily_summary: bool = True

    # Thresholds
    high_confidence_threshold: float = 0.7

    @classmethod
    def from_env(cls) -> SlackConfig:
        """Load from environment variables"""
        return cls(
            webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
            channel=os.getenv("SLACK_CHANNEL"),
            notify_high_confidence=os.getenv("SLACK_NOTIFY_HIGH_CONFIDENCE", "true").lower() == "true",
            notify_health_alerts=os.getenv("SLACK_NOTIFY_HEALTH_ALERTS", "true").lower() == "true",
            notify_daily_summary=os.getenv("SLACK_NOTIFY_DAILY_SUMMARY", "true").lower() == "true",
            high_confidence_threshold=float(os.getenv("SLACK_HIGH_CONFIDENCE_THRESHOLD", "0.7")),
        )


# =============================================================================
# SLACK NOTIFIER
# =============================================================================

class SlackNotifier:
    """
    Async Slack webhook notifier.

    Gracefully degrades if webhook URL is not configured (logs instead of failing).
    """

    def __init__(self, config: Optional[SlackConfig] = None):
        """
        Initialize notifier.

        Args:
            config: SlackConfig instance (loads from env if None)
        """
        self.config = config or SlackConfig.from_env()
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        """Check if Slack webhook is configured"""
        return bool(self.config.webhook_url)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # CORE SEND
    # =========================================================================

    async def _send(self, payload: Dict[str, Any]) -> bool:
        """
        Send payload to Slack webhook.

        Returns True if sent successfully, False otherwise.
        """
        if not self.is_configured:
            logger.debug("Slack webhook not configured, skipping notification")
            return False

        # Add defaults
        if self.config.channel:
            payload["channel"] = self.config.channel
        payload.setdefault("username", self.config.username)
        payload.setdefault("icon_emoji", self.config.icon_emoji)

        try:
            client = await self._get_client()
            response = await client.post(
                self.config.webhook_url,
                json=payload
            )

            if response.status_code == 200:
                logger.debug("Slack notification sent successfully")
                return True
            else:
                logger.warning(f"Slack webhook returned {response.status_code}: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False

    # =========================================================================
    # HIGH-CONFIDENCE SIGNAL NOTIFICATION
    # =========================================================================

    async def notify_high_confidence_signal(
        self,
        company_name: str,
        confidence: float,
        signal_types: List[str],
        sources_count: int = 1,
        notion_url: Optional[str] = None,
        canonical_key: Optional[str] = None,
        why_now: Optional[str] = None,
    ) -> bool:
        """
        Notify when a high-confidence signal is pushed to Notion.

        Args:
            company_name: Company name
            confidence: Confidence score (0.0 - 1.0)
            signal_types: List of signal types detected
            sources_count: Number of sources
            notion_url: Link to Notion page
            canonical_key: Deduplication key
            why_now: Why this company, why now

        Returns:
            True if notification sent successfully
        """
        if not self.config.notify_high_confidence:
            return False

        if confidence < self.config.high_confidence_threshold:
            return False

        # Build message
        confidence_pct = f"{confidence:.0%}"
        signals_str = ", ".join(signal_types)

        # Determine emoji based on confidence
        if confidence >= 0.85:
            emoji = ":star2:"
        elif confidence >= 0.7:
            emoji = ":star:"
        else:
            emoji = ":eyes:"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} New High-Confidence Signal",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Company:*\n{company_name}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Confidence:*\n{confidence_pct}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Signals:*\n{signals_str}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Sources:*\n{sources_count}"
                    }
                ]
            }
        ]

        # Add why now if provided
        if why_now:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Why Now:*\n{why_now}"
                }
            })

        # Add Notion link if provided
        if notion_url:
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "View in Notion",
                            "emoji": True
                        },
                        "url": notion_url,
                        "action_id": "view_notion"
                    }
                ]
            })

        # Add context
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Detected at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                }
            ]
        })

        payload = {
            "blocks": blocks,
            "text": f"New high-confidence signal: {company_name} ({confidence_pct})"  # Fallback
        }

        return await self._send(payload)

    # =========================================================================
    # HEALTH ALERT NOTIFICATION
    # =========================================================================

    async def notify_health_alert(
        self,
        status: str,
        anomalies: List[str],
        total_signals: int = 0,
        stale_signals: int = 0,
        suspicious_signals: int = 0,
    ) -> bool:
        """
        Notify when signal health is degraded or critical.

        Args:
            status: Health status (HEALTHY, DEGRADED, CRITICAL)
            anomalies: List of anomaly descriptions
            total_signals: Total signals analyzed
            stale_signals: Number of stale signals
            suspicious_signals: Number of suspicious signals

        Returns:
            True if notification sent successfully
        """
        if not self.config.notify_health_alerts:
            return False

        # Only alert on DEGRADED or CRITICAL
        if status == "HEALTHY":
            return False

        # Emoji based on status
        emoji = ":rotating_light:" if status == "CRITICAL" else ":warning:"
        color = "#FF0000" if status == "CRITICAL" else "#FFA500"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} Signal Health Alert: {status}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Total Signals:*\n{total_signals}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Stale Signals:*\n{stale_signals}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Suspicious:*\n{suspicious_signals}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Anomalies:*\n{len(anomalies)}"
                    }
                ]
            }
        ]

        # Add anomaly list
        if anomalies:
            anomaly_text = "\n".join(f"• {a}" for a in anomalies[:5])
            if len(anomalies) > 5:
                anomaly_text += f"\n... and {len(anomalies) - 5} more"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Detected Issues:*\n{anomaly_text}"
                }
            })

        # Add context
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Run `python run_pipeline.py health --verbose` for details"
                }
            ]
        })

        payload = {
            "blocks": blocks,
            "text": f"Signal health {status}: {len(anomalies)} anomalies detected"  # Fallback
        }

        return await self._send(payload)

    # =========================================================================
    # DAILY SUMMARY NOTIFICATION
    # =========================================================================

    async def notify_daily_summary(
        self,
        signals_collected: int,
        signals_pushed: int,
        high_confidence_count: int,
        collectors_succeeded: int,
        collectors_failed: int,
        health_status: str = "HEALTHY",
    ) -> bool:
        """
        Send daily pipeline summary.

        Args:
            signals_collected: Total signals collected
            signals_pushed: Signals pushed to Notion
            high_confidence_count: High-confidence signals
            collectors_succeeded: Collectors that succeeded
            collectors_failed: Collectors that failed
            health_status: Overall health status

        Returns:
            True if notification sent successfully
        """
        if not self.config.notify_daily_summary:
            return False

        # Status emoji
        if health_status == "HEALTHY" and collectors_failed == 0:
            status_emoji = ":white_check_mark:"
        elif health_status == "CRITICAL" or collectors_failed > 2:
            status_emoji = ":x:"
        else:
            status_emoji = ":warning:"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{status_emoji} Daily Pipeline Summary",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Signals Collected:*\n{signals_collected}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Pushed to Notion:*\n{signals_pushed}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*High Confidence:*\n{high_confidence_count}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Health:*\n{health_status}"
                    }
                ]
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Collectors OK:*\n{collectors_succeeded}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Collectors Failed:*\n{collectors_failed}"
                    }
                ]
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Pipeline completed at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                    }
                ]
            }
        ]

        payload = {
            "blocks": blocks,
            "text": f"Daily summary: {signals_collected} collected, {signals_pushed} pushed, {high_confidence_count} high-confidence"
        }

        return await self._send(payload)

    # =========================================================================
    # SIMPLE TEXT NOTIFICATION
    # =========================================================================

    async def notify_text(self, message: str, emoji: str = ":information_source:") -> bool:
        """
        Send a simple text notification.

        Args:
            message: Text message to send
            emoji: Emoji to prepend

        Returns:
            True if notification sent successfully
        """
        payload = {
            "text": f"{emoji} {message}"
        }
        return await self._send(payload)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

# Global notifier instance (lazy-loaded)
_notifier: Optional[SlackNotifier] = None


def get_notifier() -> SlackNotifier:
    """Get or create global notifier instance"""
    global _notifier
    if _notifier is None:
        _notifier = SlackNotifier()
    return _notifier


async def notify_high_confidence(
    company_name: str,
    confidence: float,
    signal_types: List[str],
    **kwargs
) -> bool:
    """Convenience function to notify high-confidence signal"""
    notifier = get_notifier()
    return await notifier.notify_high_confidence_signal(
        company_name=company_name,
        confidence=confidence,
        signal_types=signal_types,
        **kwargs
    )


async def notify_health(status: str, anomalies: List[str], **kwargs) -> bool:
    """Convenience function to notify health alert"""
    notifier = get_notifier()
    return await notifier.notify_health_alert(
        status=status,
        anomalies=anomalies,
        **kwargs
    )


async def notify_summary(**kwargs) -> bool:
    """Convenience function to send daily summary"""
    notifier = get_notifier()
    return await notifier.notify_daily_summary(**kwargs)


# =============================================================================
# CLI TEST
# =============================================================================

async def _test_notifications():
    """Test notification sending (requires SLACK_WEBHOOK_URL env var)"""
    notifier = SlackNotifier()

    if not notifier.is_configured:
        print("SLACK_WEBHOOK_URL not set. Skipping test.")
        return

    print("Testing Slack notifications...")

    # Test high-confidence signal
    result = await notifier.notify_high_confidence_signal(
        company_name="Test Company Inc",
        confidence=0.87,
        signal_types=["github", "sec_edgar"],
        sources_count=2,
        why_now="Multiple funding signals detected",
    )
    print(f"High-confidence notification: {'Sent' if result else 'Failed'}")

    # Test health alert
    result = await notifier.notify_health_alert(
        status="DEGRADED",
        anomalies=["Volume spike from github", "3 stale signals"],
        total_signals=150,
        stale_signals=3,
    )
    print(f"Health alert notification: {'Sent' if result else 'Failed'}")

    # Test daily summary
    result = await notifier.notify_daily_summary(
        signals_collected=42,
        signals_pushed=8,
        high_confidence_count=3,
        collectors_succeeded=7,
        collectors_failed=1,
        health_status="DEGRADED",
    )
    print(f"Daily summary notification: {'Sent' if result else 'Failed'}")

    await notifier.close()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(_test_notifications())
