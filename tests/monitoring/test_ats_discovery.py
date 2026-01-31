"""
Tests for ATS (Applicant Tracking System) embed discovery.

Tests detection of embedded job boards from Greenhouse, Lever, Ashby, and Workable
in company career page HTML.
"""

import pytest

from monitoring.content_pipeline.ats_discovery import (
    ATSDiscoveryResult,
    ATSProvider,
    ATSSignatureDetector,
)


class TestATSSignatureDetector:
    """Tests for ATSSignatureDetector class."""

    @pytest.fixture
    def detector(self) -> ATSSignatureDetector:
        """Create detector instance."""
        return ATSSignatureDetector()

    # === Greenhouse Detection ===

    def test_detect_greenhouse_script_embed(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Greenhouse script embed."""
        html = """
        <html>
        <body>
            <div id="grnhse_app"></div>
            <script src="https://boards.greenhouse.io/embed/job_board/js?for=acmecorp"></script>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.GREENHOUSE
        assert result.board_id == "acmecorp"
        assert result.api_url == "https://boards-api.greenhouse.io/v1/boards/acmecorp/jobs"

    def test_detect_greenhouse_iframe_embed(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Greenhouse iframe embed."""
        html = """
        <html>
        <body>
            <iframe src="https://boards.greenhouse.io/embed/job_board?for=techstartup"></iframe>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.GREENHOUSE
        assert result.board_id == "techstartup"

    def test_detect_greenhouse_direct_link(self, detector: ATSSignatureDetector) -> None:
        """Test detection of direct Greenhouse board link."""
        html = """
        <html>
        <body>
            <a href="https://boards.greenhouse.io/coolcompany/jobs">View Jobs</a>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.GREENHOUSE
        assert result.board_id == "coolcompany"

    def test_detect_greenhouse_job_boards_subdomain(self, detector: ATSSignatureDetector) -> None:
        """Test detection with job-boards subdomain variant."""
        html = """
        <html>
        <body>
            <a href="https://job-boards.greenhouse.io/mycompany">Careers</a>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.GREENHOUSE
        assert result.board_id == "mycompany"

    # === Lever Detection ===

    def test_detect_lever_jobs_link(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Lever jobs link."""
        html = """
        <html>
        <body>
            <a href="https://jobs.lever.co/fintech-startup">Open Positions</a>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.LEVER
        assert result.board_id == "fintech-startup"
        assert result.api_url == "https://api.lever.co/v0/postings/fintech-startup"

    def test_detect_lever_script_embed(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Lever script embed."""
        html = """
        <html>
        <body>
            <div id="lever-jobs-container"></div>
            <script src="https://jobs.lever.co/healthtech/embed/init.js"></script>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.LEVER
        assert result.board_id == "healthtech"

    def test_detect_lever_iframe(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Lever iframe embed."""
        html = """
        <html>
        <body>
            <iframe src="https://jobs.lever.co/ecommerce-co?embed=true"></iframe>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.LEVER
        assert result.board_id == "ecommerce-co"

    # === Ashby Detection ===

    def test_detect_ashby_jobs_link(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Ashby jobs link."""
        html = """
        <html>
        <body>
            <a href="https://jobs.ashbyhq.com/ai-startup">Join Us</a>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.ASHBY
        assert result.board_id == "ai-startup"
        assert result.api_url == "https://api.ashbyhq.com/posting-api/job-board/ai-startup"

    def test_detect_ashby_script_embed(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Ashby script embed."""
        html = """
        <html>
        <body>
            <script src="https://jobs.ashbyhq.com/robotics-company/embed"></script>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.ASHBY
        assert result.board_id == "robotics-company"

    # === Workable Detection ===

    def test_detect_workable_embed(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Workable embed."""
        html = """
        <html>
        <body>
            <script src="https://www.workable.com/embed/init/food-delivery"></script>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.WORKABLE
        assert result.board_id == "food-delivery"
        assert result.api_url == "https://apply.workable.com/food-delivery"

    def test_detect_workable_apply_link(self, detector: ATSSignatureDetector) -> None:
        """Test detection of Workable apply link."""
        html = """
        <html>
        <body>
            <a href="https://apply.workable.com/fitness-app/j/ABC123/">Software Engineer</a>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.WORKABLE
        assert result.board_id == "fitness-app"

    # === No Detection Cases ===

    def test_no_ats_detected(self, detector: ATSSignatureDetector) -> None:
        """Test no detection when no ATS present."""
        html = """
        <html>
        <body>
            <h1>Careers at Our Company</h1>
            <p>We're hiring! Send your resume to jobs@company.com</p>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is None

    def test_no_detection_random_script(self, detector: ATSSignatureDetector) -> None:
        """Test no false positives from random scripts."""
        html = """
        <html>
        <body>
            <script src="https://analytics.example.com/track.js"></script>
            <script src="https://cdn.example.com/app.js"></script>
        </body>
        </html>
        """
        result = detector.detect(html)

        assert result is None

    def test_empty_html(self, detector: ATSSignatureDetector) -> None:
        """Test handling of empty HTML."""
        result = detector.detect("")

        assert result is None

    def test_malformed_html(self, detector: ATSSignatureDetector) -> None:
        """Test handling of malformed HTML."""
        html = "<html><body><script>broken"
        result = detector.detect(html)

        assert result is None

    # === Priority/Precedence Tests ===

    def test_multiple_ats_returns_first_detected(self, detector: ATSSignatureDetector) -> None:
        """Test that when multiple ATS present, first detected is returned."""
        html = """
        <html>
        <body>
            <a href="https://boards.greenhouse.io/company1">Greenhouse</a>
            <a href="https://jobs.lever.co/company2">Lever</a>
        </body>
        </html>
        """
        result = detector.detect(html)

        # Greenhouse should be detected first (higher priority)
        assert result is not None
        assert result.provider == ATSProvider.GREENHOUSE
        assert result.board_id == "company1"

    # === Edge Cases ===

    def test_board_id_with_hyphens(self, detector: ATSSignatureDetector) -> None:
        """Test board ID extraction with hyphens."""
        html = """
        <a href="https://boards.greenhouse.io/my-cool-startup-2024/jobs">Jobs</a>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.board_id == "my-cool-startup-2024"

    def test_board_id_with_numbers(self, detector: ATSSignatureDetector) -> None:
        """Test board ID extraction with numbers."""
        html = """
        <a href="https://jobs.lever.co/startup123">Jobs</a>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.board_id == "startup123"

    def test_case_insensitive_detection(self, detector: ATSSignatureDetector) -> None:
        """Test case-insensitive URL matching."""
        html = """
        <a href="HTTPS://BOARDS.GREENHOUSE.IO/UPPERCASE/jobs">Jobs</a>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.provider == ATSProvider.GREENHOUSE
        # Board ID should preserve original case
        assert result.board_id.lower() == "uppercase"

    def test_url_with_trailing_slash(self, detector: ATSSignatureDetector) -> None:
        """Test URL parsing with trailing slash."""
        html = """
        <a href="https://jobs.ashbyhq.com/company-name/">Careers</a>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.board_id == "company-name"

    def test_url_with_query_params(self, detector: ATSSignatureDetector) -> None:
        """Test URL parsing with query parameters."""
        html = """
        <a href="https://jobs.lever.co/techco?team=engineering&location=sf">Engineering Jobs</a>
        """
        result = detector.detect(html)

        assert result is not None
        assert result.board_id == "techco"


class TestATSDiscoveryResult:
    """Tests for ATSDiscoveryResult dataclass."""

    def test_result_to_dict(self) -> None:
        """Test serialization to dict."""
        result = ATSDiscoveryResult(
            provider=ATSProvider.GREENHOUSE,
            board_id="testcompany",
            api_url="https://boards-api.greenhouse.io/v1/boards/testcompany/jobs",
            embed_url="https://boards.greenhouse.io/embed/job_board/js?for=testcompany",
            confidence=0.95,
        )

        data = result.to_dict()

        assert data["provider"] == "greenhouse"
        assert data["board_id"] == "testcompany"
        assert data["api_url"] == "https://boards-api.greenhouse.io/v1/boards/testcompany/jobs"
        assert data["embed_url"] == "https://boards.greenhouse.io/embed/job_board/js?for=testcompany"
        assert data["confidence"] == 0.95

    def test_result_equality(self) -> None:
        """Test result comparison."""
        result1 = ATSDiscoveryResult(
            provider=ATSProvider.LEVER,
            board_id="company",
            api_url="https://api.lever.co/v0/postings/company",
        )
        result2 = ATSDiscoveryResult(
            provider=ATSProvider.LEVER,
            board_id="company",
            api_url="https://api.lever.co/v0/postings/company",
        )

        assert result1 == result2
