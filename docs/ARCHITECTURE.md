# System Architecture

Technical overview of the Zenyt LinkedIn tracking dashboard.

## System Overview

```
LinkedIn Post → Tracking Link → Webflow Form → Dashboard → HubSpot CRM
                                      ↓
                               Apollo Enrichment
                                      ↓
                                 ICP Scoring
                                      ↓
                              Analytics & Reporting
```

## Technology Stack

### Backend
- **FastAPI** - Web framework
- **Python 3.9+** - Runtime
- **Uvicorn** - ASGI server

### APIs
- **Webflow API** - Form submission sync
- **HubSpot API** - CRM integration
- **Apollo.io API** - Company enrichment
- **Calendly API** - Meeting sync

### Data Storage
- **JSON files** - Local data persistence
  - `leads_data.json` - Lead states (pending/pushed/rejected)
  - `post_database.json` - LinkedIn post metadata

### Frontend
- **HTML + JavaScript** - Embedded in FastAPI app
- **CSS** - Custom styling
- **No framework** - Vanilla JS for simplicity

## Project Structure

```
zenyt-webflow-manager/
├── dashboard/
│   ├── app.py              # Main FastAPI application
│   └── leads_data.json     # Lead persistence
│
├── pipeline/
│   ├── enrichment.py       # Web scraping enrichment
│   ├── apollo_enrichment.py # Apollo.io integration
│   ├── icp_checker.py      # ICP scoring logic
│   └── clay_enrichment.py  # Placeholder for Clay
│
├── hubspot_managers/
│   ├── hubspot_client.py   # HubSpot API client
│   ├── hubspot_sync.py     # Real-time sync
│   ├── calendly_sync.py    # Meeting sync
│   ├── deal_sync.py        # Deal progress sync
│   ├── company_manager.py  # Company operations
│   ├── contact_manager.py  # Contact operations
│   ├── email_generator.py  # Pre-call email draft generation
│   └── prospect_manager.py # Prospect folder/file creation
│
├── scripts/
│   ├── generate_link.py    # Create tracking links
│   ├── create_linkedin_properties.py  # HubSpot properties
│   ├── sync_deals.py       # Manual deal sync
│   └── ...
│
├── config/
│   └── settings.py         # Configuration
│
├── docs/                   # Documentation
├── post_database.json      # Post tracking data
├── requirements.txt        # Python dependencies
├── run.py                  # Application entry point
└── .env                    # Environment variables (gitignored)
```

## Core Components

### 1. Lead Ingestion (`dashboard/app.py`)

**Webflow Form Sync**
- Polls Webflow API every 5 minutes
- Fetches form submissions
- Extracts tracking code from hidden field
- Creates lead objects
- Stores in `leads_data.json`

**Duplicate Prevention**
- Checks HubSpot for existing companies
- Marks leads already in CRM
- Prevents duplicate reviews

### 2. Enrichment Pipeline (`pipeline/`)

**Apollo Enrichment** (Priority 1)
```python
apollo_enricher = ApolloEnricher()
company_data = apollo_enricher.enrich_company(domain)
# Returns: revenue, employees, industry, LinkedIn, tech stack
```

**Web Enrichment** (Fallback)
```python
enricher = WebEnricher()
result = enricher.enrich(domain)
# Returns: company type, e-commerce platform, basic info
```

### 3. ICP Scoring (`pipeline/icp_checker.py`)

**Scoring Model** (0-100 points)
- Company Match (50 pts)
- Catalog/Velocity (20 pts)
- Persona Density (20 pts)
- Timing/Context (10 pts)

**Priority Assignment**
- 80-100: VERY HIGH
- 60-79: HIGH
- 40-59: MEDIUM
- 20-39: LOW
- <20: DISQUALIFIED

### 4. HubSpot Integration (`hubspot_managers/`)

**Contact & Company Push**
```python
company_manager.create_or_update_company(lead)
contact_manager.create_or_update_contact(lead)
contact_manager.associate_to_company(contact, company)
```

**Auto-Sync**
- Calendly meetings → `meeting_booked` property
- Deal stages → `meeting_completed`, `is_fast_track`, `deal_won`

### 5. Dashboard UI (`dashboard/app.py`)

**Tabs**
- New (today's leads)
- Pending (awaiting review)
- Pushed (sent to HubSpot)
- Rejected (filtered out)
- Post Performance (per-post metrics)
- Analytics (aggregate data)

**Lead Actions**
- Push to HubSpot (auto-generates pre-call email draft)
- Find LinkedIn profile (Apollo People Match)
- Reject with reason
- Re-enrich with Apollo
- View HubSpot record

## Data Flow

### Lead Lifecycle

```
1. Form Submission
   Webflow API → Dashboard

2. Lead Creation
   Extract data → Apply post attribution

3. Enrichment
   Apollo API → Company data
   Web scraping → Fallback data

4. ICP Scoring
   Calculate score → Assign priority

5. Dashboard Display
   Render lead card → Show badges

6. User Action
   Push → HubSpot CRM
   Reject → Rejected list

7. Background Sync
   Calendly → Meeting status
   Deals → Progress tracking

8. Analytics
   Aggregate metrics → Display charts
```

### Tracking Flow

```
1. Generate Link
   scripts/generate_link.py → Create tracking code (e.g., l15)

2. User Clicks Link
   https://zenyt.ai/?s=l15 → Webflow site

3. JavaScript Capture
   URL parameter → localStorage → Hidden field

4. Form Submit
   Hidden field → Webflow API → Dashboard

5. Attribution Applied
   Match code → post_database.json → Assign creator/date/track

6. Display
   Lead card shows LinkedIn post attribution
```

## API Endpoints

### Dashboard API

```python
# Lead Management
GET  /api/leads                    # Get all leads
POST /api/leads/reload             # Sync from Webflow
POST /api/leads/{id}/push          # Push to HubSpot (returns email_draft)
POST /api/leads/{id}/reject        # Reject lead
POST /api/leads/{id}/enrich        # Re-enrich with Apollo
POST /api/leads/{id}/find-linkedin # Auto-find LinkedIn profile (Apollo People Match)
POST /api/leads/{id}/save-linkedin # Manually save LinkedIn URL
POST /api/leads/{id}/test          # Mark lead as test
POST /api/leads/{id}/already-booked # Mark as already booked

# HubSpot Integration
GET  /api/hubspot/check/{id}       # Check if lead exists

# Analytics
GET  /api/post-performance         # Per-post metrics
GET  /api/analytics                # Aggregate analytics

# Webhooks
POST /api/leads/webhook            # Receive external leads
```

### External APIs Used

**Webflow**
```
GET /v2/sites/{site_id}/forms/{form_id}/submissions
```

**HubSpot**
```
POST /crm/v3/objects/contacts
POST /crm/v3/objects/companies
GET  /crm/v3/objects/deals
POST /crm/v3/objects/contacts/{id}/associations/companies
```

**Apollo**
```
GET  /api/v1/organizations/enrich?domain={domain}   # Company enrichment
POST /api/v1/people/match                            # LinkedIn profile finder
```

**Calendly**
```
GET /scheduled_events?user={user_uri}
```

## Data Models

### Lead Object

```python
{
    "id": "unique_hash",
    "email": "contact@company.com",
    "website": "company.com",
    "domain": "company.com",
    "job_title": "VP of E-commerce",
    "created_at": "2026-01-15T10:00:00",
    
    # Post Attribution
    "post_source_auto": "l15",
    "post_creator": "laura",
    "post_date": "jan15",
    "post_track": "A3",
    
    # Enrichment Data (Apollo)
    "company_name": "Company Inc",
    "revenue_range": "$10M - $50M",
    "employee_count": 150,
    "industry": "E-commerce",
    "linkedin_url": "https://linkedin.com/company/...",
    "headquarters": "San Francisco, CA",
    "founded_year": 2018,
    "tech_stack": ["Shopify", "Klaviyo", ...],
    "apollo_enriched": true,
    
    # ICP Scoring
    "icp_score": 75,
    "priority": "HIGH",
    "qualified": true,
    "disqualification_reasons": [],
    
    # Meeting/Deal Status
    "meeting_booked": false,
    "meeting_completed": false,
    "is_fast_track": false,
    "deal_status": null,
    
    # State
    "status": "pending",  # pending, pushed, rejected
    "pushed_at": null,
    "rejected_at": null,
    "rejected_reason": null
}
```

### Post Object

```python
{
    "code": "l15",
    "creator": "laura",
    "date": "jan15",
    "track": "A3",
    "cost": 2500,
    "reactions": 887,
    "comments": 1987,
    "impressions": 100000,
    "engagement_rate": 2.9
}
```

## Scalability Considerations

### Current Limitations

- **JSON file storage**: Not suitable for >10k leads
- **Webflow polling**: 5-minute delay on new leads
- **No caching**: Re-fetches HubSpot data on every check
- **Single process**: No horizontal scaling

### Scaling Path

**Phase 1: Database** (1,000-10,000 leads)
- Replace JSON with SQLite or PostgreSQL
- Add indexes on email, domain, post_source
- Enable SQL queries for analytics

**Phase 2: Message Queue** (10,000-100,000 leads)
- Add Redis/RabbitMQ for background jobs
- Async enrichment workers
- Rate limit handling for APIs

**Phase 3: Caching** (100,000+ leads)
- Redis cache for HubSpot lookups
- Cache Apollo results (24h TTL)
- Pre-compute analytics

**Phase 4: Microservices** (Enterprise scale)
- Separate enrichment service
- Separate analytics service
- API gateway for routing

## Security

### Current Security

- ✅ API tokens in `.env` (gitignored)
- ✅ No authentication (localhost only)
- ✅ No exposed secrets in code
- ✅ Rate limiting on external APIs

### Production Hardening (Future)

- [ ] Add authentication (JWT tokens)
- [ ] HTTPS/TLS encryption
- [ ] API key rotation
- [ ] Audit logging
- [ ] Input validation & sanitization
- [ ] CORS configuration
- [ ] Secrets management (AWS Secrets Manager, Vault)

## Monitoring & Logging

### Current Logging

- **Console logs**: INFO, WARNING, ERROR levels
- **Module-specific**: Each module has its own logger
- **API responses**: Status codes logged

### Recommended Monitoring

- **Uptime**: Monitor port 3000 availability
- **API failures**: Track Apollo/HubSpot errors
- **Lead throughput**: Leads per day
- **Enrichment success rate**: % Apollo enriched
- **Dashboard performance**: Page load times

## Development

### Local Development

```bash
# Install dependencies
pip3 install -r requirements.txt

# Configure
cp .env.example .env  # Edit with your tokens

# Run
python3 run.py

# Test enrichment
python3 pipeline/apollo_enrichment.py test.com

# Test ICP scoring
python3 -c "from pipeline.icp_checker import ICPChecker; print(ICPChecker())"
```

### Testing

Currently no automated tests. Recommended:

```bash
# Unit tests (future)
pytest tests/

# Integration tests
pytest tests/integration/

# API tests
pytest tests/api/
```

## Related Documentation

- [SETUP.md](SETUP.md) - Installation guide
- [APOLLO.md](APOLLO.md) - Apollo integration
- [HUBSPOT.md](HUBSPOT.md) - HubSpot integration
