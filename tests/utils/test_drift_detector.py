"""
Tests for drift detector.

Sprint 6: Evaluation & Calibration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.drift_detector import (
    DriftDetector,
    DriftAlert,
    DriftCheckResult,
    ThresholdConfig,
    DEFAULT_THRESHOLDS,
)


# =============================================================================
# DATA CLASS TESTS
# =============================================================================

class TestThresholdConfig:
    """Tests for ThresholdConfig."""

    def test_default_consecutive_runs(self):
        """Default consecutive_runs is 1."""
        config = ThresholdConfig(
            metric="test",
            comparison="absolute",
            threshold=10.0,
            severity="red",
        )
        assert config.consecutive_runs == 1


class TestDriftAlert:
    """Tests for DriftAlert."""

    def test_to_dict(self):
        """to_dict returns all fields."""
        alert = DriftAlert(
            alert_type="test_alert",
            severity="red",
            metric_name="test_metric",
            baseline_value=80.0,
            current_value=70.0,
            threshold=5.0,
            message="Test message",
        )
        d = alert.to_dict()
        assert d["alert_type"] == "test_alert"
        assert d["severity"] == "red"
        assert d["baseline_value"] == 80.0
        assert d["current_value"] == 70.0


class TestDriftCheckResult:
    """Tests for DriftCheckResult."""

    def test_has_red_alerts_true(self):
        """has_red_alerts returns True when red_count > 0."""
        result = DriftCheckResult(red_count=1)
        assert result.has_red_alerts is True

    def test_has_red_alerts_false(self):
        """has_red_alerts returns False when red_count == 0."""
        result = DriftCheckResult(red_count=0, yellow_count=2)
        assert result.has_red_alerts is False

    def test_to_dict(self):
        """to_dict returns correct structure."""
        result = DriftCheckResult(
            red_count=1,
            yellow_count=2,
            alerts=[
                DriftAlert(
                    alert_type="test",
                    severity="red",
                    metric_name="metric",
                    baseline_value=0,
                    current_value=0,
                    threshold=0,
                    message="test",
                )
            ],
        )
        d = result.to_dict()
        assert d["red_count"] == 1
        assert d["yellow_count"] == 2
        assert len(d["alerts"]) == 1


class TestDefaultThresholds:
    """Tests for default thresholds."""

    def test_extraction_f1_drop_exists(self):
        """extraction_f1_drop threshold exists."""
        assert "extraction_f1_drop" in DEFAULT_THRESHOLDS
        config = DEFAULT_THRESHOLDS["extraction_f1_drop"]
        assert config.severity == "red"
        assert config.threshold == 5.0

    def test_abstention_rate_spike_exists(self):
        """abstention_rate_spike threshold exists."""
        assert "abstention_rate_spike" in DEFAULT_THRESHOLDS
        config = DEFAULT_THRESHOLDS["abstention_rate_spike"]
        assert config.threshold == 25.0

    def test_confidence_collapse_consecutive_runs(self):
        """confidence_collapse requires 3 consecutive runs."""
        assert "confidence_collapse" in DEFAULT_THRESHOLDS
        config = DEFAULT_THRESHOLDS["confidence_collapse"]
        assert config.consecutive_runs == 3


# =============================================================================
# DRIFT DETECTOR TESTS
# =============================================================================

class TestDriftDetector:
    """Tests for DriftDetector class."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    def detector(self, store):
        """Create detector with store."""
        return DriftDetector(store)

    def test_check_threshold_vs_baseline_triggers(self, store):
        """vs_baseline comparison triggers alert when difference exceeds threshold."""
        detector = DriftDetector(store)
        config = ThresholdConfig(
            metric="f1",
            comparison="vs_baseline",
            threshold=5.0,
            severity="red",
        )

        is_alert, message = detector._check_threshold(
            current=75.0,
            baseline=85.0,
            config=config,
        )

        assert is_alert is True
        assert "dropped" in message
        assert "10.0" in message  # 85 - 75 = 10

    def test_check_threshold_vs_baseline_no_trigger(self, store):
        """vs_baseline comparison doesn't trigger when within threshold."""
        detector = DriftDetector(store)
        config = ThresholdConfig(
            metric="f1",
            comparison="vs_baseline",
            threshold=5.0,
            severity="red",
        )

        is_alert, message = detector._check_threshold(
            current=82.0,
            baseline=85.0,
            config=config,
        )

        assert is_alert is False

    def test_check_threshold_absolute_triggers(self, store):
        """absolute comparison triggers when value exceeds threshold."""
        detector = DriftDetector(store)
        config = ThresholdConfig(
            metric="abstention_rate",
            comparison="absolute",
            threshold=25.0,
            severity="red",
        )

        is_alert, message = detector._check_threshold(
            current=30.0,
            baseline=0.0,
            config=config,
        )

        assert is_alert is True
        assert "exceeds" in message

    def test_check_threshold_absolute_no_trigger(self, store):
        """absolute comparison doesn't trigger when below threshold."""
        detector = DriftDetector(store)
        config = ThresholdConfig(
            metric="abstention_rate",
            comparison="absolute",
            threshold=25.0,
            severity="red",
        )

        is_alert, message = detector._check_threshold(
            current=20.0,
            baseline=0.0,
            config=config,
        )

        assert is_alert is False

    def test_check_threshold_absolute_min_triggers(self, store):
        """absolute_min comparison triggers when below minimum."""
        detector = DriftDetector(store)
        config = ThresholdConfig(
            metric="top10_recall",
            comparison="absolute_min",
            threshold=60.0,
            severity="red",
        )

        is_alert, message = detector._check_threshold(
            current=55.0,
            baseline=0.0,
            config=config,
        )

        assert is_alert is True
        assert "below" in message

    def test_check_threshold_absolute_min_no_trigger(self, store):
        """absolute_min comparison doesn't trigger when above minimum."""
        detector = DriftDetector(store)
        config = ThresholdConfig(
            metric="top10_recall",
            comparison="absolute_min",
            threshold=60.0,
            severity="red",
        )

        is_alert, message = detector._check_threshold(
            current=75.0,
            baseline=0.0,
            config=config,
        )

        assert is_alert is False

    @pytest.mark.asyncio
    async def test_check_drift_no_alerts(self, detector):
        """No alerts when metrics are healthy."""
        result = await detector.check_drift(
            current_metrics={
                "extraction_f1": 90.0,
                "abstention_rate": 5.0,
                "top10_recall": 80.0,
            },
            baseline_metrics={
                "extraction_f1": 88.0,
                "abstention_rate": 4.0,
                "top10_recall": 78.0,
            },
            save_alerts=False,
        )

        assert len(result.alerts) == 0
        assert result.red_count == 0
        assert result.yellow_count == 0

    @pytest.mark.asyncio
    async def test_check_drift_triggers_alert(self, detector):
        """Alert triggered when threshold breached."""
        result = await detector.check_drift(
            current_metrics={
                "extraction_f1": 70.0,  # Dropped 15 points
            },
            baseline_metrics={
                "extraction_f1": 85.0,
            },
            save_alerts=False,
        )

        assert result.red_count > 0
        assert any(a.alert_type == "extraction_f1_drop" for a in result.alerts)

    @pytest.mark.asyncio
    async def test_check_drift_saves_to_db(self, store):
        """Alerts are saved to database."""
        detector = DriftDetector(store)

        await detector.check_drift(
            current_metrics={
                "abstention_rate": 30.0,  # Above 25% threshold
            },
            baseline_metrics={},
            save_alerts=True,
        )

        # Check alert was saved
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM drift_alerts WHERE alert_type = 'abstention_rate_spike'"
        )
        row = await cursor.fetchone()
        assert row[0] >= 1

    @pytest.mark.asyncio
    async def test_check_drift_skips_missing_metrics(self, detector):
        """Skips checks for metrics not in current_metrics."""
        result = await detector.check_drift(
            current_metrics={
                "some_other_metric": 50.0,
            },
            baseline_metrics={},
            save_alerts=False,
        )

        # Should not crash, no alerts for unknown metrics
        assert isinstance(result, DriftCheckResult)


class TestDriftDetectorConsecutiveRuns:
    """Tests for consecutive run detection."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_consecutive_runs_count(self, store):
        """_count_consecutive_failures counts correctly."""
        detector = DriftDetector(store)

        # Add two unacknowledged alerts
        await store.save_drift_alert(
            alert_type="confidence_collapse",
            severity="red",
            metric_name="median_confidence",
            baseline_value=70.0,
            current_value=50.0,
            threshold=55.0,
        )
        await store.save_drift_alert(
            alert_type="confidence_collapse",
            severity="red",
            metric_name="median_confidence",
            baseline_value=70.0,
            current_value=52.0,
            threshold=55.0,
        )

        count = await detector._count_consecutive_failures(
            "confidence_collapse", "median_confidence"
        )
        assert count == 2


class TestDriftDetectorBaseline:
    """Tests for baseline metric retrieval."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_get_baseline_metrics_no_history(self, store):
        """Returns None when no history exists."""
        detector = DriftDetector(store)

        baseline = await detector.get_baseline_metrics("extraction")
        assert baseline is None

    @pytest.mark.asyncio
    async def test_get_baseline_metrics_with_history(self, store):
        """Returns baseline from oldest run in window."""
        # Save an evaluation run (use 0-100 scale to match thresholds)
        await store.save_evaluation_run(
            run_id="test_run_1",
            run_type="extraction",
            model_version="v1",
            gold_set_version="v1",
            metrics={
                "extraction": {
                    "f1": 85.0,
                    "precision": 90.0,
                    "recall": 80.0,
                }
            },
        )

        detector = DriftDetector(store)
        baseline = await detector.get_baseline_metrics("extraction")

        assert baseline is not None
        assert baseline["extraction_f1"] == 85.0

    @pytest.mark.asyncio
    async def test_check_evaluation_drift(self, store):
        """check_evaluation_drift uses baseline and checks drift."""
        # Save baseline run (use 0-100 scale to match thresholds)
        await store.save_evaluation_run(
            run_id="baseline_run",
            run_type="extraction",
            model_version="v1",
            gold_set_version="v1",
            metrics={
                "extraction": {
                    "f1": 85.0,
                    "abstention_rate": 10.0,
                }
            },
        )

        detector = DriftDetector(store)

        # Check with degraded metrics (F1 dropped 15 points: 85 -> 70)
        result = await detector.check_evaluation_drift(
            evaluation_run_id=1,
            current_metrics={
                "extraction": {
                    "f1": 70.0,  # Dropped 15 points
                    "abstention_rate": 15.0,
                }
            },
            run_type="extraction",
        )

        # Should have alerts for F1 drop (threshold is 5.0 points)
        assert result.red_count > 0 or result.yellow_count > 0


class TestDriftDetectorSlack:
    """Tests for Slack notifications."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_slack_notification_sent_for_red_alerts(self, store):
        """Slack notification sent when red alerts present."""
        mock_slack = MagicMock()
        mock_slack.is_configured = True
        mock_slack.send_message = AsyncMock()

        detector = DriftDetector(store, slack_notifier=mock_slack)

        await detector.check_drift(
            current_metrics={
                "abstention_rate": 30.0,  # RED alert
            },
            baseline_metrics={},
            save_alerts=False,
            notify_slack=True,
        )

        mock_slack.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_slack_notification_not_sent_for_yellow_only(self, store):
        """Slack notification not sent for yellow-only alerts."""
        mock_slack = MagicMock()
        mock_slack.is_configured = True
        mock_slack.send_message = AsyncMock()

        # Use custom threshold with yellow severity
        custom_thresholds = {
            "test_yellow": ThresholdConfig(
                metric="test_metric",
                comparison="absolute",
                threshold=10.0,
                severity="yellow",
            ),
        }

        detector = DriftDetector(store, thresholds=custom_thresholds, slack_notifier=mock_slack)

        await detector.check_drift(
            current_metrics={
                "test_metric": 15.0,
            },
            baseline_metrics={},
            save_alerts=False,
            notify_slack=True,
        )

        mock_slack.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_slack_notification_skipped_when_disabled(self, store):
        """Slack notification skipped when notify_slack=False."""
        mock_slack = MagicMock()
        mock_slack.is_configured = True
        mock_slack.send_message = AsyncMock()

        detector = DriftDetector(store, slack_notifier=mock_slack)

        await detector.check_drift(
            current_metrics={
                "abstention_rate": 30.0,
            },
            baseline_metrics={},
            save_alerts=False,
            notify_slack=False,
        )

        mock_slack.send_message.assert_not_called()
