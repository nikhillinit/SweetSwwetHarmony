"""
Distribution Configuration

Loads and validates environment variables for the distribution layer.
Provides type-safe configuration with sensible defaults.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


@dataclass
class DistributionConfig:
    """
    Configuration for the distribution layer.

    Required fields must be set for production use.
    Optional fields have sensible defaults for development.
    """

    # Required for production
    public_api_base_url: str
    digest_from_email: str
    digest_to_emails: List[str]

    # Optional with defaults
    email_transport: str = "console"  # console | resend | smtp
    public_profile_base_url: Optional[str] = None  # defaults to public_api_base_url
    resend_api_key: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None

    # Digest settings
    max_companies_per_digest: int = 25
    digest_lookback_days: int = 7
    token_expiry_days: int = 7

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Default profile URL to API URL if not set
        if self.public_profile_base_url is None:
            self.public_profile_base_url = self.public_api_base_url

        # Validate transport-specific requirements
        if self.email_transport == "resend" and not self.resend_api_key:
            raise ConfigurationError(
                "RESEND_API_KEY is required when EMAIL_TRANSPORT=resend"
            )

        if self.email_transport == "smtp":
            if not self.smtp_host:
                raise ConfigurationError(
                    "SMTP_HOST is required when EMAIL_TRANSPORT=smtp"
                )

        # Validate email format (basic check)
        if "@" not in self.digest_from_email:
            raise ConfigurationError(
                f"Invalid DIGEST_FROM_EMAIL: {self.digest_from_email}"
            )

        for email in self.digest_to_emails:
            if "@" not in email:
                raise ConfigurationError(
                    f"Invalid email in DIGEST_TO_EMAILS: {email}"
                )

        # Strip trailing slashes from URLs
        self.public_api_base_url = self.public_api_base_url.rstrip("/")
        self.public_profile_base_url = self.public_profile_base_url.rstrip("/")

    @property
    def is_production(self) -> bool:
        """Check if running in production mode (non-console transport)."""
        return self.email_transport != "console"


def load_config(
    require_production: bool = False,
    env_prefix: str = "",
) -> DistributionConfig:
    """
    Load configuration from environment variables.

    Args:
        require_production: If True, raises error if production vars missing
        env_prefix: Optional prefix for env var names (e.g., "DIST_")

    Returns:
        DistributionConfig instance

    Raises:
        ConfigurationError: If required configuration is missing
    """

    def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
        """Get env var with optional prefix."""
        full_name = f"{env_prefix}{name}" if env_prefix else name
        return os.getenv(full_name, default)

    def get_env_required(name: str) -> str:
        """Get required env var, raise if missing."""
        value = get_env(name)
        if value is None:
            full_name = f"{env_prefix}{name}" if env_prefix else name
            raise ConfigurationError(f"Required environment variable {full_name} is not set")
        return value

    def get_env_list(name: str, default: Optional[List[str]] = None) -> List[str]:
        """Get comma-separated list from env var."""
        value = get_env(name)
        if value is None:
            return default or []
        return [email.strip() for email in value.split(",") if email.strip()]

    def get_env_int(name: str, default: int) -> int:
        """Get integer from env var."""
        value = get_env(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            raise ConfigurationError(f"{name} must be an integer, got: {value}")

    # Check if we're in development mode (relaxed requirements)
    email_transport = get_env("EMAIL_TRANSPORT", "console")
    is_dev_mode = email_transport == "console" and not require_production

    # In dev mode, provide sensible defaults
    if is_dev_mode:
        public_api_base_url = get_env("PUBLIC_API_BASE_URL", "http://localhost:8000")
        digest_from_email = get_env("DIGEST_FROM_EMAIL", "dev@localhost")
        digest_to_emails = get_env_list("DIGEST_TO_EMAILS", ["dev@localhost"])
    else:
        # Production mode - require all fields
        public_api_base_url = get_env_required("PUBLIC_API_BASE_URL")
        digest_from_email = get_env_required("DIGEST_FROM_EMAIL")
        digest_to_emails = get_env_list("DIGEST_TO_EMAILS")
        if not digest_to_emails:
            raise ConfigurationError("DIGEST_TO_EMAILS is required and cannot be empty")

    return DistributionConfig(
        public_api_base_url=public_api_base_url,
        digest_from_email=digest_from_email,
        digest_to_emails=digest_to_emails,
        email_transport=email_transport,
        public_profile_base_url=get_env("PUBLIC_PROFILE_BASE_URL"),
        resend_api_key=get_env("RESEND_API_KEY"),
        smtp_host=get_env("SMTP_HOST"),
        smtp_port=get_env_int("SMTP_PORT", 587),
        smtp_user=get_env("SMTP_USER"),
        smtp_password=get_env("SMTP_PASSWORD"),
        max_companies_per_digest=get_env_int("MAX_COMPANIES_PER_DIGEST", 25),
        digest_lookback_days=get_env_int("DIGEST_LOOKBACK_DAYS", 7),
        token_expiry_days=get_env_int("TOKEN_EXPIRY_DAYS", 7),
    )


def validate_config_for_production(config: DistributionConfig) -> List[str]:
    """
    Validate configuration is ready for production.

    Returns:
        List of warning/error messages (empty if valid)
    """
    issues = []

    if config.email_transport == "console":
        issues.append("EMAIL_TRANSPORT is 'console' - emails will only print to stdout")

    if "localhost" in config.public_api_base_url:
        issues.append(f"PUBLIC_API_BASE_URL contains 'localhost': {config.public_api_base_url}")

    if "localhost" in config.public_profile_base_url:
        issues.append(f"PUBLIC_PROFILE_BASE_URL contains 'localhost': {config.public_profile_base_url}")

    if config.digest_from_email.endswith("@localhost"):
        issues.append(f"DIGEST_FROM_EMAIL is localhost: {config.digest_from_email}")

    return issues


# Quick test when run directly
if __name__ == "__main__":
    try:
        config = load_config()
        print(f"Configuration loaded successfully:")
        print(f"  Transport: {config.email_transport}")
        print(f"  API URL: {config.public_api_base_url}")
        print(f"  Profile URL: {config.public_profile_base_url}")
        print(f"  From: {config.digest_from_email}")
        print(f"  To: {config.digest_to_emails}")
        print(f"  Production mode: {config.is_production}")

        warnings = validate_config_for_production(config)
        if warnings:
            print("\nProduction warnings:")
            for w in warnings:
                print(f"  - {w}")
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
