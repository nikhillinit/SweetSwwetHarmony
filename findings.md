# Findings: Skills & Visualization Implementation

## Key Discoveries

### 1. Anthropic Skills Best Practices
- YAML frontmatter MUST include `name` (kebab-case) and `description` (what + when)
- Skills should be under 5,000 words (progressive disclosure)
- Three categories: Document Creation, Workflow Automation, MCP Enhancement

### 2. Current Codebase
- 8 existing skills are domain knowledge (not executable workflows)
- Pipeline has comprehensive stats (`PipelineStats`, `CollectorMetrics`)
- 15+ CLI commands available
- Streamlit dashboard exists but lacks pipeline metrics

### 3. Collector Patterns  
**Universal 5-Step Workflow:**
1. INITIALIZE - API client, auth, rate limiting
2. FETCH - Rate-limited HTTP, pagination
3. ENRICH - Extract, normalize, classify
4. CONVERT - Transform to Signal objects
5. PERSIST - Dedupe via canonical keys

### 4. Visualization Architecture
**Three-Tier Hybrid:**
- Tier 1: Terminal progress (rich library)
- Tier 2: HTML reports (plotly)
- Tier 3: Dashboard trends (altair)

### 5. Windows Compatibility
- Approved: rich, plotly, altair
- Avoid: curses (Unix-only)
