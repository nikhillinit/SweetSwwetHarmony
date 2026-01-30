"""
Monitoring Subsystem for Discovery Engine

Website change tracking via watches, snapshots, diffs, and alerts.
Enables: "What changed on this company's website since we last checked?"

Main components:
- WebsiteMonitor: Core monitoring logic
- MonitorStore: Database operations
- DiffEngine: Severity calculation + semantic drift
- PageTypeClassifier: URL/content classification (pricing, careers, terms, etc.)
- EventType: Type-safe event routing
- RetentionManager: Snapshot/diff pruning
"""

from monitoring.events import EventType, ProfileUpdateRequestedPayload
from monitoring.models import (
    Watch,
    Snapshot,
    Diff,
    SeverityComponents,
    MonitoringAlert,
    CanonicalKeyAlias,
    MonitoringConfig,
)
from monitoring.monitor_store import MonitorStore
from monitoring.diff_engine import DiffEngine, DiffResult
from monitoring.website_monitor import WebsiteMonitor, MonitoringResult
from monitoring.retention import RetentionManager
from monitoring.page_type_classifier import (
    PageTypeClassifier,
    PageClassification,
    PageType,
    classify_page,
)

__all__ = [
    # Events
    "EventType",
    "ProfileUpdateRequestedPayload",
    # Models
    "Watch",
    "Snapshot",
    "Diff",
    "SeverityComponents",
    "MonitoringAlert",
    "CanonicalKeyAlias",
    "MonitoringConfig",
    # Core classes
    "MonitorStore",
    "DiffEngine",
    "DiffResult",
    "WebsiteMonitor",
    "MonitoringResult",
    "RetentionManager",
    # Page classification
    "PageTypeClassifier",
    "PageClassification",
    "PageType",
    "classify_page",
]
