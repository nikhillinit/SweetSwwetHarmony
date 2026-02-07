"""
ML Thesis Model - Supervised classifier for thesis fit rescue.

Simple TF-IDF + LogisticRegression binary classifier that rescues
false negatives missed by the keyword matcher.

Includes model versioning (model_id) mirroring the v2 policy_hash
pattern for audit trail and A/B analysis.

Usage:
    from utils.ml_thesis_model import MLThesisModel

    # Training
    model = MLThesisModel()
    metrics = model.train(texts, labels)
    model.save("models/thesis_classifier.joblib")

    # Inference
    model = MLThesisModel()
    model.load("models/thesis_classifier.joblib")
    prob = model.predict_proba("meal kit delivery startup")
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class MLModelMetrics:
    """Evaluation metrics from model training."""
    precision: float
    recall: float
    f1: float
    accuracy: float
    cv_f1_mean: Optional[float] = None
    cv_f1_std: Optional[float] = None
    train_size: int = 0
    test_size: int = 0
    positive_count: int = 0
    negative_count: int = 0


class MLThesisModel:
    """Binary TF-IDF + LogisticRegression classifier for thesis fit.

    Deliberately simple: one pipeline, one purpose. No multi-class,
    no decision_function fallbacks, no ensemble complexity.

    Model versioning: Each loaded/trained model gets a model_id
    (SHA-256 of file content, first 16 chars) mirroring the v2
    policy_hash pattern for tracking which model produced which
    predictions.
    """

    # Class-level version for schema tracking
    __version__ = "2026.02.v1"

    def __init__(self):
        self._pipeline = None
        self._model_id: Optional[str] = None
        self._model_path: Optional[str] = None
        self._trained_at: Optional[str] = None

    @property
    def model_id(self) -> Optional[str]:
        """Model version identifier (SHA-256 hash, first 16 chars).

        Mirrors the v2 policy_hash pattern. Computed from:
        - File content hash on load()
        - Pipeline state hash on train()
        """
        return self._model_id

    @property
    def is_loaded(self) -> bool:
        """Check if a model is loaded and ready for inference."""
        return self._pipeline is not None

    def train(
        self,
        texts: List[str],
        labels: List[int],
        *,
        structured_features: Optional[List[Dict[str, float]]] = None,
        random_state: int = 42,
    ) -> MLModelMetrics:
        """Train the classifier on labeled data.

        Args:
            texts: List of text inputs (from build_ml_text)
            labels: Binary labels (1=positive/thesis_fit, 0=negative)
            structured_features: Optional structured features from ThesisMatcher
                (intent_count, domain_match, negative_count, keyword_score).
                If provided, used alongside TF-IDF in a FeatureUnion.
            random_state: Random seed for reproducibility

        Returns:
            MLModelMetrics with train/test performance

        Raises:
            ValueError: If texts and labels have different lengths
        """
        if len(texts) != len(labels):
            raise ValueError(
                f"texts ({len(texts)}) and labels ({len(labels)}) must have same length"
            )

        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
        from sklearn.metrics import precision_score, recall_score, f1_score, accuracy_score
        from sklearn.pipeline import Pipeline

        if structured_features is not None:
            pipeline = self._build_feature_union_pipeline(random_state)
        else:
            pipeline = Pipeline([
                ("tfidf", TfidfVectorizer(
                    max_features=5000,
                    ngram_range=(1, 2),
                    min_df=2,
                    sublinear_tf=True,
                )),
                ("clf", LogisticRegression(
                    class_weight="balanced",
                    C=1.0,
                    max_iter=1000,
                    random_state=random_state,
                )),
            ])

        # Stratified split
        X_train, X_test, y_train, y_test = train_test_split(
            texts, labels,
            test_size=0.2,
            random_state=random_state,
            stratify=labels,
        )

        # Train
        pipeline.fit(X_train, y_train)

        # Evaluate on holdout
        y_pred = pipeline.predict(X_test)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        f1 = f1_score(y_test, y_pred, zero_division=0)
        accuracy = accuracy_score(y_test, y_pred)

        # Cross-validation
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=random_state)
        cv_scores = cross_val_score(pipeline, texts, labels, cv=cv, scoring="f1")

        # Retrain on full dataset for production model
        pipeline.fit(texts, labels)
        self._pipeline = pipeline
        self._compute_model_id_from_pipeline()

        from datetime import datetime, timezone
        self._trained_at = datetime.now(timezone.utc).isoformat()

        return MLModelMetrics(
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1=round(f1, 4),
            accuracy=round(accuracy, 4),
            cv_f1_mean=round(float(cv_scores.mean()), 4),
            cv_f1_std=round(float(cv_scores.std()), 4),
            train_size=len(X_train),
            test_size=len(X_test),
            positive_count=sum(labels),
            negative_count=len(labels) - sum(labels),
        )

    def _build_feature_union_pipeline(self, random_state: int):
        """Build pipeline with FeatureUnion (TF-IDF + structured features).

        Reserved for Phase 2 when structured features from ThesisMatcher
        (intent counts, domain patterns, negative keyword counts) are
        included alongside text features.
        """
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline

        # Phase 1: text-only pipeline (FeatureUnion deferred to Phase 2)
        return Pipeline([
            ("tfidf", TfidfVectorizer(
                max_features=5000,
                ngram_range=(1, 2),
                min_df=2,
                sublinear_tf=True,
            )),
            ("clf", LogisticRegression(
                class_weight="balanced",
                C=1.0,
                max_iter=1000,
                random_state=random_state,
            )),
        ])

    def predict_proba(self, text: str) -> float:
        """Predict probability of positive class (thesis fit).

        Args:
            text: Text input (from build_ml_text)

        Returns:
            Probability of positive class (0.0-1.0).
            Returns 0.0 for empty text.

        Raises:
            RuntimeError: If no model is loaded
        """
        if self._pipeline is None:
            raise RuntimeError("No model loaded. Call train() or load() first.")

        if not text or not text.strip():
            return 0.0

        proba = self._pipeline.predict_proba([text])[0]
        # positive class is index 1
        positive_idx = list(self._pipeline.classes_).index(1)
        return float(proba[positive_idx])

    def predict_proba_timed(self, text: str) -> Tuple[float, float]:
        """Predict with latency measurement.

        Args:
            text: Text input (from build_ml_text)

        Returns:
            Tuple of (probability, latency_ms)
        """
        start = time.monotonic()
        prob = self.predict_proba(text)
        latency_ms = (time.monotonic() - start) * 1000
        return prob, latency_ms

    def save(self, path: str) -> str:
        """Save trained model to disk.

        Args:
            path: File path for joblib serialization

        Returns:
            model_id of saved model

        Raises:
            RuntimeError: If no model is trained
        """
        if self._pipeline is None:
            raise RuntimeError("No model to save. Call train() first.")

        import joblib

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        joblib.dump({
            "pipeline": self._pipeline,
            "version": self.__version__,
            "trained_at": self._trained_at,
        }, path)

        # Compute file-based model_id
        self._model_path = path
        self._model_id = self._compute_file_hash(path)

        logger.info("Model saved to %s (model_id=%s)", path, self._model_id)
        return self._model_id

    def load(self, path: str) -> str:
        """Load trained model from disk.

        Validates that the loaded object is a valid sklearn pipeline
        with predict_proba support, then computes model_id from file hash.

        Args:
            path: File path to joblib model

        Returns:
            model_id of loaded model

        Raises:
            FileNotFoundError: If model file doesn't exist
            RuntimeError: If loaded object is invalid
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"Model file not found: {path}")

        import joblib
        data = joblib.load(path)

        # Support both wrapped format and raw pipeline
        if isinstance(data, dict) and "pipeline" in data:
            pipeline = data["pipeline"]
            self._trained_at = data.get("trained_at")
        else:
            pipeline = data
            self._trained_at = None

        # Validate loaded object
        if not hasattr(pipeline, "predict_proba"):
            raise RuntimeError(
                f"Loaded object from {path} has no predict_proba method. "
                "Expected sklearn Pipeline."
            )

        # Sanity check: predict on dummy text
        try:
            result = pipeline.predict_proba(["test"])
            if not (0.0 <= result[0][0] <= 1.0):
                raise RuntimeError("Sanity check failed: predict_proba returned invalid value")
        except Exception as e:
            raise RuntimeError(f"Model sanity check failed: {e}") from e

        self._pipeline = pipeline
        self._model_path = path
        self._model_id = self._compute_file_hash(path)

        logger.info("Model loaded from %s (model_id=%s)", path, self._model_id)
        return self._model_id

    def get_feature_importances(self, top_n: int = 20) -> Dict[str, float]:
        """Get top feature importances from the logistic regression.

        Args:
            top_n: Number of top features to return

        Returns:
            Dict mapping feature names to importance weights
        """
        if self._pipeline is None:
            return {}

        try:
            tfidf = self._pipeline.named_steps.get("tfidf")
            clf = self._pipeline.named_steps.get("clf")
            if tfidf is None or clf is None:
                return {}

            feature_names = tfidf.get_feature_names_out()
            importances = clf.coef_[0]

            # Sort by absolute importance
            indices = sorted(
                range(len(importances)),
                key=lambda i: abs(importances[i]),
                reverse=True,
            )[:top_n]

            return {
                feature_names[i]: round(float(importances[i]), 4)
                for i in indices
            }
        except Exception as e:
            logger.warning("Failed to extract feature importances: %s", e)
            return {}

    @staticmethod
    def _compute_file_hash(path: str) -> str:
        """Compute SHA-256 hash of file content (first 16 chars).

        Mirrors the v2 _compute_policy_hash pattern.
        """
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()[:16]

    def _compute_model_id_from_pipeline(self) -> None:
        """Compute model_id from pipeline state (for freshly trained models)."""
        import pickle
        sha = hashlib.sha256()
        sha.update(pickle.dumps(self._pipeline.get_params()))
        sha.update(self.__version__.encode())
        self._model_id = sha.hexdigest()[:16]
