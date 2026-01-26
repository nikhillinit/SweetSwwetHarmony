"""
Event Types and Payloads for Monitoring Subsystem

Provides type-safe event contracts for async processing via notion_outbox.
All events use Pydantic for validation and include versioning for future changes.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class EventType(str, Enum):
    """
    Event types for routing in notion_outbox worker.

    Each event type has a corresponding Payload class and handler.
    """
    # Existing event type for Notion writes
    NOTION_PUSH = "notion_push"

    # Monitoring events
    PROFILE_UPDATE_REQUESTED = "profile_update_requested"


class ProfileUpdateRequestedPayload(BaseModel):
    """
    Payload for profile update request events.

    Triggered when a watched URL changes significantly and needs re-profiling.
    """
    event_type: EventType = Field(
        default=EventType.PROFILE_UPDATE_REQUESTED,
        description="Event type for routing"
    )
    version: int = Field(
        default=1,
        description="Payload version for backwards compatibility"
    )
    watch_id: int = Field(
        description="ID of the watch that triggered this event"
    )
    snapshot_id: int = Field(
        description="ID of the new snapshot"
    )
    diff_id: int = Field(
        description="ID of the diff that triggered this event"
    )
    trigger: str = Field(
        description="What triggered the update: 'high_severity', 'host_changed', 'state_change'"
    )
    canonical_key: str = Field(
        description="Canonical key of the company being monitored"
    )
    url: Optional[str] = Field(
        default=None,
        description="URL that was monitored (for convenience)"
    )

    def dedupe_key(self) -> str:
        """
        Generate idempotency key to prevent duplicate processing.

        Uses watch_id + snapshot_id to ensure each snapshot triggers
        at most one profile update.
        """
        return f"profile_update:{self.watch_id}:{self.snapshot_id}"

    class Config:
        use_enum_values = True


# Type registry for event routing
EVENT_PAYLOAD_TYPES = {
    EventType.PROFILE_UPDATE_REQUESTED: ProfileUpdateRequestedPayload,
}


def parse_event_payload(payload_dict: dict) -> Optional[BaseModel]:
    """
    Parse a payload dict into the appropriate typed payload.

    Args:
        payload_dict: Raw payload from outbox

    Returns:
        Typed payload or None if unknown event type
    """
    event_type_str = payload_dict.get("event_type")
    if not event_type_str:
        return None

    try:
        event_type = EventType(event_type_str)
    except ValueError:
        return None

    payload_class = EVENT_PAYLOAD_TYPES.get(event_type)
    if not payload_class:
        return None

    return payload_class(**payload_dict)
