"""
SOURCE_TYPE constants for SourceAssetStore integration.

Each collector should use these constants when saving assets to ensure
consistent naming across the system. These types are used for:
- Asset storage and retrieval
- Change detection
- Entity resolution

Usage:
    from collectors.source_types import SOURCE_TYPE

    asset = SourceAsset(
        source_type=SOURCE_TYPE.GITHUB_REPO,
        external_id="owner/repo",
        ...
    )
"""


class SOURCE_TYPE:
    """Standard source type identifiers for the asset store."""

    # Code repositories
    GITHUB_REPO = "github_repo"
    GITHUB_ORG = "github_org"
    GITHUB_USER = "github_user"

    # Official registries
    SEC_EDGAR_FILING = "sec_edgar_filing"
    COMPANIES_HOUSE = "companies_house"
    OPENCORPORATES = "opencorporates"
    USPTO_PATENT = "uspto_patent"

    # Product launches
    PRODUCT_HUNT = "product_hunt"
    HACKER_NEWS = "hacker_news"

    # Domain & web
    DOMAIN_WHOIS = "domain_whois"
    WEBSITE_SNAPSHOT = "website_snapshot"

    # Jobs
    GREENHOUSE_JOB = "greenhouse_job"
    LEVER_JOB = "lever_job"
    LINKEDIN_JOB = "linkedin_job"
    LINKEDIN_COMPANY = "linkedin_company"

    # Funding databases
    CRUNCHBASE_ORG = "crunchbase_org"
    CRUNCHBASE_FUNDING = "crunchbase_funding"
    PITCHBOOK_COMPANY = "pitchbook_company"

    # Academic
    ARXIV_PAPER = "arxiv_paper"


# Map collector names to their primary source types
COLLECTOR_SOURCE_TYPES = {
    "github": SOURCE_TYPE.GITHUB_REPO,
    "github_activity": SOURCE_TYPE.GITHUB_USER,
    "sec_edgar": SOURCE_TYPE.SEC_EDGAR_FILING,
    "companies_house": SOURCE_TYPE.COMPANIES_HOUSE,
    "opencorporates": SOURCE_TYPE.OPENCORPORATES,
    "product_hunt": SOURCE_TYPE.PRODUCT_HUNT,
    "hacker_news": SOURCE_TYPE.HACKER_NEWS,
    "domain_whois": SOURCE_TYPE.DOMAIN_WHOIS,
    "job_postings": SOURCE_TYPE.GREENHOUSE_JOB,  # or LEVER_JOB based on source
    "linkedin": SOURCE_TYPE.LINKEDIN_COMPANY,
    "crunchbase": SOURCE_TYPE.CRUNCHBASE_ORG,
    "arxiv": SOURCE_TYPE.ARXIV_PAPER,
    "uspto": SOURCE_TYPE.USPTO_PATENT,
}
