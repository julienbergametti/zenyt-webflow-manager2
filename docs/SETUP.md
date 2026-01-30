# Setup Guide — Zenyt LinkedIn Tracking Dashboard

Complete setup guide for the Zenyt LinkedIn post tracking and lead enrichment dashboard.

## Prerequisites

- Python 3.9+
- API Keys:
  - Webflow API Token
  - HubSpot Access Token
  - Calendly API Token (optional)
  - Apollo API Key

## Quick Start

### 1. Install Dependencies

```bash
cd /Users/antoinegiacomini/Documents/Zenyt/laboratory/zenyt-webflow-manager
pip3 install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the project root:

```bash
# Webflow
WEBFLOW_API_TOKEN=your_webflow_token
WEBFLOW_SITE_ID=your_site_id

# HubSpot
HUBSPOT_ACCESS_TOKEN=your_hubspot_pat

# Calendly (optional)
CALENDLY_API_TOKEN=your_calendly_token

# Apollo.io
APOLLO_API_KEY=your_apollo_key
```

**How to get API keys:**

- **Webflow**: Settings → Integrations → API Access
- **HubSpot**: Settings → Integrations → Private Apps → Create token
- **Calendly**: Settings → Integrations → API & Webhooks
- **Apollo**: Settings → API → Create API key (Enrichment API)

### 3. Set Up HubSpot Properties

Run the property creation script:

```bash
python3 scripts/create_linkedin_properties.py
```

This creates the required HubSpot contact properties:
- `linkedin_post_source` (tracking code)
- `linkedin_post_creator` (influencer name)
- `linkedin_post_date` (post date)
- `linkedin_post_track` (campaign track)

### 4. Start the Dashboard

```bash
python3 run.py
```

Dashboard will be available at: **http://localhost:3000**

## Webflow Tracking Setup

### Generate a Tracking Link

```bash
python3 scripts/generate_link.py
```

Follow the prompts to create a tracking link like: `https://zenyt.ai/?s=l15`

### Add Tracking Code to Webflow

1. Go to Webflow → Project Settings → Custom Code
2. Add this code to **Footer Code** (before `</body>`):

```html
<script>
(function() {
    // Capture ?s= parameter and store in localStorage
    const params = new URLSearchParams(window.location.search);
    const source = params.get('s');
    if (source) {
        localStorage.setItem('post_source', source);
        console.log('Tracking source captured:', source);
    }

    // Inject into form hidden field
    const observer = new MutationObserver(function() {
        const form = document.querySelector('form');
        if (form && !form.querySelector('[name="Post-Source-Auto"]')) {
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = 'Post-Source-Auto';
            hidden.value = localStorage.getItem('post_source') || '';
            form.appendChild(hidden);
            console.log('Injected tracking field with value:', hidden.value);
        }
    });
    
    observer.observe(document.body, { childList: true, subtree: true });
})();
</script>
```

3. Add a hidden field to your Webflow form named **"Post-Source-Auto"**

### Test the Tracking

1. Generate a test link: `https://zenyt.ai/?s=test123`
2. Visit the link in a browser
3. Fill out the form
4. Check the dashboard - lead should appear with attribution

## Workflow

```
LinkedIn Post → Tracking Link (?s=code) → Webflow Form → Dashboard → HubSpot
                      ↓
                  localStorage
                      ↓
              Hidden Form Field
                      ↓
            Webflow API Captures
                      ↓
         Dashboard Fetches & Enriches
         (Apollo + ICP Scoring)
                      ↓
            Push to HubSpot CRM
```

## Dashboard Features

### Lead Management
- **Pending**: New leads awaiting review
- **Pushed**: Leads sent to HubSpot
- **Rejected**: Filtered out leads

### Enrichment
- **Apollo API**: Automatic company data (revenue, employees, industry)
- **ICP Scoring**: 0-100 score based on your ICP framework
- **Manual Re-enrichment**: Button to update Apollo data

### Tracking
- **Post Attribution**: See which influencer/post generated each lead
- **Meeting Sync**: Calendly integration shows booked/completed meetings
- **Deal Tracking**: HubSpot deals synced to contact properties

### Analytics
- **Weekly/Monthly**: Traffic sources, conversion funnel
- **Post Performance**: ROI per influencer post
- **CSV Export**: Download analytics data

## Troubleshooting

### Dashboard won't start
```bash
# Check for port conflicts
lsof -ti:3000 | xargs kill -9

# Restart
python3 run.py
```

### No leads appearing
- Check Webflow API token in `.env`
- Verify form submissions in Webflow
- Check dashboard logs for errors

### Apollo not enriching
- Verify `APOLLO_API_KEY` in `.env`
- Test: `python3 pipeline/apollo_enrichment.py shopify.com`
- Check API credit limits at apollo.io

### HubSpot sync issues
- Verify `HUBSPOT_ACCESS_TOKEN` in `.env`
- Ensure HubSpot properties are created
- Check token permissions (contacts + companies write access)

## Next Steps

- Read [APOLLO.md](APOLLO.md) for Apollo integration details
- Read [HUBSPOT.md](HUBSPOT.md) for HubSpot sync details
- Read [ARCHITECTURE.md](ARCHITECTURE.md) for system design

## Support

For issues, check the logs:
- Dashboard logs: stdout when running `python3 run.py`
- Error logs: Check terminal output for tracebacks
