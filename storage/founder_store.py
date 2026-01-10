"""
Founder Profile Storage Layer for Discovery Engine

Provides persistent SQLite storage for founder profiles with:
- Background and experience tracking
- Founder scoring based on patterns
- Cross-linking to signals and companies
- Historical tracking of founder activity

Tables:
  - founders: Core founder profile data
  - founder_experiences: Work history and education
  - founder_signals: Links founders to signals

Usage:
    store = FounderStore("signals.db")
    await store.initialize()

    # Save a founder profile
    founder_id = await store.save_founder(FounderProfile(...))

    # Get aggregate founder score for a company
    score = await store.get_aggregate_founder_score("domain:acme.ai")
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncIterator, Set
from enum import Enum

import aiosqlite

logger = logging.getLogger(__name__)


# =============================================================================
# SCHEMA VERSION
# =============================================================================

FOUNDER_SCHEMA_VERSION = 1

FOUNDER_MIGRATIONS = {
    1: """
    -- Founders table: core founder profiles
    CREATE TABLE IF NOT EXISTS founders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        -- Identity
        canonical_key TEXT NOT NULL,  -- Company canonical key this founder is linked to
        founder_key TEXT NOT NULL UNIQUE,  -- Unique founder identifier (linkedin:xxx, github:xxx, email:xxx)
        name TEXT NOT NULL,
        email TEXT,
        linkedin_url TEXT,
        github_username TEXT,
        twitter_handle TEXT,

        -- Background
        current_title TEXT,
        current_company TEXT,
        bio TEXT,
        location TEXT,

        -- Scoring factors
        is_serial_founder BOOLEAN DEFAULT 0,
        is_technical BOOLEAN DEFAULT 0,
        has_faang_experience BOOLEAN DEFAULT 0,
        has_startup_experience BOOLEAN DEFAULT 0,
        has_domain_expertise BOOLEAN DEFAULT 0,
        previous_exits INTEGER DEFAULT 0,
        years_experience INTEGER DEFAULT 0,

        -- Calculated score (cached)
        founder_score REAL DEFAULT 0.0,
        score_calculated_at TEXT,

        -- Metadata
        raw_data TEXT,  -- JSON for full profile data
        source_api TEXT NOT NULL,  -- linkedin, github, crunchbase, etc.
        first_seen_at TEXT NOT NULL,
        last_updated_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_founders_canonical_key ON founders(canonical_key);
    CREATE INDEX IF NOT EXISTS idx_founders_founder_key ON founders(founder_key);
    CREATE INDEX IF NOT EXISTS idx_founders_linkedin_url ON founders(linkedin_url);
    CREATE INDEX IF NOT EXISTS idx_founders_github_username ON founders(github_username);
    CREATE INDEX IF NOT EXISTS idx_founders_founder_score ON founders(founder_score DESC);

    -- Founder experiences: work history and education
    CREATE TABLE IF NOT EXISTS founder_experiences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        founder_id INTEGER NOT NULL,

        -- Experience details
        experience_type TEXT NOT NULL,  -- 'work', 'education', 'advisory', 'investment'
        organization TEXT NOT NULL,
        title TEXT,
        description TEXT,
        location TEXT,

        -- Timing
        start_date TEXT,
        end_date TEXT,  -- NULL = current
        is_current BOOLEAN DEFAULT 0,

        -- Flags
        is_faang BOOLEAN DEFAULT 0,
        is_founder_role BOOLEAN DEFAULT 0,
        is_leadership_role BOOLEAN DEFAULT 0,
        is_technical_role BOOLEAN DEFAULT 0,
        is_consumer_domain BOOLEAN DEFAULT 0,

        -- Metadata
        raw_data TEXT,
        created_at TEXT NOT NULL,

        FOREIGN KEY (founder_id) REFERENCES founders(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_experiences_founder_id ON founder_experiences(founder_id);
    CREATE INDEX IF NOT EXISTS idx_experiences_org ON founder_experiences(organization);
    CREATE INDEX IF NOT EXISTS idx_experiences_type ON founder_experiences(experience_type);

    -- Founder-signal links: connect founders to signals
    CREATE TABLE IF NOT EXISTS founder_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        founder_id INTEGER NOT NULL,
        signal_id INTEGER NOT NULL,
        relationship TEXT DEFAULT 'founder',  -- 'founder', 'cofounder', 'advisor', 'investor'
        created_at TEXT NOT NULL,

        FOREIGN KEY (founder_id) REFERENCES founders(id) ON DELETE CASCADE,
        UNIQUE(founder_id, signal_id)
    );

    CREATE INDEX IF NOT EXISTS idx_founder_signals_founder_id ON founder_signals(founder_id);
    CREATE INDEX IF NOT EXISTS idx_founder_signals_signal_id ON founder_signals(signal_id);

    -- Founder schema migrations tracking
    CREATE TABLE IF NOT EXISTS founder_schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT
    );
    """
}


# =============================================================================
# ENUMS
# =============================================================================

class ExperienceType(str, Enum):
    """Type of founder experience."""
    WORK = "work"
    EDUCATION = "education"
    ADVISORY = "advisory"
    INVESTMENT = "investment"


class FounderRelationship(str, Enum):
    """Relationship between founder and company."""
    FOUNDER = "founder"
    COFOUNDER = "cofounder"
    ADVISOR = "advisor"
    INVESTOR = "investor"


# =============================================================================
# DATA CLASSES
# =============================================================================

# FAANG and equivalent companies for scoring
FAANG_COMPANIES = {
    "meta", "facebook", "google", "alphabet", "amazon", "apple", "netflix",
    "microsoft", "uber", "airbnb", "stripe", "square", "block", "coinbase",
    "palantir", "snowflake", "databricks", "openai", "anthropic", "nvidia",
    "salesforce", "oracle", "adobe", "twitter", "x corp", "linkedin",
    "doordash", "instacart", "shopify", "spotify", "slack", "dropbox",
    "zoom", "asana", "notion", "figma", "canva", "airtable"
}

# Consumer-relevant domains for domain expertise scoring
CONSUMER_DOMAINS = {
    "food", "beverage", "cpg", "consumer goods", "retail", "e-commerce",
    "health", "wellness", "fitness", "beauty", "personal care",
    "travel", "hospitality", "restaurants", "entertainment",
    "media", "gaming", "social", "marketplace", "fintech",
    "consumer tech", "mobile apps", "d2c", "direct to consumer"
}

# Technical roles for scoring
TECHNICAL_ROLES = {
    "engineer", "developer", "programmer", "architect", "cto",
    "tech lead", "data scientist", "ml engineer", "research",
    "software", "platform", "infrastructure", "devops", "sre"
}

# Leadership roles for scoring
LEADERSHIP_ROLES = {
    "ceo", "cto", "cfo", "coo", "cpo", "cmo", "chief",
    "founder", "co-founder", "cofounder", "president",
    "vp", "vice president", "director", "head of", "gm", "general manager"
}


@dataclass
class FounderExperience:
    """A single experience entry for a founder."""
    experience_type: ExperienceType
    organization: str
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_current: bool = False
    raw_data: Optional[Dict[str, Any]] = None

    # Calculated flags
    id: Optional[int] = None
    is_faang: bool = False
    is_founder_role: bool = False
    is_leadership_role: bool = False
    is_technical_role: bool = False
    is_consumer_domain: bool = False

    def __post_init__(self):
        """Calculate flags from experience data."""
        org_lower = self.organization.lower() if self.organization else ""
        title_lower = self.title.lower() if self.title else ""
        desc_lower = self.description.lower() if self.description else ""

        # Check if FAANG/top-tier company
        self.is_faang = any(company in org_lower for company in FAANG_COMPANIES)

        # Check if founder role
        self.is_founder_role = any(
            role in title_lower for role in ["founder", "co-founder", "cofounder"]
        )

        # Check if leadership role
        self.is_leadership_role = any(
            role in title_lower for role in LEADERSHIP_ROLES
        )

        # Check if technical role
        self.is_technical_role = any(
            role in title_lower for role in TECHNICAL_ROLES
        )

        # Check consumer domain
        combined = f"{org_lower} {title_lower} {desc_lower}"
        self.is_consumer_domain = any(
            domain in combined for domain in CONSUMER_DOMAINS
        )

    @property
    def duration_years(self) -> float:
        """Calculate duration of experience in years."""
        if not self.start_date:
            return 0.0
        end = self.end_date or datetime.now(timezone.utc)
        delta = end - self.start_date
        return delta.days / 365.25


@dataclass
class FounderProfile:
    """Complete founder profile with experiences."""
    # Identity
    name: str
    founder_key: str  # Unique identifier: linkedin:xxx, github:xxx, email:xxx
    canonical_key: str  # Company they're linked to
    source_api: str  # Where we got this data

    # Contact/links
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_username: Optional[str] = None
    twitter_handle: Optional[str] = None

    # Background
    current_title: Optional[str] = None
    current_company: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[str] = None

    # Experiences
    experiences: List[FounderExperience] = field(default_factory=list)

    # Scoring factors (can be set directly or calculated)
    is_serial_founder: bool = False
    is_technical: bool = False
    has_faang_experience: bool = False
    has_startup_experience: bool = False
    has_domain_expertise: bool = False
    previous_exits: int = 0
    years_experience: int = 0

    # Calculated score
    founder_score: float = 0.0
    score_calculated_at: Optional[datetime] = None

    # Metadata
    raw_data: Optional[Dict[str, Any]] = None
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # DB fields
    id: Optional[int] = None

    def calculate_score(self) -> float:
        """
        Calculate founder score based on background patterns.

        Scoring factors (max 1.0):
        - Serial founder (previous exits): +0.25 per exit (max 0.5)
        - FAANG/top-tier experience: +0.15
        - Technical background: +0.1
        - Consumer domain expertise: +0.15
        - Years of experience: +0.02 per year (max 0.2)
        - Current leadership role: +0.1

        Returns:
            Float between 0.0 and 1.0
        """
        score = 0.0

        # Process experiences to update flags
        self._analyze_experiences()

        # Serial founder bonus (big signal)
        if self.previous_exits > 0:
            score += min(0.25 * self.previous_exits, 0.5)
            self.is_serial_founder = True
        elif self.is_serial_founder:
            score += 0.25

        # FAANG/top-tier experience
        if self.has_faang_experience:
            score += 0.15

        # Technical background
        if self.is_technical:
            score += 0.1

        # Consumer domain expertise
        if self.has_domain_expertise:
            score += 0.15

        # Years of experience (diminishing returns)
        if self.years_experience > 0:
            score += min(0.02 * self.years_experience, 0.2)

        # Current leadership role
        if self.current_title:
            title_lower = self.current_title.lower()
            if any(role in title_lower for role in LEADERSHIP_ROLES):
                score += 0.1

        # Cap at 1.0
        self.founder_score = min(score, 1.0)
        self.score_calculated_at = datetime.now(timezone.utc)

        return self.founder_score

    def _analyze_experiences(self) -> None:
        """Analyze experiences to set scoring flags."""
        self.has_faang_experience = False
        self.is_technical = False
        self.has_domain_expertise = False
        self.has_startup_experience = False
        self.is_serial_founder = False
        self.years_experience = 0
        
        founder_count = 0
        total_years = 0.0

        for exp in self.experiences:
            # Update flags from experiences
            if exp.is_faang:
                self.has_faang_experience = True

            if exp.is_technical_role:
                self.is_technical = True

            if exp.is_consumer_domain:
                self.has_domain_expertise = True

            if exp.is_founder_role:
                founder_count += 1
                # Count as startup experience
                self.has_startup_experience = True

            # Sum years
            total_years += exp.duration_years

        # Serial founder = 2+ founder roles
        if founder_count >= 2:
            self.is_serial_founder = True

        self.years_experience = int(total_years)


@dataclass
class FounderSignalLink:
    """Links a founder to a signal."""
    founder_id: int
    signal_id: int
    relationship: FounderRelationship = FounderRelationship.FOUNDER
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: Optional[int] = None


# =============================================================================
# FOUNDER STORE
# =============================================================================

class FounderStore:
    """
    Async SQLite storage for founder profiles.

    Features:
    - Founder profile CRUD
    - Experience tracking
    - Score calculation and caching
    - Signal linking
    """

    def __init__(
        self,
        db_path: str | Path = "signals.db",
    ):
        """
        Initialize founder store.

        Args:
            db_path: Path to SQLite database file (shared with SignalStore)
        """
        self.db_path = Path(db_path)
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize database connection and apply migrations."""
        from storage.sqlite_pragmas import apply_sqlite_pragmas
        
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self.db_path))
        await apply_sqlite_pragmas(self._db)

        await self._apply_migrations()

        logger.info(f"FounderStore initialized: {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager for transactions."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        async with self._lock:
            try:
                await self._db.execute("BEGIN")
                yield self._db
                await self._db.commit()
            except Exception:
                await self._db.rollback()
                raise

    async def _apply_migrations(self) -> None:
        """Apply pending schema migrations."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        try:
            cursor = await self._db.execute(
                "SELECT MAX(version) FROM founder_schema_migrations"
            )
            row = await cursor.fetchone()
            current_version = row[0] if row and row[0] else 0
        except aiosqlite.OperationalError:
            current_version = 0

        for version in sorted(FOUNDER_MIGRATIONS.keys()):
            if version <= current_version:
                continue

            logger.info(f"Applying founder migration v{version}...")

            async with self.transaction() as conn:
                await conn.executescript(FOUNDER_MIGRATIONS[version])
                await conn.execute(
                    """
                    INSERT INTO founder_schema_migrations (version, applied_at, description)
                    VALUES (?, ?, ?)
                    """,
                    (
                        version,
                        datetime.now(timezone.utc).isoformat(),
                        f"Founder schema version {version}"
                    )
                )

            logger.info(f"Founder migration v{version} applied")

    # =========================================================================
    # FOUNDER OPERATIONS
    # =========================================================================

    async def save_founder(self, profile: FounderProfile) -> int:
        """
        Save or update a founder profile.

        Returns the founder ID.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Calculate score before saving
        profile.calculate_score()
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            # Check if founder exists
            cursor = await conn.execute(
                "SELECT id FROM founders WHERE founder_key = ?",
                (profile.founder_key,)
            )
            existing = await cursor.fetchone()

            if existing:
                # Update existing founder
                founder_id = existing[0]
                await conn.execute(
                    """
                    UPDATE founders SET
                        canonical_key = ?,
                        name = ?,
                        email = ?,
                        linkedin_url = ?,
                        github_username = ?,
                        twitter_handle = ?,
                        current_title = ?,
                        current_company = ?,
                        bio = ?,
                        location = ?,
                        is_serial_founder = ?,
                        is_technical = ?,
                        has_faang_experience = ?,
                        has_startup_experience = ?,
                        has_domain_expertise = ?,
                        previous_exits = ?,
                        years_experience = ?,
                        founder_score = ?,
                        score_calculated_at = ?,
                        raw_data = ?,
                        source_api = ?,
                        last_updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        profile.canonical_key,
                        profile.name,
                        profile.email,
                        profile.linkedin_url,
                        profile.github_username,
                        profile.twitter_handle,
                        profile.current_title,
                        profile.current_company,
                        profile.bio,
                        profile.location,
                        profile.is_serial_founder,
                        profile.is_technical,
                        profile.has_faang_experience,
                        profile.has_startup_experience,
                        profile.has_domain_expertise,
                        profile.previous_exits,
                        profile.years_experience,
                        profile.founder_score,
                        profile.score_calculated_at.isoformat() if profile.score_calculated_at else None,
                        json.dumps(profile.raw_data) if profile.raw_data else None,
                        profile.source_api,
                        now,
                        founder_id,
                    )
                )
                logger.debug(f"Updated founder {founder_id}: {profile.name}")
            else:
                # Insert new founder
                cursor = await conn.execute(
                    """
                    INSERT INTO founders (
                        canonical_key, founder_key, name, email, linkedin_url,
                        github_username, twitter_handle, current_title, current_company,
                        bio, location, is_serial_founder, is_technical,
                        has_faang_experience, has_startup_experience, has_domain_expertise,
                        previous_exits, years_experience, founder_score, score_calculated_at,
                        raw_data, source_api, first_seen_at, last_updated_at, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.canonical_key,
                        profile.founder_key,
                        profile.name,
                        profile.email,
                        profile.linkedin_url,
                        profile.github_username,
                        profile.twitter_handle,
                        profile.current_title,
                        profile.current_company,
                        profile.bio,
                        profile.location,
                        profile.is_serial_founder,
                        profile.is_technical,
                        profile.has_faang_experience,
                        profile.has_startup_experience,
                        profile.has_domain_expertise,
                        profile.previous_exits,
                        profile.years_experience,
                        profile.founder_score,
                        profile.score_calculated_at.isoformat() if profile.score_calculated_at else None,
                        json.dumps(profile.raw_data) if profile.raw_data else None,
                        profile.source_api,
                        profile.first_seen_at.isoformat(),
                        now,
                        now,
                    )
                )
                founder_id = cursor.lastrowid
                logger.debug(f"Saved new founder {founder_id}: {profile.name}")

            # Save experiences
            if profile.experiences:
                # Clear old experiences
                await conn.execute(
                    "DELETE FROM founder_experiences WHERE founder_id = ?",
                    (founder_id,)
                )

                # Insert new experiences
                for exp in profile.experiences:
                    await conn.execute(
                        """
                        INSERT INTO founder_experiences (
                            founder_id, experience_type, organization, title,
                            description, location, start_date, end_date, is_current,
                            is_faang, is_founder_role, is_leadership_role,
                            is_technical_role, is_consumer_domain, raw_data, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            founder_id,
                            exp.experience_type.value,
                            exp.organization,
                            exp.title,
                            exp.description,
                            exp.location,
                            exp.start_date.isoformat() if exp.start_date else None,
                            exp.end_date.isoformat() if exp.end_date else None,
                            exp.is_current,
                            exp.is_faang,
                            exp.is_founder_role,
                            exp.is_leadership_role,
                            exp.is_technical_role,
                            exp.is_consumer_domain,
                            json.dumps(exp.raw_data) if exp.raw_data else None,
                            now,
                        )
                    )

        return founder_id

    async def get_founder(self, founder_key: str) -> Optional[FounderProfile]:
        """Get a founder by their unique key."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT * FROM founders WHERE founder_key = ?
            """,
            (founder_key,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        profile = self._row_to_founder(row)

        # Load experiences
        profile.experiences = await self._get_experiences(profile.id)

        return profile

    async def get_founders_for_company(
        self,
        canonical_key: str,
    ) -> List[FounderProfile]:
        """Get all founders linked to a company."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT * FROM founders WHERE canonical_key = ?
            ORDER BY founder_score DESC
            """,
            (canonical_key,)
        )
        rows = await cursor.fetchall()

        founders = []
        for row in rows:
            profile = self._row_to_founder(row)
            profile.experiences = await self._get_experiences(profile.id)
            founders.append(profile)

        return founders

    async def get_aggregate_founder_score(
        self,
        canonical_key: str,
    ) -> float:
        """
        Get aggregate founder score for a company.

        Combines scores from all linked founders:
        - Takes best founder score
        - Adds bonus for multiple strong founders

        Returns:
            Float between 0.0 and 1.0
        """
        founders = await self.get_founders_for_company(canonical_key)

        if not founders:
            return 0.0

        # Get top founder scores
        scores = sorted([f.founder_score for f in founders], reverse=True)

        # Base is the best founder's score
        aggregate = scores[0]

        # Bonus for additional strong founders (0.7+ score)
        strong_founders = sum(1 for s in scores[1:] if s >= 0.7)
        aggregate += min(0.05 * strong_founders, 0.15)

        # Bonus for multiple founders (team signal)
        if len(founders) >= 2:
            aggregate += 0.05

        return min(aggregate, 1.0)

    async def _get_experiences(self, founder_id: int) -> List[FounderExperience]:
        """Get all experiences for a founder."""
        if not self._db:
            return []

        cursor = await self._db.execute(
            """
            SELECT * FROM founder_experiences
            WHERE founder_id = ?
            ORDER BY (start_date IS NULL), start_date DESC
            """,
            (founder_id,)
        )
        rows = await cursor.fetchall()

        return [self._row_to_experience(row) for row in rows]

    def _row_to_founder(self, row: tuple) -> FounderProfile:
        """Convert database row to FounderProfile."""
        columns = [
            "id", "canonical_key", "founder_key", "name", "email",
            "linkedin_url", "github_username", "twitter_handle",
            "current_title", "current_company", "bio", "location",
            "is_serial_founder", "is_technical", "has_faang_experience",
            "has_startup_experience", "has_domain_expertise",
            "previous_exits", "years_experience", "founder_score",
            "score_calculated_at", "raw_data", "source_api",
            "first_seen_at", "last_updated_at", "created_at"
        ]

        data = dict(zip(columns, row))

        return FounderProfile(
            id=data["id"],
            canonical_key=data["canonical_key"],
            founder_key=data["founder_key"],
            name=data["name"],
            email=data["email"],
            linkedin_url=data["linkedin_url"],
            github_username=data["github_username"],
            twitter_handle=data["twitter_handle"],
            current_title=data["current_title"],
            current_company=data["current_company"],
            bio=data["bio"],
            location=data["location"],
            is_serial_founder=bool(data["is_serial_founder"]),
            is_technical=bool(data["is_technical"]),
            has_faang_experience=bool(data["has_faang_experience"]),
            has_startup_experience=bool(data["has_startup_experience"]),
            has_domain_expertise=bool(data["has_domain_expertise"]),
            previous_exits=data["previous_exits"] or 0,
            years_experience=data["years_experience"] or 0,
            founder_score=data["founder_score"] or 0.0,
            score_calculated_at=datetime.fromisoformat(data["score_calculated_at"]) if data["score_calculated_at"] else None,
            raw_data=json.loads(data["raw_data"]) if data["raw_data"] else None,
            source_api=data["source_api"],
            first_seen_at=datetime.fromisoformat(data["first_seen_at"]),
            last_updated_at=datetime.fromisoformat(data["last_updated_at"]),
        )

    def _row_to_experience(self, row: tuple) -> FounderExperience:
        """Convert database row to FounderExperience."""
        columns = [
            "id", "founder_id", "experience_type", "organization", "title",
            "description", "location", "start_date", "end_date", "is_current",
            "is_faang", "is_founder_role", "is_leadership_role",
            "is_technical_role", "is_consumer_domain", "raw_data", "created_at"
        ]

        data = dict(zip(columns, row))

        return FounderExperience(
            id=data["id"],
            experience_type=ExperienceType(data["experience_type"]),
            organization=data["organization"],
            title=data["title"],
            description=data["description"],
            location=data["location"],
            start_date=datetime.fromisoformat(data["start_date"]) if data["start_date"] else None,
            end_date=datetime.fromisoformat(data["end_date"]) if data["end_date"] else None,
            is_current=bool(data["is_current"]),
            is_faang=bool(data["is_faang"]),
            is_founder_role=bool(data["is_founder_role"]),
            is_leadership_role=bool(data["is_leadership_role"]),
            is_technical_role=bool(data["is_technical_role"]),
            is_consumer_domain=bool(data["is_consumer_domain"]),
            raw_data=json.loads(data["raw_data"]) if data["raw_data"] else None,
        )

    # =========================================================================
    # SIGNAL LINKING
    # =========================================================================

    async def link_founder_to_signal(
        self,
        founder_id: int,
        signal_id: int,
        relationship: FounderRelationship = FounderRelationship.FOUNDER,
    ) -> None:
        """Link a founder to a signal."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO founder_signals (founder_id, signal_id, relationship, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(founder_id, signal_id) DO UPDATE SET
                    relationship = excluded.relationship
                """,
                (founder_id, signal_id, relationship.value, now)
            )

    async def get_founders_for_signal(
        self,
        signal_id: int,
    ) -> List[FounderProfile]:
        """Get all founders linked to a signal."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        cursor = await self._db.execute(
            """
            SELECT f.* FROM founders f
            INNER JOIN founder_signals fs ON f.id = fs.founder_id
            WHERE fs.signal_id = ?
            ORDER BY f.founder_score DESC
            """,
            (signal_id,)
        )
        rows = await cursor.fetchall()

        founders = []
        for row in rows:
            profile = self._row_to_founder(row)
            profile.experiences = await self._get_experiences(profile.id)
            founders.append(profile)

        return founders

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def get_stats(self) -> Dict[str, Any]:
        """Get founder store statistics."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Total founders
        cursor = await self._db.execute("SELECT COUNT(*) FROM founders")
        total_founders = (await cursor.fetchone())[0]

        # High-score founders (0.7+)
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM founders WHERE founder_score >= 0.7"
        )
        high_score_founders = (await cursor.fetchone())[0]

        # Serial founders
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM founders WHERE is_serial_founder = 1"
        )
        serial_founders = (await cursor.fetchone())[0]

        # FAANG alumni
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM founders WHERE has_faang_experience = 1"
        )
        faang_alumni = (await cursor.fetchone())[0]

        # Total experiences
        cursor = await self._db.execute("SELECT COUNT(*) FROM founder_experiences")
        total_experiences = (await cursor.fetchone())[0]

        # By source
        cursor = await self._db.execute(
            "SELECT source_api, COUNT(*) FROM founders GROUP BY source_api"
        )
        by_source = dict(await cursor.fetchall())

        return {
            "total_founders": total_founders,
            "high_score_founders": high_score_founders,
            "serial_founders": serial_founders,
            "faang_alumni": faang_alumni,
            "total_experiences": total_experiences,
            "by_source": by_source,
        }


# =============================================================================
# CONTEXT MANAGER
# =============================================================================

@asynccontextmanager
async def founder_store(
    db_path: str | Path = "signals.db",
) -> AsyncIterator[FounderStore]:
    """Context manager for FounderStore."""
    store = FounderStore(db_path)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()
