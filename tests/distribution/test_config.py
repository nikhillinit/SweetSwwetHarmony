"""
Tests for distribution/config.py

Validates configuration loading and validation logic.
"""

import os
import pytest
from unittest.mock import patch

from distribution.config import (
    DistributionConfig,
    ConfigurationError,
    load_config,
    validate_config_for_production,
)


class TestDistributionConfig:
    """Tests for DistributionConfig dataclass."""

    def test_valid_config_creation(self):
        """Config with all required fields should be valid."""
        config = DistributionConfig(
            public_api_base_url="https://api.example.com",
            digest_from_email="deals@example.com",
            digest_to_emails=["gp@example.com"],
        )

        assert config.public_api_base_url == "https://api.example.com"
        assert config.digest_from_email == "deals@example.com"
        assert config.digest_to_emails == ["gp@example.com"]
        assert config.email_transport == "console"  # default

    def test_profile_url_defaults_to_api_url(self):
        """PUBLIC_PROFILE_BASE_URL should default to PUBLIC_API_BASE_URL."""
        config = DistributionConfig(
            public_api_base_url="https://api.example.com",
            digest_from_email="deals@example.com",
            digest_to_emails=["gp@example.com"],
        )

        assert config.public_profile_base_url == "https://api.example.com"

    def test_trailing_slashes_stripped(self):
        """URLs should have trailing slashes removed."""
        config = DistributionConfig(
            public_api_base_url="https://api.example.com/",
            public_profile_base_url="https://profile.example.com/",
            digest_from_email="deals@example.com",
            digest_to_emails=["gp@example.com"],
        )

        assert config.public_api_base_url == "https://api.example.com"
        assert config.public_profile_base_url == "https://profile.example.com"

    def test_resend_requires_api_key(self):
        """Resend transport without API key should raise error."""
        with pytest.raises(ConfigurationError, match="RESEND_API_KEY is required"):
            DistributionConfig(
                public_api_base_url="https://api.example.com",
                digest_from_email="deals@example.com",
                digest_to_emails=["gp@example.com"],
                email_transport="resend",
                resend_api_key=None,
            )

    def test_smtp_requires_host(self):
        """SMTP transport without host should raise error."""
        with pytest.raises(ConfigurationError, match="SMTP_HOST is required"):
            DistributionConfig(
                public_api_base_url="https://api.example.com",
                digest_from_email="deals@example.com",
                digest_to_emails=["gp@example.com"],
                email_transport="smtp",
                smtp_host=None,
            )

    def test_invalid_from_email_rejected(self):
        """Invalid from email should raise error."""
        with pytest.raises(ConfigurationError, match="Invalid DIGEST_FROM_EMAIL"):
            DistributionConfig(
                public_api_base_url="https://api.example.com",
                digest_from_email="invalid-email",
                digest_to_emails=["gp@example.com"],
            )

    def test_invalid_to_email_rejected(self):
        """Invalid to email should raise error."""
        with pytest.raises(ConfigurationError, match="Invalid email in DIGEST_TO_EMAILS"):
            DistributionConfig(
                public_api_base_url="https://api.example.com",
                digest_from_email="deals@example.com",
                digest_to_emails=["invalid-email"],
            )

    def test_is_production_flag(self):
        """is_production should be True for non-console transport."""
        console_config = DistributionConfig(
            public_api_base_url="https://api.example.com",
            digest_from_email="deals@example.com",
            digest_to_emails=["gp@example.com"],
            email_transport="console",
        )
        assert console_config.is_production is False

        resend_config = DistributionConfig(
            public_api_base_url="https://api.example.com",
            digest_from_email="deals@example.com",
            digest_to_emails=["gp@example.com"],
            email_transport="resend",
            resend_api_key="re_xxx",
        )
        assert resend_config.is_production is True


class TestLoadConfig:
    """Tests for load_config function."""

    def test_dev_mode_defaults(self):
        """In dev mode (console), sensible defaults should be used."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        assert config.email_transport == "console"
        assert config.public_api_base_url == "http://localhost:8000"
        assert config.digest_from_email == "dev@localhost"

    def test_env_vars_loaded(self):
        """Environment variables should be loaded correctly."""
        env = {
            "PUBLIC_API_BASE_URL": "https://api.prod.com",
            "PUBLIC_PROFILE_BASE_URL": "https://profile.prod.com",
            "DIGEST_FROM_EMAIL": "deals@prod.com",
            "DIGEST_TO_EMAILS": "gp1@prod.com,gp2@prod.com",
            "EMAIL_TRANSPORT": "resend",
            "RESEND_API_KEY": "re_test_key",
        }

        with patch.dict(os.environ, env, clear=True):
            config = load_config()

        assert config.public_api_base_url == "https://api.prod.com"
        assert config.public_profile_base_url == "https://profile.prod.com"
        assert config.digest_from_email == "deals@prod.com"
        assert config.digest_to_emails == ["gp1@prod.com", "gp2@prod.com"]
        assert config.email_transport == "resend"
        assert config.resend_api_key == "re_test_key"


class TestValidateConfigForProduction:
    """Tests for validate_config_for_production function."""

    def test_localhost_warnings(self):
        """Localhost URLs should generate warnings."""
        config = DistributionConfig(
            public_api_base_url="http://localhost:8000",
            digest_from_email="deals@example.com",
            digest_to_emails=["gp@example.com"],
        )

        warnings = validate_config_for_production(config)

        assert any("localhost" in w for w in warnings)

    def test_console_transport_warning(self):
        """Console transport should generate warning."""
        config = DistributionConfig(
            public_api_base_url="https://api.example.com",
            digest_from_email="deals@example.com",
            digest_to_emails=["gp@example.com"],
            email_transport="console",
        )

        warnings = validate_config_for_production(config)

        assert any("console" in w for w in warnings)

    def test_production_config_no_warnings(self):
        """Valid production config should have no warnings."""
        config = DistributionConfig(
            public_api_base_url="https://api.example.com",
            digest_from_email="deals@example.com",
            digest_to_emails=["gp@example.com"],
            email_transport="resend",
            resend_api_key="re_xxx",
        )

        warnings = validate_config_for_production(config)

        assert len(warnings) == 0
