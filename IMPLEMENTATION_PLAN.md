# Discovery Engine Implementation Plan
## Tailored for Press On Ventures' Notion CRM

**Date:** January 5, 2026  
**Database:** Venture Pipeline (677 deals)  
**Team:** William Litvack, Maui Dancel, Nikhil Bhambi

---

## Executive Summary

This plan integrates a founder discovery engine with your existing Notion "Venture Pipeline" database. The system will:

1. **Detect** emerging founders via GitHub, Companies House, domain registrations
2. **Score** prospects against your thesis (Healthcare, AI/ML, CPG, etc.)
3. **Push** qualified leads directly into Notion as new "Lead" status deals
4. **Suppress** deals you've already passed on or are actively working

**Cost:** $0 additional (uses your existing infrastructure + free tools)  
**Timeline:** 8 weeks to MVP

---

## Part 1: Notion Schema Changes

### Properties to Add (5 total)

Add these properties to your Venture Pipeline database:

| Property | Type | Purpose | How to Add |
|----------|------|---------|------------|
| **Discovery ID** | Text | Stable link to Discovery system | Add → Text |
| **Confidence Score** | Number | Thesis fit (0.0-1.0) | Add → Number |
| **Signal Types** | Multi-select | What triggered discovery | Add → Multi-select |
| **Why Now** | Text | 1-sentence summary | Add → Text |
| **Source** | Select | Deal source channel | Add → Select (if not exists) |

### Signal Types Options (for Multi-select)

Add these options to the "Signal Types" property:

- `github_spike` — New repos / commit activity
- `incorporation` — Companies House / SoS filing
- `domain_registration` — New domain claimed
- `patent_filing` — USPTO / Espacenet filing
- `funding_announced` — Press / Crunchbase
- `founder_movement` — LinkedIn title change
- `product_hunt` — PH launch detected

### Source Options (for Select)

Add these options if "Source" property doesn't exist:

- `Discovery Engine`
- `Referral`
- `Inbound`
- `Conference`
- `Cold Outreach`
- `Network`

### Status Values for Suppression

Based on your existing statuses, here's the suppression logic:

| Status | Suppress? | Reasoning |
|--------|-----------|-----------|
| Passed | ✅ Yes | Don't resurface rejected deals |
| Diligence | ✅ Yes | Already actively evaluating |
| Initial Meeting / ... | ✅ Yes | Already engaged |
| Committed | ✅ Yes | Deal in progress |
| Funded | ✅ Yes | Portfolio company |
| Tracking | ❓ Optional | Configure based on your workflow |
| Lead | ❌ No | New leads can be re-ranked |

---

## Part 2: Technical Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     YOUR EXISTING INFRASTRUCTURE                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐ │
│   │ PostgreSQL  │    │   Notion    │    │    n8n (new)            │ │
│   │ (Updog DB)  │    │ Venture     │    │ Workflow Automation     │ │
│   │             │    │ Pipeline    │◄───│ - Poll Notion           │ │
│   │             │    │ (677 deals) │    │ - Trigger cache refresh │ │
│   └─────────────┘    └──────▲──────┘    └─────────────────────────┘ │
│                             │                                        │
│                             │ Push qualified prospects               │
│                             │                                        │
│   ┌─────────────────────────┴───────────────────────────────────┐   │
│   │              DISCOVERY ENGINE (New)                          │   │
│   │                                                              │   │
│   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │   │
│   │  │ Signal       │  │ Weaviate     │  │ Ranking      │       │   │
│   │  │ Collectors   │  │ (Vector DB)  │  │ Engine       │       │   │
│   │  │              │  │              │  │              │       │   │
│   │  │ - GitHub     │  │ - 50K vectors│  │ - Two-pass   │       │   │
│   │  │ - Companies  │  │ - Hybrid     │  │ - Thesis fit │       │   │
│   │  │   House      │  │   search     │  │ - Suppress   │       │   │
│   │  │ - Domains    │  │ - Pre-filter │  │   passed     │       │   │
│   │  └──────────────┘  └──────────────┘  └──────────────┘       │   │
│   │                                                              │   │
│   │  ┌──────────────────────────────────────────────────────┐   │   │
│   │  │ PostgreSQL (discovery_db)                             │   │   │
│   │  │ - companies (stubs + verified)                        │   │   │
│   │  │ - signals (raw detections)                            │   │   │
│   │  │ - founders (watchlist)                                │   │   │
│   │  │ - notion_sync_log (audit)                             │   │   │
│   │  └──────────────────────────────────────────────────────┘   │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Part 3: Sprint Plan (8 Weeks)

### Sprint 1: Foundation (Week 1-2)

**Goal:** Set up Discovery database + Notion connector

#### Story 1.1: Notion Schema Setup
- [ ] Add "Discovery ID" property (Text)
- [ ] Add "Confidence Score" property (Number)
- [ ] Add "Signal Types" property (Multi-select with options above)
- [ ] Add "Why Now" property (Text)
- [ ] Add/verify "Source" property with "Discovery Engine" option
- [ ] Test: Create a test deal manually with all new properties

#### Story 1.2: Discovery Database Schema
```sql
-- Run in your existing PostgreSQL instance
CREATE DATABASE discovery_db;

\c discovery_db

-- Companies table (stubs + verified)
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    website TEXT,  -- Primary dedup key
    description TEXT,
    sector TEXT,
    stage TEXT,
    location TEXT,
    is_stub BOOLEAN DEFAULT FALSE,
    notion_page_id TEXT,  -- Link to Notion
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_companies_website ON companies(website) WHERE website IS NOT NULL;

-- Founders table (watchlist)
CREATE TABLE founders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    company_id UUID REFERENCES companies(id),
    linkedin_url TEXT,
    github_handle TEXT,
    email TEXT,
    previous_companies TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_founders_github ON founders(github_handle) WHERE github_handle IS NOT NULL;

-- Signals table (raw detections) with Glass.AI provenance tracking
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    founder_id UUID REFERENCES founders(id),
    signal_type TEXT NOT NULL,  -- github_spike, incorporation, etc.
    confidence DECIMAL(3,2) DEFAULT 1.0,
    
    -- PROVENANCE (Glass.AI principle: full audit trail)
    source_api TEXT NOT NULL,           -- 'github', 'companies_house', 'whois'
    source_url TEXT,                    -- Exact API endpoint called
    source_response_hash TEXT,          -- SHA256 of raw response (for audit)
    retrieved_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- VERIFICATION STATUS (MiroThinker principle: verification loops)
    verified_by_sources TEXT[],         -- ['companies_house', 'domain_whois']
    verification_status TEXT DEFAULT 'unverified',  
    -- Values: 'unverified', 'single_source', 'multi_source', 'conflicting'
    
    raw_data JSONB,
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ  -- For decay
);

CREATE INDEX idx_signals_company ON signals(company_id);
CREATE INDEX idx_signals_type ON signals(signal_type);
CREATE INDEX idx_signals_verification ON signals(verification_status);

-- Company confidence breakdown (Glass.AI: show your work)
-- Add this column to companies table:
-- ALTER TABLE companies ADD COLUMN confidence_breakdown JSONB DEFAULT '{}';
-- Shape: {
--   "overall": 0.85,
--   "sources_agreeing": 2,
--   "sources_checked": 3,
--   "signal_details": [{"type": "github_spike", "source": "github", "weight": 0.20}]
-- }

-- Provenance audit log (for LP defensibility)
CREATE TABLE provenance_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    event_type TEXT NOT NULL,  -- 'signal_detected', 'verification_passed', 'pushed_to_notion'
    event_data JSONB,
    source_documents TEXT[],   -- URLs to original evidence
    created_by TEXT,           -- 'system' or user
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_provenance_company ON provenance_audit(company_id);
CREATE INDEX idx_provenance_type ON provenance_audit(event_type);

-- Notion sync audit log
CREATE TABLE notion_sync_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    notion_page_id TEXT,
    action TEXT NOT NULL,  -- created, updated, skipped
    payload JSONB,
    synced_at TIMESTAMPTZ DEFAULT NOW()
);

-- Suppression cache (materialized from Notion)
CREATE TABLE notion_suppression_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notion_page_id TEXT NOT NULL,
    website TEXT,
    discovery_id TEXT,
    status TEXT NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_suppression_website ON notion_suppression_cache(website);
CREATE INDEX idx_suppression_discovery ON notion_suppression_cache(discovery_id);
```

#### Story 1.3: NotionConnector Implementation
- [ ] Deploy `notion_connector.py` (provided above)
- [ ] Set environment variables:
  ```bash
  export NOTION_API_KEY="secret_xxx"  # Get from Notion integrations
  export NOTION_DATABASE_ID="xxx"     # From your database URL
  ```
- [ ] Test connection: `python -m discovery_engine.connectors.notion_connector`
- [ ] Verify suppression list loads correctly

**Acceptance Criteria:**
- [ ] Can query Notion database via API
- [ ] Suppression cache populates with Passed/Diligence deals
- [ ] Can create a test deal in Notion via API

---

### Sprint 2: Signal Collection (Week 3-4)

**Goal:** Collect founder signals from GitHub and Companies House

#### Story 2.1: GitHub Collector
```python
# discovery_engine/collectors/github_collector.py
"""
Detects founder activity signals:
- New repositories created
- Commit velocity spikes
- Profile bio changes
"""

import httpx
from datetime import datetime, timedelta

class GitHubCollector:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"token {token}"}
    
    async def check_founder(self, github_handle: str) -> list[dict]:
        """Check a founder's GitHub for signals"""
        signals = []
        
        async with httpx.AsyncClient() as client:
            # Get recent repos
            repos = await self._get_recent_repos(client, github_handle)
            
            # Check for new private-turned-public repos
            for repo in repos:
                if self._is_startup_signal(repo):
                    signals.append({
                        "type": "github_spike",
                        "confidence": 0.7,
                        "data": {
                            "repo": repo["full_name"],
                            "created_at": repo["created_at"],
                            "description": repo["description"]
                        }
                    })
        
        return signals
    
    async def _get_recent_repos(self, client, handle: str) -> list:
        response = await client.get(
            f"https://api.github.com/users/{handle}/repos",
            headers=self.headers,
            params={"sort": "created", "per_page": 10}
        )
        response.raise_for_status()
        return response.json()
    
    def _is_startup_signal(self, repo: dict) -> bool:
        """Heuristics for startup-related repo"""
        created = datetime.fromisoformat(repo["created_at"].replace("Z", "+00:00"))
        is_recent = created > datetime.now(created.tzinfo) - timedelta(days=90)
        
        # Check for startup indicators
        startup_keywords = ["api", "app", "platform", "sdk", "client"]
        has_keyword = any(kw in (repo.get("name", "") + repo.get("description", "")).lower() 
                         for kw in startup_keywords)
        
        return is_recent and has_keyword
```

#### Story 2.2: Companies House Collector
```python
# discovery_engine/collectors/companies_house_collector.py
"""
Detects new UK company incorporations
Free API: https://developer-specs.company-information.service.gov.uk/
"""

import httpx
from datetime import datetime, timedelta

class CompaniesHouseCollector:
    BASE_URL = "https://api.company-information.service.gov.uk"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    async def search_by_founder(self, founder_name: str) -> list[dict]:
        """Search for companies where founder is director"""
        signals = []
        
        async with httpx.AsyncClient() as client:
            # Search officers
            officers = await self._search_officers(client, founder_name)
            
            for officer in officers:
                company = await self._get_company(client, officer["company_number"])
                
                if self._is_recent_incorporation(company):
                    signals.append({
                        "type": "incorporation",
                        "confidence": 0.95,  # High confidence
                        "data": {
                            "company_name": company["company_name"],
                            "company_number": company["company_number"],
                            "incorporation_date": company.get("date_of_creation"),
                            "sic_codes": company.get("sic_codes", [])
                        }
                    })
        
        return signals
    
    async def _search_officers(self, client, name: str) -> list:
        response = await client.get(
            f"{self.BASE_URL}/search/officers",
            auth=(self.api_key, ""),
            params={"q": name, "items_per_page": 20}
        )
        response.raise_for_status()
        return response.json().get("items", [])
    
    async def _get_company(self, client, company_number: str) -> dict:
        response = await client.get(
            f"{self.BASE_URL}/company/{company_number}",
            auth=(self.api_key, "")
        )
        response.raise_for_status()
        return response.json()
    
    def _is_recent_incorporation(self, company: dict) -> bool:
        """Check if incorporated within last 12 months"""
        date_str = company.get("date_of_creation")
        if not date_str:
            return False
        
        created = datetime.strptime(date_str, "%Y-%m-%d")
        return created > datetime.now() - timedelta(days=365)
```

#### Story 2.3: Cron Job Setup
```bash
# /etc/cron.d/discovery-engine

# Run GitHub collector daily at 2 AM
0 2 * * * ubuntu cd /home/ubuntu/discovery_engine && python -m collectors.run_github >> /var/log/discovery/github.log 2>&1

# Run Companies House collector daily at 3 AM
0 3 * * * ubuntu cd /home/ubuntu/discovery_engine && python -m collectors.run_companies_house >> /var/log/discovery/ch.log 2>&1

# Refresh Notion suppression cache every 15 minutes
*/15 * * * * ubuntu cd /home/ubuntu/discovery_engine && python -m connectors.refresh_notion_cache >> /var/log/discovery/notion.log 2>&1
```

**Acceptance Criteria:**
- [ ] GitHub collector detects repos from test founder
- [ ] Companies House collector finds recent incorporations
- [ ] Signals stored in `signals` table
- [ ] Cron jobs running without errors

---

### Sprint 3: Weaviate + Ranking (Week 5-6)

**Goal:** Vector search + thesis-based ranking

#### Story 3.1: Weaviate Setup
```bash
# Download and run Weaviate binary
wget https://github.com/weaviate/weaviate/releases/download/v1.23.7/weaviate-v1.23.7-linux-amd64.tar.gz
tar -xzf weaviate-v1.23.7-linux-amd64.tar.gz

# Create data directory
mkdir -p /home/ubuntu/weaviate_data

# Run Weaviate
./weaviate --host 0.0.0.0 --port 8080 --scheme http \
    --data-path /home/ubuntu/weaviate_data &
```

#### Story 3.2: Schema + Indexing
```python
# discovery_engine/search/weaviate_setup.py

import weaviate

client = weaviate.Client("http://localhost:8080")

# Create schema
schema = {
    "class": "Company",
    "properties": [
        {"name": "name", "dataType": ["text"]},
        {"name": "description", "dataType": ["text"]},
        {"name": "sector", "dataType": ["text"], "indexFilterable": True},
        {"name": "stage", "dataType": ["text"], "indexFilterable": True},
        {"name": "location", "dataType": ["text"], "indexFilterable": True},
        {"name": "website", "dataType": ["text"]},
        # Suppression flags (synced from Notion)
        {"name": "is_passed", "dataType": ["boolean"], "indexFilterable": True},
        {"name": "in_pipeline", "dataType": ["boolean"], "indexFilterable": True},
        # Links
        {"name": "postgres_id", "dataType": ["text"]},
        {"name": "notion_page_id", "dataType": ["text"]},
    ],
    "vectorizer": "none",  # We'll provide our own vectors
}

client.schema.create_class(schema)
```

#### Story 3.3: Two-Pass Ranking
```python
# discovery_engine/ranking/two_pass_ranker.py

async def rank_candidates(
    candidates: list[dict],
    thesis: str,
    suppression_list: set,
    llm_client
) -> list[dict]:
    """
    Two-pass ranking to optimize LLM costs.
    
    Pass 1: Cheap scoring (all candidates)
    Pass 2: Expensive summarization (top 15 only)
    """
    
    # Filter out suppressed deals FIRST
    active_candidates = [
        c for c in candidates
        if f"website:{c.get('website', '')}" not in suppression_list
        and f"discovery:{c.get('discovery_id', '')}" not in suppression_list
    ]
    
    # PASS 1: Quick scoring with cheaper model
    scoring_prompt = f"""
    Rate each company 0-100 for fit with this investment thesis:
    {thesis}
    
    Companies:
    {format_candidates_brief(active_candidates)}
    
    Return JSON: {{"scores": [{{"id": "...", "score": N}}]}}
    """
    
    scores = await llm_client.complete(
        model="gpt-4o-mini",  # Cheaper model
        prompt=scoring_prompt,
        max_tokens=500
    )
    
    # Sort and take top 15
    sorted_candidates = sorted(
        active_candidates,
        key=lambda c: get_score(scores, c["id"]),
        reverse=True
    )[:15]
    
    # PASS 2: Deep summarization with better model
    results = []
    for candidate in sorted_candidates:
        summary_prompt = f"""
        Analyze this company for a {thesis} focused VC:
        
        {format_candidate_full(candidate)}
        
        Provide:
        1. One-sentence "Why Now" summary
        2. Key signals (bullet points)
        3. Confidence score (0.0-1.0)
        """
        
        analysis = await llm_client.complete(
            model="claude-3-5-sonnet",  # Better model for summaries
            prompt=summary_prompt,
            max_tokens=300
        )
        
        results.append({
            **candidate,
            "why_now": analysis["why_now"],
            "confidence_score": analysis["confidence"],
            "signal_types": analysis["signals"]
        })
    
    return results
```

**Acceptance Criteria:**
- [ ] Weaviate running and accessible
- [ ] 1000+ companies indexed with vectors
- [ ] Hybrid search returns relevant results
- [ ] Two-pass ranking produces scored output

---

### Sprint 3.5: Verification Gate (Week 6)

**Goal:** Implement Glass.AI provenance and verification loops

#### Story 3.5.1: Add "Needs Research" Status to Notion
- [ ] Add new Status option: `Needs Research`
- [ ] Create filtered view for "Needs Research" deals
- [ ] Assign to rotation for human review

#### Story 3.5.2: Verification Gate Implementation
- [ ] Deploy `verification_gate.py` (provided)
- [ ] Configure thresholds:
  - `HIGH_CONFIDENCE_THRESHOLD = 0.7` (auto-push to "Lead")
  - `MEDIUM_CONFIDENCE_THRESHOLD = 0.4` (push to "Needs Research")
- [ ] Add `confidence_breakdown` JSONB column to companies table

#### Story 3.5.3: Cross-Source Verification Loops
- [ ] GitHub signal → verify with Companies House
- [ ] Incorporation signal → verify with GitHub activity
- [ ] Domain signal → verify with DNS lookup
- [ ] Target: 70%+ of pushed deals have 2+ source verification

#### Story 3.5.4: Provenance Audit Log
- [ ] Create `provenance_audit` table (schema above)
- [ ] Log every signal detection with `source_url` and `source_response_hash`
- [ ] Log every Notion push with full `confidence_breakdown`

**Acceptance Criteria:**
- [ ] Single-source leads go to "Needs Research" queue
- [ ] Multi-source leads auto-push to "Lead" status
- [ ] Full audit trail for any deal (for LP defensibility)

---

### Sprint 4: Integration + Launch (Week 7-8)

**Goal:** Connect everything, deploy, test with team

#### Story 4.1: n8n Deployment
```bash
# Install n8n globally
npm install -g n8n

# Add to pm2
pm2 start n8n --name n8n

# Access at http://localhost:5678
```

#### Story 4.2: n8n Workflow - Notion Sync
Create workflow in n8n UI:

1. **Schedule Trigger** → Every 15 minutes
2. **Notion: Query Database** → Filter by "Last edited time" in past 20 min
3. **IF** → Status changed to "Passed"
4. **HTTP Request** → POST to `http://localhost:8000/api/cache/invalidate`

#### Story 4.3: Push to Notion Flow
```python
# discovery_engine/flows/push_to_notion.py

async def push_ranked_results_to_notion(
    results: list[dict],
    notion_connector: NotionConnector
):
    """Push qualified prospects to Notion"""
    
    created = 0
    updated = 0
    skipped = 0
    
    for result in results:
        # Build payload
        payload = ProspectPayload(
            discovery_id=result["id"],
            company_name=result["name"],
            website=result["website"],
            stage=map_stage(result["stage"]),
            confidence_score=result["confidence_score"],
            signal_types=result["signal_types"],
            why_now=result["why_now"],
            short_description=result.get("description", ""),
            sector=map_sector(result.get("sector")),
            location=result.get("location", "")
        )
        
        # Push to Notion
        response = await notion_connector.upsert_prospect(payload)
        
        if response["status"] == "created":
            created += 1
        elif response["status"] == "updated":
            updated += 1
        else:
            skipped += 1
        
        # Log for audit
        await log_sync(result["id"], response)
    
    return {"created": created, "updated": updated, "skipped": skipped}
```

#### Story 4.4: End-to-End Test
```python
# tests/test_e2e.py

async def test_full_pipeline():
    """
    1. Add a test founder to watchlist
    2. Mock GitHub signal
    3. Run ranking
    4. Verify deal created in Notion
    5. Mark as "Passed" in Notion
    6. Re-run ranking
    7. Verify deal NOT re-surfaced
    """
    
    # Setup
    notion = NotionConnector(API_KEY, DATABASE_ID)
    
    # Create test company
    test_prospect = ProspectPayload(
        discovery_id="test-123",
        company_name="E2E Test Company",
        website="https://e2e-test-delete-me.com",
        stage=InvestmentStage.SEED,
        confidence_score=0.85,
        signal_types=["github_spike", "incorporation"],
        why_now="Test: Founder left Anthropic, incorporated last month"
    )
    
    # Push to Notion
    result = await notion.upsert_prospect(test_prospect)
    assert result["status"] == "created"
    page_id = result["page_id"]
    
    # Verify in suppression list (should NOT be there yet - status is Lead)
    suppression = await notion.get_suppression_list(force_refresh=True)
    assert f"website:e2e-test-delete-me.com" not in suppression
    
    # Manually mark as Passed in Notion (simulate user action)
    # ... (do this manually in Notion UI for test)
    
    # Refresh and verify suppression
    await asyncio.sleep(5)  # Wait for Notion to update
    suppression = await notion.get_suppression_list(force_refresh=True)
    assert f"website:e2e-test-delete-me.com" in suppression
    
    # Try to push again - should be skipped
    result2 = await notion.upsert_prospect(test_prospect)
    assert result2["status"] == "skipped"
    
    print("✅ E2E test passed!")
    
    # Cleanup: Delete test page from Notion manually
```

**Acceptance Criteria:**
- [ ] n8n running and syncing
- [ ] Full pipeline works end-to-end
- [ ] Passed deals don't resurface
- [ ] Team can run searches and see results in Notion

---

## Part 4: Environment Setup

### Required API Keys

| Service | How to Get | Cost |
|---------|-----------|------|
| **Notion** | Settings → Integrations → New | Free |
| **GitHub** | Settings → Developer → Personal access tokens | Free |
| **Companies House** | Register at developer.company-information.service.gov.uk | Free |
| **OpenAI/Anthropic** | For LLM ranking (optional initially) | Pay per use |

### Environment Variables

```bash
# /home/ubuntu/discovery_engine/.env

# Notion
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx  # From database URL

# GitHub
GITHUB_TOKEN=ghp_xxx

# Companies House
COMPANIES_HOUSE_API_KEY=xxx

# LLM (optional - can use local models initially)
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx

# Database
DATABASE_URL=postgresql://localhost/discovery_db

# Weaviate
WEAVIATE_URL=http://localhost:8080
```

### Notion Integration Setup

1. Go to https://www.notion.so/my-integrations
2. Click "New integration"
3. Name: "Discovery Engine"
4. Select your workspace
5. Capabilities: Read content, Update content, Insert content
6. Copy the "Internal Integration Token"
7. Go to your Venture Pipeline database
8. Click "..." → "Add connections" → Select "Discovery Engine"

---

## Part 5: Risk Mitigation

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| **Duplicate deals** | Medium | Website normalization + Discovery ID |
| **Rate limits (Notion)** | Low | 0.35s delay between requests |
| **Stale suppression** | Medium | 15-min cache TTL + n8n polling |
| **Schema drift** | Low | Property names in constants |
| **LLM costs spike** | Medium | Two-pass ranking + caching |

---

## Part 6: Success Metrics

### Week 4 Checkpoint
- [ ] 500+ founders in watchlist
- [ ] 10+ signals detected per day
- [ ] Notion connector working

### Week 8 Launch
- [ ] Team using for 5+ searches/week
- [ ] At least 1 deal sourced via Discovery
- [ ] Zero duplicate deals in Notion
- [ ] Suppression working correctly

### Post-Launch (Month 2+)
- [ ] Track: Searches → Intro calls → Investments
- [ ] Measure: Time saved vs. manual sourcing
- [ ] Iterate: Adjust signal weights based on outcomes

---

## Appendix: File Structure

```
discovery_engine/
├── __init__.py
├── config.py                 # Environment + settings
├── models/
│   ├── __init__.py
│   ├── company.py           # SQLAlchemy models
│   ├── signal.py
│   └── founder.py
├── connectors/
│   ├── __init__.py
│   ├── notion_connector.py  # ← Provided above
│   └── weaviate_connector.py
├── collectors/
│   ├── __init__.py
│   ├── github_collector.py
│   ├── companies_house_collector.py
│   └── run_all.py
├── ranking/
│   ├── __init__.py
│   ├── two_pass_ranker.py
│   └── thesis_scorer.py
├── flows/
│   ├── __init__.py
│   └── push_to_notion.py
├── api/
│   ├── __init__.py
│   └── routes.py            # FastAPI endpoints
└── tests/
    ├── __init__.py
    ├── test_notion.py
    └── test_e2e.py
```

---

## Next Steps (Action Items)

### This Week
1. [ ] Add 5 new properties to Notion (15 min)
2. [ ] Create Notion integration and get API key (10 min)
3. [ ] Run SQL schema in PostgreSQL (5 min)
4. [ ] Test NotionConnector with your database (30 min)

### Next Week
5. [ ] Set up GitHub collector with 10 test founders
6. [ ] Register for Companies House API
7. [ ] Deploy Weaviate binary

### Week 3+
8. [ ] Build ranking pipeline
9. [ ] Deploy n8n
10. [ ] Full team testing

---

**Questions?** This plan is designed to be executed incrementally. Start with Notion schema changes and connector testing — those are zero-risk and take 30 minutes.
