# SEC EDGAR Collector Reference

**Signal Type:** `funding_event`  
**API Base:** https://www.sec.gov  
**Auth:** None (User-Agent required)  
**Rate Limit:** 0.15s between requests (~6 req/sec)  

## API Endpoints

```
# Form D RSS feed
GET /cgi-bin/browse-edgar?action=getcurrent&type=D&output=atom

# Detail filing
GET /Archives/edgar/data/{cik}/{accession}/primary_doc.xml
```

## Consumer SIC Codes

- **CPG:** 2000-2099 (food/beverage), 2800-2899 (cosmetics)
- **Health Tech:** 8000-8099 (health services), 7900-7999 (fitness)
- **Travel:** 5800-5899 (restaurants), 7000-7099 (hotels)
- **Marketplaces:** 5900-5999 (retail), 7300-7399 (services)

## Confidence Formula

```python
base = 0.7  # Form D is authoritative
if is_target_sector: base += 0.15
if offering_amount >= 500_000: base += 0.1
if age_days > 60: base -= 0.05
if age_days > 120: base -= 0.1
```

## Canonical Keys

Primary: `domain:example.com` (if website)  
Fallback: `sec_edgar_{cik}`

## Common Issues

- 404 on detail XML → Skip enrichment, use Atom data
- Missing SIC code → Check `industryGroupType` field
- Website not in Form D → Requires external enrichment
