# Zenyt LinkedIn Tracking Dashboard

Track, enrich, and qualify leads from LinkedIn influencer posts with automatic HubSpot CRM integration.

## What It Does

🎯 **Track LinkedIn Posts** - Generate unique tracking links for influencer posts
📊 **Enrich Leads** - Auto-enrich with Apollo.io (revenue, employees, industry)
⚡ **ICP Scoring** - Score leads 0-100 based on your Ideal Customer Profile
🔄 **HubSpot Sync** - Push qualified leads to CRM with full attribution
📈 **Analytics** - Track ROI per post, conversion funnel, meeting pipeline

## Quick Start

### 1. Install

```bash
pip3 install -r requirements.txt
```

### 2. Configure

Create `.env` file:

```bash
WEBFLOW_API_TOKEN=your_token
WEBFLOW_SITE_ID=your_site_id
HUBSPOT_ACCESS_TOKEN=your_token
CALENDLY_API_TOKEN=your_token
APOLLO_API_KEY=your_key
```

### 3. Set Up HubSpot Properties

```bash
python3 scripts/create_linkedin_properties.py
```

### 4. Run

```bash
python3 run.py
```

Dashboard: **http://localhost:3000**

## Workflow

```
1. Generate tracking link    →  python3 scripts/generate_link.py
2. Share in LinkedIn post    →  https://zenyt.ai/?s=l15
3. Lead fills Webflow form   →  Captured with tracking code
4. Dashboard auto-enriches   →  Apollo API + ICP scoring
5. Review and push to CRM    →  HubSpot with full attribution
6. Track performance         →  Analytics dashboard
```

## Features

### Lead Management
- **Automatic enrichment** with Apollo.io (revenue, employees, industry, LinkedIn)
- **ICP scoring** (0-100) with priority levels (Very High, High, Medium, Low)
- **Duplicate detection** - Check HubSpot before adding leads
- **Post attribution** - Track which influencer/post generated each lead

### Dashboard Tabs
- **Pending** - New leads awaiting review
- **Pushed** - Leads sent to HubSpot
- **Rejected** - Filtered out leads
- **Post Performance** - ROI per influencer post
- **Analytics** - Weekly/monthly metrics, conversion funnel

### HubSpot Integration
- Push leads as contacts + companies
- Store LinkedIn post attribution
- Auto-sync meeting status (Calendly)
- Track deal progress (fast track, wins)

### Enrichment
- **Apollo API** - Company data (revenue, employees, industry, tech stack)
- **ICP Framework** - Automatic qualification scoring
- **Manual re-enrich** - Update button on each lead card

## Documentation

- **[SETUP.md](docs/SETUP.md)** - Complete installation and setup guide
- **[APOLLO.md](docs/APOLLO.md)** - Apollo integration & ICP scoring
- **[HUBSPOT.md](docs/HUBSPOT.md)** - HubSpot CRM integration
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System design & technical details

## Requirements

- Python 3.9+
- API access to:
  - Webflow (form submissions)
  - HubSpot (CRM integration)
  - Apollo.io (enrichment)
  - Calendly (optional, meeting sync)

## Tech Stack

- **Backend**: FastAPI + Python
- **APIs**: Webflow, HubSpot, Apollo, Calendly
- **Frontend**: HTML + Vanilla JavaScript (embedded)
- **Storage**: JSON files (local)

## Project Structure

```
zenyt-webflow-manager/
├── dashboard/          # FastAPI app & UI
├── pipeline/          # Enrichment & ICP logic
├── hubspot_managers/  # HubSpot integration
├── scripts/          # Utility scripts
├── docs/             # Documentation
├── run.py            # Entry point
└── requirements.txt  # Dependencies
```

## Support

### Common Issues

**Dashboard won't start:**
```bash
lsof -ti:3000 | xargs kill -9  # Kill existing process
python3 run.py                  # Restart
```

**No leads appearing:**
- Check Webflow API token in `.env`
- Verify form submissions in Webflow dashboard

**Apollo not enriching:**
- Test: `python3 pipeline/apollo_enrichment.py shopify.com`
- Check API key and credits at apollo.io

**HubSpot sync fails:**
- Verify token permissions (contacts + companies write)
- Run: `python3 scripts/create_linkedin_properties.py`

### Logs

Check terminal output when running `python3 run.py` for detailed error logs.

## License

Internal use only - Zenyt team.

---

**Built for tracking LinkedIn influencer post performance and lead attribution.**
