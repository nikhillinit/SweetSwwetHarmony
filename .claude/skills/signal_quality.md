# Signal Quality Evaluation

## Purpose

This skill defines how to evaluate the quality of signals collected by the Discovery Engine. Apply these heuristics when assessing whether a signal is actionable.

## Signal Quality Tiers

### HIGH Quality (0.70-1.00)
- From authoritative source (SEC, Companies House, official registries)
- Recent (within signal-specific freshness window)
- Contains rich metadata (amounts, dates, identifiers)
- Corroborated by other signals
- Strong canonical key available (domain, registration number)

### MEDIUM Quality (0.40-0.69)
- From reliable but non-authoritative source (GitHub, WHOIS)
- Moderately recent (within 2x freshness window)
- Basic metadata present
- Single source, awaiting corroboration
- Has canonical key (may be github_org or name_loc)

### LOW Quality (0.00-0.39)
- From unverified source (social media, press release)
- Stale (beyond 2x freshness window)
- Missing key metadata
- No corroborating signals
- Only name_loc canonical key (unstable)

## Signal Freshness & Decay

Each signal type has an optimal freshness window and decay curve:

| Signal Type | Peak Window | Half-Life | Decay to Zero |
|-------------|-------------|-----------|---------------|
| incorporation | 0-90 days | 365 days | 3 years |
| github_spike | 0-14 days | 14 days | 60 days |
| funding_event | 0-30 days | 180 days | 2 years |
| domain_registration | 0-60 days | 60 days | 180 days |
| patent_filing | 0-90 days | 180 days | 2 years |
| product_hunt | 0-7 days | 7 days | 30 days |
| social_announcement | 0-3 days | 3 days | 14 days |

### Decay Formula
```
decay_factor = 0.5 ^ (days_since_signal / half_life)
decayed_weight = base_weight * decay_factor
```

## Source Reliability Rankings

### Tier 1 - Authoritative (1.0x multiplier)
- SEC EDGAR (Form D, S-1)
- UK Companies House
- USPTO (patents)
- State incorporation records

### Tier 2 - Reliable (0.85x multiplier)
- GitHub API (stars, commits)
- Domain WHOIS
- Crunchbase (verified)
- LinkedIn (verified profiles)

### Tier 3 - Informational (0.70x multiplier)
- Twitter/X announcements
- Product Hunt launches
- Press releases
- Blog posts

### Tier 4 - Unverified (0.50x multiplier)
- Anonymous tips
- Forum mentions
- Unverified social media
- Scraped data without provenance

## Red Flags

### Hard Kill Signals (Score → 0, Reject)
- `company_dissolved` - Company no longer exists
- `fraud_investigation` - Active legal issues
- `founder_blacklisted` - Known bad actor
- `age_over_10_years` - Not early-stage
- `competitor_portfolio` - Conflict of interest

### Warning Flags (-0.15 each)
- Missing website after 6 months post-incorporation
- GitHub repo with stars but no commits in 90 days
- Funding announced but no SEC Form D filed
- Domain registered but parked/for sale
- Founder LinkedIn shows "Looking for opportunities"

### Context-Dependent Flags (investigate further)
- Very high star velocity (could be bought stars)
- Multiple pivots in short time
- High founder turnover
- Unusual incorporation jurisdiction
- Offering amount mismatch (Form D vs press)

## Signal Combination Patterns

### Strong Patterns (boost confidence)
| Pattern | Signals | Interpretation |
|---------|---------|----------------|
| "Committed Founder" | incorporation + domain + github_active | Serious venture |
| "Funded & Building" | sec_form_d + github_spike | Capital + execution |
| "Market Entry" | domain + product_hunt + social_buzz | Go-to-market |

### Weak Patterns (reduce confidence)
| Pattern | Signals | Interpretation |
|---------|---------|----------------|
| "Tire Kicker" | domain_only | May be speculative |
| "Abandoned" | old_incorporation + no_activity | Likely dead |
| "Hype Only" | social_buzz + no_product | Vaporware risk |

## Quality Scoring Formula

```
quality_score = (
    base_weight
    * source_reliability_multiplier
    * decay_factor
    * (1 - sum(warning_flags) * 0.15)
    * multi_source_boost
)

multi_source_boost:
  3+ sources: 1.30x
  2 sources: 1.15x
  1 source: 1.00x
```

## Output Template

When assessing signal quality, provide:

```
Signal: [type] from [source]
Age: [days] (within/beyond freshness window)
Quality Tier: [HIGH/MEDIUM/LOW]
Reliability: Tier [1-4] ([multiplier]x)
Decay Factor: [0.00-1.00]
Flags: [list any warnings]
Corroboration: [other signals if any]
Quality Score: [0.00-1.00]
Recommendation: [Use/Investigate/Discard]
```

## Examples

### High Quality Signal
```
Signal: funding_event from sec_edgar
Age: 12 days (within 30-day peak)
Quality Tier: HIGH
Reliability: Tier 1 (1.0x)
Decay Factor: 0.95
Flags: None
Corroboration: github_spike (same company)
Quality Score: 0.87
Recommendation: Use - authoritative source, recent, corroborated
```

### Low Quality Signal
```
Signal: social_announcement from twitter
Age: 45 days (beyond 3-day peak)
Quality Tier: LOW
Reliability: Tier 3 (0.70x)
Decay Factor: 0.02
Flags: No corroborating signals
Corroboration: None
Quality Score: 0.12
Recommendation: Discard - stale, unverified, no corroboration
```
