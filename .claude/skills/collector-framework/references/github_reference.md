# GitHub Collector Reference

**Signal Type:** `github_spike`  
**API Base:** https://api.github.com  
**Auth:** GITHUB_TOKEN (optional, but rate limits)  
**Rate Limit:** 5,000/hr authenticated, 60/hr unauthenticated  

## API Endpoints

```
# Search repos
GET /search/repositories?q=stars:>100+pushed:>2024-01-01+topic:ai

# Repo detail
GET /repos/{owner}/{repo}

# Owner detail
GET /users/{login} OR /orgs/{login}
```

## Topic Classification

**TECH mode (default):** ai, ml, llm, developer-tools  
**CONSUMER mode:** food, fitness, wellness, ecommerce

## Confidence Formula

```python
base = 0.5
if recent_stars > 100: base += 0.2
if growth_rate > 0.5: base += 0.15
if is_org_owned: base += 0.1
if has_website: base += 0.05
```

## Canonical Keys

1. `domain:example.com` (if website)
2. `github_org:owner` (if organization)
3. `github_repo:owner/repo`
4. `name_loc:name|region`

## Common Issues

- Org vs User lookup → Try /orgs first, fallback to /users
- Rate limit → Use authenticated token or wait for reset
- Spike detection → Estimate from age and push frequency
