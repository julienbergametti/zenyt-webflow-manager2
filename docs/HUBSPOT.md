# HubSpot CRM Integration

Complete guide for HubSpot integration, auto-sync, and deal tracking.

## Overview

The dashboard integrates with HubSpot to:
- Push enriched leads as contacts + companies
- Auto-sync meeting status from Calendly
- Track deal progress (fast track, won/lost)
- Store LinkedIn post attribution
- Prevent duplicates

## Setup

### 1. Create HubSpot Private App

1. Go to HubSpot → Settings → Integrations → Private Apps
2. Click "Create a private app"
3. Name it "Zenyt Dashboard"
4. **Required scopes**:
   - `crm.objects.contacts.read`
   - `crm.objects.contacts.write`
   - `crm.objects.companies.read`
   - `crm.objects.companies.write`
   - `crm.objects.deals.read`
   - `crm.schemas.contacts.read` (for properties)
5. Generate token and copy it

### 2. Add to Environment

```bash
# .env
HUBSPOT_ACCESS_TOKEN=pat-na2-your-token-here
```

### 3. Create Required Properties

Run the property creation script:

```bash
python3 scripts/create_linkedin_properties.py
```

This creates 4 contact properties:
- `linkedin_post_source` (e.g., "l15")
- `linkedin_post_creator` (e.g., "laura")
- `linkedin_post_date` (e.g., "jan15")
- `linkedin_post_track` (e.g., "A3")

These store LinkedIn post attribution for each contact.

## Features

### Lead Push to HubSpot

When you click "Push to HubSpot" on a lead:

**Contact created/updated with:**
- First Name / Last Name
- Email (unique identifier)
- Job Title
- LinkedIn URL (personal, from Apollo People Match or manual entry)
- LinkedIn post properties (source, creator, date, track)

**Company created/updated with:**
- Company Name
- Company Domain (unique identifier)
- Company LinkedIn URL (from Apollo)
- Industry
- Employee Count (from Apollo)
- Revenue Range (from Apollo)
- Founded Year (from Apollo)
- Headquarters (from Apollo)

**Auto-association:**
- Contact automatically associated to Company by domain

**Pre-call email draft (auto-generated):**

After a successful push, the dashboard generates a personalized pre-call email and shows it in a modal with copy buttons. The email includes:
- Industry-specific context paragraph and P.S. line
- Agency detection (adds portfolio question for agencies)
- Calendly CTA for booking a call
- LinkedIn connection request message
- Mailto link for quick sending

The email content can also be used via the Microsoft MCP `create_email_draft` tool in Cursor.

### Duplicate Prevention

The dashboard checks HubSpot before adding to pending:
- **By company domain**: Searches existing companies
- **Shows badge**: "Already in HubSpot CRM" with link
- **Prevents duplicates**: Won't create duplicate contacts

### Automatic Sync

The dashboard auto-syncs with HubSpot for:

#### Meeting Status (Calendly Integration)

```bash
# .env
CALENDLY_API_TOKEN=your_calendly_token
```

- Syncs scheduled meetings from Calendly
- Updates `meeting_booked` property
- Only counts unique meetings
- Excludes customer meetings
- Updates `meeting_completed` when meeting is done

#### Deal Progress Sync

Run the deal sync script:

```bash
python3 scripts/sync_deals.py
```

Updates contact properties based on deal stages:
- `meeting_completed` - Deal moved to "Meeting Done" stage
- `is_fast_track` - Deal in fast track pipeline
- `deal_won` - Deal closed/won

**Auto-sync setup** (optional):
Add to crontab for automatic updates:

```bash
# Run every hour
0 * * * * cd /path/to/zenyt-webflow-manager && python3 scripts/sync_deals.py
```

## Dashboard Features

### HubSpot Status Indicator

Each lead card shows:
- ✨ **New Lead** - Not in HubSpot yet
- ✅ **Already in HubSpot CRM** - Exists in HubSpot (with link)

### Meeting Indicators

Badges show meeting status:
- 📆 **Meeting Booked** (green) - Calendly meeting scheduled
- ✅ **Meeting Done** (green) - Meeting completed
- ⚡ **Fast Track** (orange) - In fast track pipeline
- 🏆 **Won** (purple) - Deal closed won

### Post Performance Metrics

The "Post Performance" tab shows HubSpot data:
- Meetings booked (from Calendly)
- Meetings completed (from deals)
- Fast tracks (from deals)
- Wins (from deals)
- ROI per post

### Analytics Dashboard

The "Analytics" tab aggregates HubSpot data:
- Total leads pushed to CRM
- ICP fit (qualified leads)
- Meetings booked/completed
- Fast track count
- Deal wins
- Weekly/monthly breakdowns

## Data Flow

```
Dashboard Lead
    ↓
Push Button Clicked
    ↓
Create/Update Company (by domain)
    ↓
Create/Update Contact (by email)
    ↓
Associate Contact → Company
    ↓
Set LinkedIn Properties
    ↓
Background Sync (hourly):
  - Calendly → meeting_booked
  - Deals → meeting_completed, fast_track, won
    ↓
Dashboard shows updated status
```

## API Endpoints

The dashboard provides HubSpot API endpoints:

### Check if Lead Exists

```bash
GET /api/hubspot/check/{lead_id}
```

Returns: `{"exists": true/false, "url": "hubspot_link"}`

### Sync Deals

```bash
POST /api/hubspot/sync-deals
```

Triggers manual deal sync.

## HubSpot Workflows (Recommended)

### Auto-assign Leads

Create a workflow in HubSpot:
1. Trigger: Contact property `linkedin_post_creator` is known
2. Action: Assign to owner based on creator
   - laura → Owner A
   - freddie → Owner B

### Auto-sequence for ICP

1. Trigger: Contact property `linkedin_post_source` is known
2. Filter: ICP score ≥ 60 (custom property)
3. Action: Enroll in sequence "LinkedIn Inbound"

### Fast Track Notification

1. Trigger: Deal stage changed to "Fast Track"
2. Action: Send Slack notification to #sales
3. Action: Create task for senior rep

## Properties Reference

### Contact Properties (LinkedIn Attribution)

| Property | Type | Description | Example |
|----------|------|-------------|---------|
| `linkedin_post_source` | Text | Tracking code | "l15" |
| `linkedin_post_creator` | Text | Influencer name | "laura" |
| `linkedin_post_date` | Text | Post date | "jan15" |
| `linkedin_post_track` | Text | Campaign track | "A3" |

### Contact Properties (Auto-synced)

| Property | Type | Source | Description |
|----------|------|--------|-------------|
| `meeting_booked` | Boolean | Calendly | Has scheduled meeting |
| `meeting_completed` | Boolean | Deals | Meeting done |
| `is_fast_track` | Boolean | Deals | In fast track |
| `deal_won` | Boolean | Deals | Deal closed won |

### Company Properties (From Apollo)

| Property | Type | Description |
|----------|------|-------------|
| Company Name | Text | Company name |
| Company Domain | Text | Unique identifier |
| Company LinkedIn URL | URL | From Apollo |
| Industry | Text | From Apollo |
| Number of Employees | Number | From Apollo |
| Annual Revenue | Text | Range from Apollo |
| Founded Year | Number | From Apollo |

## Troubleshooting

### "HubSpot integration not available"

1. Check token: `cat .env | grep HUBSPOT`
2. Verify token scopes at HubSpot
3. Test connection: `curl -H "Authorization: Bearer YOUR_TOKEN" https://api.hubapi.com/crm/v3/objects/contacts?limit=1`

### Duplicate contacts created

- Ensure email is unique
- Check company domain normalization
- Use HubSpot's deduplication tools

### Properties not showing in HubSpot

1. Run property creation: `python3 scripts/create_linkedin_properties.py`
2. Check HubSpot → Settings → Properties → Contact properties
3. Verify token has `crm.schemas.contacts.read` scope

### Meeting sync not working

1. Verify Calendly token: `cat .env | grep CALENDLY`
2. Check Calendly API limits
3. Run manual sync: `python3 scripts/sync_deals.py`
4. Check logs for errors

### Deal sync not updating

1. Verify deal stages match expected names:
   - "Meeting Done" (or your equivalent)
   - "Fast Track" (or your fast track pipeline)
2. Check deal associations to contacts
3. Run manual sync with verbose logging

## Best Practices

1. **Use consistent naming**: Keep post codes (l15, f3) organized
2. **Tag creators properly**: Ensure creator names are consistent
3. **Regular sync**: Run deal sync at least daily
4. **Monitor duplicates**: Check HubSpot's duplicate management
5. **Clean data**: Reject bad leads before pushing
6. **Use workflows**: Automate routing and sequences in HubSpot

## ROI Tracking

The dashboard calculates ROI per post:

```
Cost Per Lead = Post Cost / Total Leads
Cost Per Meeting = Post Cost / Meetings Booked
Cost Per Win = Post Cost / Deals Won
ROI = (Deal Value × Wins - Post Cost) / Post Cost × 100%
```

View in "Post Performance" tab with HubSpot deal data.

## Advanced: Custom Properties

To add more properties:

1. Create property in HubSpot
2. Add to `scripts/create_linkedin_properties.py`
3. Update `dashboard/app.py` push logic
4. Restart dashboard

Example:
```python
{
    "name": "linkedin_post_cost",
    "label": "LinkedIn Post Cost",
    "type": "number",
    "fieldType": "number",
    "groupName": "contactinformation"
}
```

## Related Documentation

- [SETUP.md](SETUP.md) - Initial setup
- [APOLLO.md](APOLLO.md) - Apollo enrichment
- [ARCHITECTURE.md](ARCHITECTURE.md) - System design
