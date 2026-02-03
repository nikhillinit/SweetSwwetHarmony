# Collectors reference

Full collector inventory, signal strength heuristics, and key requirements.

## Collectors

| Collector | Source | Signal Strength | API Key |
|-----------|--------|-----------------|---------|
| `github.py` | GitHub trending repos | 0.5-0.7 | GITHUB_TOKEN |
| `github_activity.py` | Founder GitHub activity | 0.5-0.7 | GITHUB_TOKEN |
| `sec_edgar.py` | SEC Form D filings | 0.6-0.8 | None |
| `companies_house.py` | UK incorporations | 0.6-0.8 | COMPANIES_HOUSE_API_KEY |
| `domain_whois.py` | Domain registrations | 0.4-0.6 | None |
| `job_postings.py` | Greenhouse/Lever ATS | 0.7-0.95 | None |
| `product_hunt.py` | Product Hunt launches | 0.5-0.7 | PH_API_KEY |
| `hacker_news.py` | HN mentions/Show HN | 0.5-0.7 | None |
| `arxiv.py` | ArXiv research papers | 0.3-0.5 | None |
| `uspto.py` | USPTO patent filings | 0.4-0.6 | None |
| `linkedin.py` | LinkedIn company/jobs | 0.5-0.8 | PROXYCURL_API_KEY |
| `crunchbase.py` | Crunchbase funding data | 0.6-0.9 | CRUNCHBASE_API_KEY |
| `opencorporates.py` | Global incorporations | 0.6-0.75 | OPENCORPORATES_API_KEY |
| `news_api.py` | GNews consumer news | 0.4-0.75 | GNEWS_API_KEY |
| `rss_feeds.py` | TechCrunch, PR Newswire, etc. | 0.35-0.65 | None |
| `changedetection.py` | Website change monitoring | 0.5-0.85 | ABANDONED (use built-in `monitoring/`) |

Notes:
- “Signal Strength” is a heuristic; treat as guidance, not ground truth.
- For keys and setup, see `docs/claude/environment-variables.md`.
