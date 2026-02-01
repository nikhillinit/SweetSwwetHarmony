# Companies House Collector Reference

**Signal Type:** `incorporation`  
**API Base:** https://api.company-information.service.gov.uk  
**Auth:** Basic Auth (API key as username, empty password)  
**Rate Limit:** 600 requests per 5 minutes  

## API Endpoints

```
# Search companies
GET /search/companies?incorporated_from={date}&company_status=active

# Company profile
GET /company/{number}

# Officers
GET /company/{number}/officers
```

## SIC 2007 Codes (UK)

- **CPG:** 10xxx-11xxx (food/beverage), 20420 (cosmetics)
- **Health Tech:** 93xxx (fitness), 86xxx (healthcare)
- **Travel:** 55xxx (accommodation), 56xxx (restaurants)
- **Marketplaces:** 47xxx (retail), 62xxx (consumer apps)

## Confidence Formula

```python
base = 0.6  # Incorporation is authoritative
if is_target_sector: base += 0.2
if age_days <= 30: base += 0.15
if has_website: base += 0.05
if officers_count >= 2: base += 0.05
```

## Canonical Keys

1. `companies_house:12345678` (UK company number)
2. `domain:example.com` (if website)
3. `name_loc:name|uk`

## Authentication

```python
# Base64 encode: API_KEY:
auth_header = base64.b64encode(f"{api_key}:".encode()).decode()
headers = {"Authorization": f"Basic {auth_header}"}
```

## Common Issues

- Company status filter → Only "active" companies
- SIC codes in profile → Fetch /company/{number} for full data
- Dissolved companies → Check company_status field
