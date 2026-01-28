"""
Configuration for PDF and URL profilers.

Provides privacy controls to prevent NDA leakage via cloud services.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class PrivacyConfig:
    """
    Privacy configuration for profilers.

    Controls whether profilers can send data to cloud services.
    Both default to False to prevent NDA leakage.
    """
    allow_cloud_llm: bool = False    # Text to Gemini/OpenAI (NDA risk)
    allow_cloud_vision: bool = False  # Images to multimodal models


def load_privacy_config() -> PrivacyConfig:
    """
    Load privacy configuration from environment variables.

    Environment Variables:
        ALLOW_CLOUD_LLM: Set to "true" to enable cloud text extraction (default: false)
        ALLOW_CLOUD_VISION: Set to "true" to enable cloud vision extraction (default: false)

    Returns:
        PrivacyConfig with settings loaded from environment
    """
    def parse_bool(value: Optional[str]) -> bool:
        """Parse string to boolean, handling common formats."""
        if value is None:
            return False
        return value.lower() in ("true", "1", "yes", "on")

    return PrivacyConfig(
        allow_cloud_llm=parse_bool(os.getenv("ALLOW_CLOUD_LLM")),
        allow_cloud_vision=parse_bool(os.getenv("ALLOW_CLOUD_VISION")),
    )
