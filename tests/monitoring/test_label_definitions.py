"""Tests for label taxonomy definitions."""

from monitoring.label_definitions import (
    GoldLabel,
    LabelLayer,
    LagWindow,
    OperatorLabel,
    OutcomeLabel,
    LAG_WINDOWS,
    LABEL_PRIORITY,
    resolve_label_conflict,
)


class TestLabelLayers:
    def test_operator_labels(self):
        assert OperatorLabel.TP.value == "TP"
        assert OperatorLabel.FP.value == "FP"
        assert OperatorLabel.UNSURE.value == "UNSURE"

    def test_outcome_labels(self):
        assert OutcomeLabel.FUNDED.value == "funded"
        assert OutcomeLabel.PASSED.value == "passed"
        assert OutcomeLabel.TRACKING.value == "tracking"

    def test_gold_labels(self):
        assert GoldLabel.TP.value == "TP"
        assert GoldLabel.BORDERLINE.value == "BORDERLINE"


class TestLagWindows:
    def test_operator_is_immediate(self):
        lag = LAG_WINDOWS[LabelLayer.OPERATOR]
        assert lag.min_days == 0

    def test_outcome_has_lag(self):
        lag = LAG_WINDOWS[LabelLayer.OUTCOME]
        assert lag.min_days == 30
        assert lag.recommended_days == 90

    def test_gold_no_lag(self):
        lag = LAG_WINDOWS[LabelLayer.GOLD]
        assert lag.min_days == 0


class TestLabelPriority:
    def test_gold_highest(self):
        assert LABEL_PRIORITY[LabelLayer.GOLD] > LABEL_PRIORITY[LabelLayer.OUTCOME]
        assert LABEL_PRIORITY[LabelLayer.OUTCOME] > LABEL_PRIORITY[LabelLayer.OPERATOR]


class TestConflictResolution:
    def test_gold_wins(self):
        label, layer = resolve_label_conflict("FP", "funded", "TP")
        assert label == "TP"
        assert layer == LabelLayer.GOLD

    def test_outcome_wins_over_operator(self):
        label, layer = resolve_label_conflict("FP", "funded", None)
        assert label == "TP"
        assert layer == LabelLayer.OUTCOME

    def test_operator_fallback(self):
        label, layer = resolve_label_conflict("TP", None, None)
        assert label == "TP"
        assert layer == LabelLayer.OPERATOR

    def test_no_labels(self):
        label, layer = resolve_label_conflict(None, None, None)
        assert label is None

    def test_outcome_passed_maps_to_fp(self):
        label, layer = resolve_label_conflict("TP", "passed", None)
        assert label == "FP"
        assert layer == LabelLayer.OUTCOME

    def test_outcome_committed_maps_to_tp(self):
        label, layer = resolve_label_conflict(None, "committed", None)
        assert label == "TP"
