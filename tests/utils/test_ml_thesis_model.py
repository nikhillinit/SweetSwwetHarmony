"""Tests for MLThesisModel wrapper."""

import os
import tempfile

import pytest


class TestMLThesisModelTrainPredict:
    """Test train/predict lifecycle."""

    def _make_model(self):
        from utils.ml_thesis_model import MLThesisModel
        return MLThesisModel()

    def _synthetic_data(self):
        """Create synthetic dataset with clear separation."""
        positive_texts = [
            "meal kit delivery service for healthy eating",
            "fitness app for personalized workout plans",
            "beauty brand selling skincare products direct to consumer",
            "travel booking platform for unique hotel experiences",
            "consumer marketplace for secondhand fashion",
            "wellness app for meditation and mental health",
            "organic beverage brand for health-conscious consumers",
            "subscription snack box delivered monthly",
            "restaurant discovery platform for foodies",
            "p2p marketplace for vacation rental experiences",
        ] * 3  # 30 positive

        negative_texts = [
            "enterprise saas platform for developer tools",
            "b2b api management infrastructure service",
            "blockchain cryptocurrency trading exchange",
            "consulting agency for digital transformation",
            "devops monitoring cli framework plugin",
            "series c funded logistics data platform",
            "web3 nft defi token governance protocol",
            "cloud infrastructure sdk library for developers",
            "enterprise security compliance automation tool",
            "b2b analytics dashboard for supply chain",
        ] * 3  # 30 negative

        texts = positive_texts + negative_texts
        labels = [1] * len(positive_texts) + [0] * len(negative_texts)
        return texts, labels

    def test_train_returns_metrics(self):
        model = self._make_model()
        texts, labels = self._synthetic_data()
        metrics = model.train(texts, labels)

        assert 0.0 <= metrics.precision <= 1.0
        assert 0.0 <= metrics.recall <= 1.0
        assert 0.0 <= metrics.f1 <= 1.0
        assert metrics.train_size > 0
        assert metrics.test_size > 0
        assert metrics.positive_count == sum(labels)
        assert metrics.negative_count == len(labels) - sum(labels)

    def test_predict_proba_returns_float(self):
        model = self._make_model()
        texts, labels = self._synthetic_data()
        model.train(texts, labels)

        prob = model.predict_proba("meal kit delivery for healthy eating")
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0

    def test_predict_proba_empty_text_returns_zero(self):
        model = self._make_model()
        texts, labels = self._synthetic_data()
        model.train(texts, labels)

        assert model.predict_proba("") == 0.0
        assert model.predict_proba("   ") == 0.0

    def test_predict_without_training_raises(self):
        model = self._make_model()
        with pytest.raises(RuntimeError, match="No model loaded"):
            model.predict_proba("test")

    def test_class_weight_balanced(self):
        """Verify class_weight='balanced' is set."""
        model = self._make_model()
        texts, labels = self._synthetic_data()
        model.train(texts, labels)

        clf = model._pipeline.named_steps["clf"]
        assert clf.class_weight == "balanced"

    def test_mismatched_lengths_raises(self):
        model = self._make_model()
        with pytest.raises(ValueError, match="same length"):
            model.train(["a", "b"], [1])


class TestMLThesisModelSaveLoad:
    """Test model persistence with model_id versioning."""

    def _trained_model(self):
        from utils.ml_thesis_model import MLThesisModel
        model = MLThesisModel()
        texts = ["positive example"] * 15 + ["negative example"] * 15
        labels = [1] * 15 + [0] * 15
        model.train(texts, labels)
        return model

    def test_save_load_roundtrip(self):
        model = self._trained_model()

        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
            path = f.name

        try:
            model.save(path)

            from utils.ml_thesis_model import MLThesisModel
            loaded = MLThesisModel()
            loaded.load(path)

            # Predictions should match
            test_text = "fitness app for wellness"
            original_pred = model.predict_proba(test_text)
            loaded_pred = loaded.predict_proba(test_text)
            assert abs(original_pred - loaded_pred) < 1e-6
        finally:
            os.unlink(path)

    def test_model_id_computed_on_save(self):
        model = self._trained_model()

        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
            path = f.name

        try:
            model_id = model.save(path)
            assert model_id is not None
            assert len(model_id) == 16  # SHA-256 hex, first 16 chars
            assert model.model_id == model_id
        finally:
            os.unlink(path)

    def test_model_id_computed_on_load(self):
        model = self._trained_model()

        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
            path = f.name

        try:
            save_id = model.save(path)

            from utils.ml_thesis_model import MLThesisModel
            loaded = MLThesisModel()
            load_id = loaded.load(path)

            # Same file → same model_id
            assert save_id == load_id
            assert loaded.model_id == save_id
        finally:
            os.unlink(path)

    def test_load_missing_file_raises(self):
        from utils.ml_thesis_model import MLThesisModel
        model = MLThesisModel()
        with pytest.raises(FileNotFoundError):
            model.load("/nonexistent/path.joblib")

    def test_save_without_training_raises(self):
        from utils.ml_thesis_model import MLThesisModel
        model = MLThesisModel()
        with pytest.raises(RuntimeError, match="No model to save"):
            model.save("/tmp/test.joblib")


class TestMLThesisModelVersion:
    """Test model versioning."""

    def test_version_attribute_exists(self):
        from utils.ml_thesis_model import MLThesisModel
        assert hasattr(MLThesisModel, "__version__")
        assert MLThesisModel.__version__ == "2026.02.v1"

    def test_is_loaded_property(self):
        from utils.ml_thesis_model import MLThesisModel
        model = MLThesisModel()
        assert not model.is_loaded

        texts = ["meal kit delivery service"] * 15 + ["enterprise saas platform"] * 15
        labels = [1] * 15 + [0] * 15
        model.train(texts, labels)
        assert model.is_loaded


class TestMLThesisModelFeatureImportances:
    """Test feature importance extraction."""

    def test_returns_dict(self):
        from utils.ml_thesis_model import MLThesisModel
        model = MLThesisModel()
        texts = ["meal kit delivery"] * 15 + ["enterprise saas"] * 15
        labels = [1] * 15 + [0] * 15
        model.train(texts, labels)

        importances = model.get_feature_importances(top_n=5)
        assert isinstance(importances, dict)
        assert len(importances) <= 5

    def test_empty_model_returns_empty(self):
        from utils.ml_thesis_model import MLThesisModel
        model = MLThesisModel()
        assert model.get_feature_importances() == {}
