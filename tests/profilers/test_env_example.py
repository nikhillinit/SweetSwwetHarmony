"""
Tests to verify .env.example contains required configuration

Ensures that privacy flags and other critical settings are documented
for users setting up the project.
"""

import pytest
from pathlib import Path


class TestEnvExample:
    """Verify .env.example has required privacy and configuration entries"""

    def test_env_example_exists(self):
        """Should have .env.example file in project root"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        assert env_example.exists(), ".env.example file not found in project root"

    def test_env_example_has_allow_cloud_llm(self):
        """Should document ALLOW_CLOUD_LLM privacy flag"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        content = env_example.read_text()

        assert "ALLOW_CLOUD_LLM" in content, "Missing ALLOW_CLOUD_LLM in .env.example"
        assert "false" in content, "ALLOW_CLOUD_LLM should default to false"

    def test_env_example_has_allow_cloud_vision(self):
        """Should document ALLOW_CLOUD_VISION privacy flag"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        content = env_example.read_text()

        assert "ALLOW_CLOUD_VISION" in content, "Missing ALLOW_CLOUD_VISION in .env.example"
        assert "false" in content, "ALLOW_CLOUD_VISION should default to false"

    def test_env_example_has_privacy_warnings(self):
        """Should include warnings about NDA/confidential data"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        content = env_example.read_text()

        # Should warn about NDA or confidential data
        assert any(keyword in content.upper() for keyword in ["NDA", "CONFIDENTIAL", "SENSITIVE", "WARNING"]), \
            "Missing privacy warnings in .env.example"

    def test_env_example_documents_defaults(self):
        """Privacy flags should explicitly state default=false"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        content = env_example.read_text()

        # Find the privacy section
        privacy_section = ""
        in_privacy_section = False
        for line in content.split("\n"):
            if "PDF PROFILER PRIVACY" in line.upper() or "NDA PROTECTION" in line.upper():
                in_privacy_section = True
            elif "====" in line and in_privacy_section and len(privacy_section) > 50:
                break  # End of section
            if in_privacy_section:
                privacy_section += line + "\n"

        # Should mention "default" or "Default"
        assert "default" in privacy_section.lower(), \
            "Privacy section should document default values"

    def test_env_example_has_google_api_key(self):
        """Should document GOOGLE_API_KEY for Gemini"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        content = env_example.read_text()

        assert "GOOGLE_API_KEY" in content, "Missing GOOGLE_API_KEY in .env.example"

    def test_env_example_has_discovery_db_path(self):
        """Should document DISCOVERY_DB_PATH for SQLite storage"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        content = env_example.read_text()

        assert "DISCOVERY_DB_PATH" in content, "Missing DISCOVERY_DB_PATH in .env.example"

    def test_env_example_organized_into_sections(self):
        """Should have clear section headers with separators"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        content = env_example.read_text()

        # Count section separators (=======)
        separator_count = content.count("=" * 10)

        # Should have multiple sections (at least 5)
        assert separator_count >= 5, f"Expected multiple sections, found {separator_count}"

    def test_env_example_no_real_secrets(self):
        """Should not contain real API keys or secrets"""
        env_example = Path(__file__).parent.parent.parent / ".env.example"
        content = env_example.read_text()

        # Should use placeholders, not real secrets
        suspicious_patterns = ["sk-", "pk_live_", "xoxb-"]
        for pattern in suspicious_patterns:
            assert pattern not in content, f"Found potential real secret pattern: {pattern}"
