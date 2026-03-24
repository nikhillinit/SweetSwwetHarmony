"""Tests for ML enablement in RuntimeControls."""

import os
from unittest.mock import patch

import pytest

from utils.runtime_controls import RuntimeControls, VALID_ML_ENABLEMENTS


class TestMLEnablement:
    """Test ML enablement resolution."""

    def test_default_is_disabled(self, monkeypatch):
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        controls = RuntimeControls.from_env()
        assert controls.ml_enablement == "disabled"
        assert controls.ml_model_path is None

    def test_explicit_shadow(self):
        controls = RuntimeControls.from_env(ml_enablement="shadow")
        assert controls.ml_enablement == "shadow"

    def test_explicit_live(self):
        controls = RuntimeControls.from_env(ml_enablement="live")
        assert controls.ml_enablement == "live"

    def test_explicit_disabled(self):
        controls = RuntimeControls.from_env(ml_enablement="disabled")
        assert controls.ml_enablement == "disabled"

    def test_invalid_explicit_raises(self):
        with pytest.raises(ValueError, match="Invalid ml_enablement"):
            RuntimeControls.from_env(ml_enablement="invalid")

    def test_env_var_resolution(self):
        with patch.dict(os.environ, {"ML_ENABLEMENT": "shadow"}):
            controls = RuntimeControls.from_env()
            assert controls.ml_enablement == "shadow"

    def test_env_var_case_insensitive(self):
        with patch.dict(os.environ, {"ML_ENABLEMENT": "SHADOW"}):
            controls = RuntimeControls.from_env()
            assert controls.ml_enablement == "shadow"

    def test_env_var_invalid_warns_and_defaults(self):
        with patch.dict(os.environ, {"ML_ENABLEMENT": "bogus"}):
            controls = RuntimeControls.from_env()
            assert controls.ml_enablement == "disabled"

    def test_explicit_overrides_env(self):
        with patch.dict(os.environ, {"ML_ENABLEMENT": "shadow"}):
            controls = RuntimeControls.from_env(ml_enablement="live")
            assert controls.ml_enablement == "live"


class TestMLModelPath:
    """Test ML model path resolution."""

    def test_explicit_path(self):
        controls = RuntimeControls.from_env(
            ml_enablement="shadow",
            ml_model_path="/custom/model.joblib",
        )
        assert controls.ml_model_path == "/custom/model.joblib"

    def test_env_var_path(self):
        with patch.dict(os.environ, {
            "ML_ENABLEMENT": "shadow",
            "ML_MODEL_PATH": "/env/model.joblib",
        }):
            controls = RuntimeControls.from_env()
            assert controls.ml_model_path == "/env/model.joblib"

    def test_default_is_none(self):
        controls = RuntimeControls.from_env()
        assert controls.ml_model_path is None


class TestMLProperties:
    """Test ML convenience properties."""

    def test_is_ml_active(self):
        controls = RuntimeControls.from_env(ml_enablement="shadow")
        assert controls.is_ml_active is True

    def test_is_ml_active_disabled(self, monkeypatch):
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        controls = RuntimeControls.from_env()
        assert controls.is_ml_active is False

    def test_is_ml_shadow(self):
        controls = RuntimeControls.from_env(ml_enablement="shadow")
        assert controls.is_ml_shadow is True
        assert controls.is_ml_live is False

    def test_is_ml_live(self):
        controls = RuntimeControls.from_env(ml_enablement="live")
        assert controls.is_ml_live is True
        assert controls.is_ml_shadow is False


class TestMLIndependentOfV2:
    """ML enablement is independent of v2 policy enablement."""

    def test_ml_active_v2_disabled(self):
        controls = RuntimeControls.from_env(
            ml_enablement="shadow",
            v2_enablement="disabled",
        )
        assert controls.is_ml_active is True
        assert controls.v2_enablement == "disabled"

    def test_v2_active_ml_disabled(self):
        controls = RuntimeControls.from_env(
            ml_enablement="disabled",
            v2_enablement="shadow",
        )
        assert controls.is_ml_active is False
        assert controls.is_v2_active is True

    def test_both_active(self):
        controls = RuntimeControls.from_env(
            ml_enablement="shadow",
            v2_enablement="shadow",
        )
        assert controls.is_ml_active is True
        assert controls.is_v2_active is True


class TestValidMLEnablements:
    """Test VALID_ML_ENABLEMENTS constant."""

    def test_valid_values(self):
        assert VALID_ML_ENABLEMENTS == frozenset({"disabled", "shadow", "live"})

    def test_post_init_validates(self):
        with pytest.raises(ValueError, match="Invalid ml_enablement"):
            RuntimeControls(
                policy_loader_mode="permissive",
                v2_enablement="disabled",
                v2_execution_enabled=False,
                ml_enablement="invalid",
            )
