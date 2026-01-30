#!/usr/bin/env python3
"""
Lead Dashboard - Review and push leads to HubSpot
Beautiful web interface with ICP details, history, and rejected leads
Now with Webflow API integration - no webhook/ngrok needed!
"""

import asyncio
import json
import logging
import os
import re
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

# Webflow API configuration
WEBFLOW_API_TOKEN = os.getenv("WEBFLOW_API_TOKEN")
WEBFLOW_SITE_ID = os.getenv("WEBFLOW_SITE_ID", "68a35cba2419d62e582f33b7")
WEBFLOW_API_BASE = "https://api.webflow.com/v2"

# Optional imports - make them lazy to allow dashboard to start without all dependencies
try:
    from config.settings import get_settings
except ImportError:
    get_settings = None

from pipeline.enrichment import WebEnricher, EnrichmentResult
from pipeline.icp_checker import ICPChecker, Priority
from pipeline.apollo_enrichment import ApolloEnricher, ApolloCompanyData

# HubSpot managers - import lazily when needed
try:
    from hubspot_managers.company_manager import CompanyManager
    from hubspot_managers.contact_manager import ContactManager
    HUBSPOT_AVAILABLE = True
except ImportError:
    CompanyManager = None
    ContactManager = None
    HUBSPOT_AVAILABLE = False

# Prospect and email generation - import lazily when needed
try:
    from hubspot_managers.email_generator import generate_demo_request_email
    from hubspot_managers.prospect_manager import (
        create_prospect_folder,
        create_campaign_overview,
        create_touch_1_file
    )
    PROSPECT_GENERATION_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Prospect generation modules not available: {e}")
    generate_demo_request_email = None
    create_prospect_folder = None
    create_campaign_overview = None
    create_touch_1_file = None
    PROSPECT_GENERATION_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zenyt Lead Dashboard")

# Initialize HubSpot sync globally (optional - works without it)
hubspot_sync = None
try:
    from hubspot_managers.hubspot_sync import get_hubspot_sync
    hubspot_sync = get_hubspot_sync()
    if hubspot_sync:
        logger.info("✅ HubSpot real-time sync enabled at startup")
except Exception as e:
    logger.warning(f"HubSpot sync not available: {e}")

# Data persistence file
DATA_FILE = Path(__file__).parent / "leads_data.json"

# Keywords in rejected_reason that indicate "already booked" (meeting in Calendly etc.)
ALREADY_BOOKED_REASON_KEYWORDS = (
    "booked in calendly", "already in calendly", "meeting already booked",
    "already booked", "in calendly", "calendly", "booked"
)

def _rejected_reason_is_already_booked(reason: Optional[str]) -> bool:
    if not reason:
        return False
    r = reason.lower().strip()
    return any(k in r for k in ALREADY_BOOKED_REASON_KEYWORDS)


def _is_test_lead(lead: dict) -> bool:
    """Exclude leads whose email or domain contains 'test' so they never appear."""
    email = (lead.get("email") or "").lower()
    domain = (lead.get("domain") or "").lower()
    return "test" in email or "test" in domain


def _is_2025_lead(lead: dict) -> bool:
    """True if lead created_at is in 2025 (excluded from pending forever)."""
    return (lead.get("created_at") or "").startswith("2025-")


# Blocklist: these emails/domains are test and never shown or persisted
BLOCKED_LEAD_EMAILS = {"w@w.com", "jean-bernard@paslechoix.com"}
BLOCKED_LEAD_DOMAINS = {"w", "paslechoix", "paslechoix.com"}


def _is_blocked_lead(lead: dict) -> bool:
    """True if lead is on blocklist (test/fake, excluded forever)."""
    email = (lead.get("email") or "").strip().lower()
    domain = (lead.get("domain") or "").strip().lower()
    return email in BLOCKED_LEAD_EMAILS or domain in BLOCKED_LEAD_DOMAINS


def load_leads():
    """Load leads from JSON file - called on every request for real-time sync.
    Returns (pending, pushed, rejected, already_booked).
    One-time migration: rejected leads with reason containing booked/calendly are moved to already_booked.
    """
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
            pending = data.get('pending', [])
            # Exclude 2025 pending leads so they never appear on refresh or poll
            pending = [l for l in pending if not _is_2025_lead(l)]
            pushed = data.get('pushed', [])
            rejected = data.get('rejected', [])
            already_booked = data.get('already_booked', [])
            # Exclude test leads (email or domain contains "test") so they never appear
            pending = [l for l in pending if not _is_test_lead(l)]
            pushed = [l for l in pushed if not _is_test_lead(l)]
            rejected = [l for l in rejected if not _is_test_lead(l)]
            already_booked = [l for l in already_booked if not _is_test_lead(l)]
            # Exclude blocklisted leads (w@w.com, jean-bernard@paslechoix.com) forever
            pending = [l for l in pending if not _is_blocked_lead(l)]
            pushed = [l for l in pushed if not _is_blocked_lead(l)]
            rejected = [l for l in rejected if not _is_blocked_lead(l)]
            already_booked = [l for l in already_booked if not _is_blocked_lead(l)]
            # One-time migration: move rejected leads that were "already booked" into already_booked
            to_remove = []
            for lead in rejected:
                if _rejected_reason_is_already_booked(lead.get('rejected_reason')):
                    lead['status'] = 'already_booked'
                    lead['already_booked_at'] = lead.get('rejected_at') or datetime.now().isoformat()
                    already_booked.append(lead)
                    to_remove.append(lead)
            for lead in to_remove:
                rejected.remove(lead)
            if to_remove:
                save_leads(pending, pushed, rejected, already_booked)
                logger.info(f"Migrated {len(to_remove)} rejected lead(s) to already_booked")
            logger.info(f"Loaded {len(pending)} pending, {len(pushed)} pushed, {len(rejected)} rejected, {len(already_booked)} already_booked leads")
            return pending, pushed, rejected, already_booked
        except Exception as e:
            logger.error(f"Error loading leads: {e}")
    return [], [], [], []

def save_leads(lead_queue, pushed_leads, rejected_leads, already_booked_leads):
    """Save leads to JSON file (pending, pushed, rejected, already_booked)"""
    try:
        # Never persist 2025 pending or blocklisted leads so they never reappear
        lead_queue = [l for l in lead_queue if not _is_2025_lead(l) and not _is_blocked_lead(l)]
        pushed_leads = [l for l in pushed_leads if not _is_blocked_lead(l)]
        rejected_leads = [l for l in rejected_leads if not _is_blocked_lead(l)]
        already_booked_leads = [l for l in already_booked_leads if not _is_blocked_lead(l)]
        with open(DATA_FILE, 'w') as f:
            json.dump({
                'pending': lead_queue,
                'pushed': pushed_leads,
                'rejected': rejected_leads,
                'already_booked': already_booked_leads,
                'last_updated': datetime.now().isoformat()
            }, f, indent=2)
        logger.info(f"Saved {len(lead_queue)} pending, {len(pushed_leads)} pushed, {len(rejected_leads)} rejected, {len(already_booked_leads)} already_booked leads")
    except Exception as e:
        logger.error(f"Error saving leads: {e}")


# ============== WEBFLOW API INTEGRATION ==============

def get_webflow_headers():
    """Get headers for Webflow API requests"""
    return {
        "Authorization": f"Bearer {WEBFLOW_API_TOKEN}",
        "accept": "application/json"
    }


def normalize_website_url(url: str) -> str:
    """Normalize website URL - remove any existing protocol and add https://"""
    if not url:
        return ""
    # Remove any existing protocol (http://, https://, or malformed like https//)
    # Handle multiple protocols by removing them iteratively
    while True:
        original = url
        # Remove http:// or https://
        url = re.sub(r'^https?://', '', url, flags=re.IGNORECASE, count=1)
        # Remove malformed protocols like https// or http//
        url = re.sub(r'^https?//', '', url, flags=re.IGNORECASE, count=1)
        # Remove http:/ or https:/ (missing one slash)
        url = re.sub(r'^https?:/', '', url, flags=re.IGNORECASE, count=1)
        # If no change, break
        if url == original:
            break
    # Remove any leading slashes
    url = url.lstrip('/')
    # Add https:// if we have a valid domain
    if url:
        return f"https://{url}"
    return ""


def extract_domain_from_url(url: str) -> str:
    """Extract apex domain from URL or email"""
    if not url:
        return ""
    # Remove protocol
    url = re.sub(r'^https?://', '', url.lower())
    # Remove www.
    url = re.sub(r'^www\.', '', url)
    # Get just the domain part
    domain = url.split('/')[0].split('?')[0]
    return domain


CONSUMER_EMAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "protonmail.com", "mail.com", "live.com", "me.com", "ymail.com",
    "googlemail.com", "aol.com", "test.com", "example.com", "mailinator.com",
})


def get_email_domain_for_enrichment(lead: Dict) -> Optional[str]:
    """
    Derive email domain from lead email for dual Apollo enrichment.
    Returns None if missing or consumer domain (gmail, yahoo, etc.).
    """
    email = lead.get("email") or ""
    if not email or "@" not in email:
        return None
    domain = email.split("@")[-1].strip().lower()
    if not domain or domain in CONSUMER_EMAIL_DOMAINS:
        return None
    return domain


def normalize_website_domain(lead: Dict) -> str:
    """Normalize website domain from lead (domain or website)."""
    raw = lead.get("domain") or lead.get("website") or ""
    return extract_domain_from_url(raw) if raw else ""


# HubSpot industry enum fixes: our normalized value -> HubSpot's exact option
HUBSPOT_INDUSTRY_ENUM_FIX = {
    "INFORMATION_TECHNOLOGY_SERVICES": "INFORMATION_TECHNOLOGY_AND_SERVICES",
}


def normalize_industry_for_hubspot(industry: str) -> Optional[str]:
    """
    Convert industry to HubSpot's enum format: UPPERCASE with underscores.
    e.g. 'apparel & fashion' -> 'APPAREL_FASHION'.
    Returns None if empty or 'Unknown' (skip sending to avoid validation errors).
    """
    if not industry or not str(industry).strip():
        return None
    s = str(industry).strip().upper()
    if s == "UNKNOWN":
        return None
    # Replace spaces, &, -, commas, etc. with underscore; collapse multiple; strip
    for c in " &-,./'":
        s = s.replace(c, "_")
    while "__" in s:
        s = s.replace("__", "_")
    s = s.strip("_")
    if not s:
        return None
    # Apply HubSpot enum fixes (e.g. INFORMATION_TECHNOLOGY_SERVICES -> INFORMATION_TECHNOLOGY_AND_SERVICES)
    s = HUBSPOT_INDUSTRY_ENUM_FIX.get(s, s)
    return s


def generate_lead_id(email: str, website: str, submitted_at: str) -> str:
    """Generate a unique lead ID based on submission data"""
    unique_string = f"{email}|{website}|{submitted_at}"
    return hashlib.md5(unique_string.encode()).hexdigest()[:12]


def is_demo_request(lead: Dict) -> bool:
    """Detect if this is a demo request from form data"""
    # Check if demo_request field is explicitly set to 'true'
    demo_request = lead.get("demo_request", "").lower()
    if demo_request == "true":
        return True
    
    # Check form fields for demo request indicators
    demo_indicators = [
        lead.get("request_type", "").lower() == "demo",
        lead.get("interested_in", "").lower() == "demo",
        lead.get("demo", "").lower() == "true",
        lead.get("request_demo", "").lower() == "true",
    ]
    
    # Must have website/domain AND one of the indicators
    has_website = bool(lead.get("website") or lead.get("domain"))
    return any(demo_indicators) and has_website


def fetch_webflow_forms() -> List[Dict]:
    """
    Fetch forms from Webflow site.
    Only returns forms that have BOTH email AND URL fields.
    """
    if not WEBFLOW_API_TOKEN:
        logger.warning("WEBFLOW_API_TOKEN not configured")
        return []
    
    try:
        response = requests.get(
            f"{WEBFLOW_API_BASE}/sites/{WEBFLOW_SITE_ID}/forms",
            headers=get_webflow_headers()
        )
        response.raise_for_status()
        data = response.json()
        
        # Filter to forms with BOTH email AND URL fields
        valid_forms = []
        for form in data.get("forms", []):
            fields = form.get("fields", {})
            
            has_email = False
            has_url = False
            
            for field_id, field_info in fields.items():
                field_name = field_info.get("displayName", "").lower()
                field_type = field_info.get("type", "").lower()
                
                if "email" in field_name or field_type == "email":
                    has_email = True
                if "url" in field_name or "site" in field_name or "website" in field_name:
                    has_url = True
            
            # Only include forms with BOTH email and URL
            if has_email and has_url:
                valid_forms.append(form)
                logger.info(f"Including form: {form.get('displayName')} (has email + URL)")
            else:
                logger.debug(f"Skipping form: {form.get('displayName')} (email={has_email}, url={has_url})")
        
        logger.info(f"Found {len(valid_forms)} valid lead forms (with email + URL)")
        return valid_forms
    except Exception as e:
        logger.error(f"Error fetching Webflow forms: {e}")
        return []


def fetch_webflow_submissions(form_id: str, limit: int = 100) -> List[Dict]:
    """Fetch submissions for a specific form"""
    if not WEBFLOW_API_TOKEN:
        return []
    
    try:
        response = requests.get(
            f"{WEBFLOW_API_BASE}/forms/{form_id}/submissions?limit={limit}",
            headers=get_webflow_headers()
        )
        response.raise_for_status()
        data = response.json()
        return data.get("formSubmissions", [])
    except Exception as e:
        logger.error(f"Error fetching submissions for form {form_id}: {e}")
        return []


def apply_post_attribution(lead: Dict) -> Dict:
    """Apply LinkedIn post attribution based on tracking code"""
    code = lead.get('post_source_auto')
    if not code:
        return lead
    
    # Load post database
    db_path = Path(__file__).parent.parent / "post_database.json"
    if not db_path.exists():
        logger.warning("post_database.json not found")
        return lead
    
    try:
        with open(db_path, 'r') as f:
            post_db = json.load(f)
        
        # Find matching post
        post = next((p for p in post_db.get('posts', []) if p['code'] == code), None)
        
        if post:
            lead['post_creator'] = post['creator']
            lead['post_date'] = post['date']
            lead['post_track'] = post['track']
            lead['post_cost'] = post['cost']
            lead['post_source'] = f"{post['creator']}_{post['date']}"
            logger.info(f"✅ Attributed lead {lead.get('email')} → {post['creator']} {post['date']} (track: {post['track']})")
        else:
            logger.warning(f"⚠️  Unknown tracking code: {code}")
    
    except Exception as e:
        logger.error(f"Error applying post attribution: {e}")
    
    return lead


def parse_webflow_submission(submission: Dict, form_name: str) -> Optional[Dict]:
    """
    Parse a Webflow submission into our lead format.
    IMPORTANT: Only accepts leads with BOTH email AND website URL.
    """
    form_response = submission.get("formResponse", {})
    submitted_at = submission.get("dateSubmitted", datetime.now().isoformat())
    
    # Extract email and website from form response
    email = None
    website = None
    name = None
    message = None
    post_source_auto = None  # LinkedIn tracking code
    
    for field_name, value in form_response.items():
        if not value:
            continue
        field_lower = field_name.lower()
        
        # Check for tracking field (LinkedIn post tracking)
        if "post_source_auto" in field_lower or "source_auto" in field_lower:
            post_source_auto = value.strip() if value else None
        # Check for email fields
        elif "email" in field_lower:
            email = value.strip() if value else None
        # Check for URL/website fields
        elif "url" in field_lower or "site" in field_lower or "website" in field_lower:
            website = value.strip() if value else None
        # Check for name fields
        elif field_lower == "name" or "your name" in field_lower:
            name = value.strip() if value else None
        # Check for message fields
        elif "message" in field_lower:
            message = value.strip() if value else None
    
    # CRITICAL: Must have BOTH email AND website - reject otherwise
    if not email or not website:
        logger.debug(f"Skipping submission - missing email or website. Email: {email}, Website: {website}")
        return None
    
    # Validate email format
    if "@" not in email:
        logger.debug(f"Skipping submission - invalid email format: {email}")
        return None
    
    # Skip test/personal emails
    email_lower = email.lower()
    email_domain = email.split("@")[1].lower()
    
    skip_domains = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", 
                    "icloud.com", "aol.com", "mail.com", "protonmail.com",
                    "test.com", "example.com", "mailinator.com"]
    skip_patterns = ["test@", "demo@", "fake@", "edu.escp.eu", "student.", ".edu"]
    
    if email_domain in skip_domains:
        logger.debug(f"Skipping personal email: {email}")
        return None
    
    if any(pattern in email_lower for pattern in skip_patterns):
        logger.debug(f"Skipping test/edu email: {email}")
        return None
    
    # Extract domain from website
    domain = extract_domain_from_url(website)
    if not domain:
        # Try to extract from email as fallback
        email_domain = email.split("@")[1].lower()
        # Skip generic email domains
        generic_domains = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", 
                          "icloud.com", "aol.com", "mail.com", "protonmail.com",
                          "edu.escp.eu", "student.", "edu.", "test.com"]
        if not any(g in email_domain for g in generic_domains):
            domain = email_domain
    
    if not domain:
        logger.debug(f"Skipping submission - could not extract domain from {website}")
        return None
    
    lead_id = generate_lead_id(email, website, submitted_at)
    
    # Apply LinkedIn post attribution
    lead = {
        "id": lead_id,
        "webflow_id": submission.get("id"),
        "email": email,
        "website": normalize_website_url(website),
        "domain": domain,
        "name": name,
        "message": message,
        "form_name": form_name,
        "created_at": submitted_at,
        "source": "webflow_api",
        "post_source_auto": post_source_auto
    }
    
    # Apply attribution logic if tracking code exists
    if post_source_auto:
        lead = apply_post_attribution(lead)
    
    return lead


def get_last_sync_timestamp() -> Optional[str]:
    """Get the last sync timestamp from data file"""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                return data.get('last_webflow_sync')
        except:
            pass
    return None


def save_sync_timestamp(timestamp: str):
    """Save the last sync timestamp"""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
            data['last_webflow_sync'] = timestamp
            with open(DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving sync timestamp: {e}")


def domain_to_company_name(domain: str) -> str:
    """Convert domain to company name (remove TLD like .com, .fr, etc.)"""
    if not domain:
        return "Unknown"
    d = domain.replace('https://', '').replace('http://', '').replace('www.', '')
    d = d.split('/')[0]  # Remove path
    parts = d.split('.')
    if len(parts) > 1:
        parts.pop()  # Remove TLD
    name = '-'.join(parts)
    # Capitalize first letter
    return name.capitalize() if name else "Unknown"


def _scanned_url_dict_from_web(result: EnrichmentResult, domain: str, default_name: str) -> Dict:
    """Build apollo_scanned_url-shaped dict from WebEnricher result."""
    return {
        "company_name": result.company_name or default_name,
        "domain": domain,
        "revenue": result.revenue_display,
        "revenue_range": result.revenue_display,
        "employee_count": result.employee_count,
        "employee_range": None,
        "industry": result.industry,
        "technologies": result.technologies or [],
        "founded_year": None,
        "headquarters": None,
        "linkedin_url": None,
        "phone": None,
        "description": result.description,
    }


def enrich_lead_data(lead: Dict) -> Dict:
    """Enrich a lead with ICP scoring and company data (Apollo + Web enrichment).
    Supports dual enrichment: when contact works for a different company (e.g. agency)
    than the demo URL, we store apollo_contact_company (email domain) and
    apollo_scanned_url (website domain). Flat fields and ICP always use scanned URL.
    """
    domain = lead.get("domain")
    if not domain:
        lead["icp_score"] = 0
        lead["priority"] = "Low"
        lead["qualified"] = False
        lead["company_name"] = "Unknown"
        lead["company_type"] = "Unknown"
        lead["industry"] = "Unknown"
        lead["apollo_dual_enrichment"] = False
        return lead

    website_domain = normalize_website_domain(lead) or domain
    email_domain = get_email_domain_for_enrichment(lead)
    dual = bool(email_domain and email_domain != website_domain)
    default_company_name = domain_to_company_name(website_domain)

    lead["apollo_dual_enrichment"] = False
    lead["apollo_contact_company"] = None
    lead["apollo_scanned_url"] = None

    try:
        apollo_enricher = ApolloEnricher()

        # --- Scanned URL (always): Apollo first, then web fallback ---
        apollo_scanned = apollo_enricher.enrich_company(website_domain)
        if apollo_scanned:
            logger.info(f"✅ Apollo enriched (scanned): {website_domain} ({apollo_scanned.company_name})")
            lead["apollo_scanned_url"] = apollo_scanned.to_dict()
            lead["company_name"] = apollo_scanned.company_name or default_company_name
            lead["industry"] = apollo_scanned.industry or "Unknown"
            lead["revenue_range"] = apollo_scanned.revenue_range
            lead["employee_count"] = apollo_scanned.employee_count
            lead["employee_range"] = apollo_scanned.employee_range
            lead["founded_year"] = apollo_scanned.founded_year
            lead["headquarters"] = apollo_scanned.headquarters
            lead["linkedin_url"] = apollo_scanned.linkedin_url
            lead["tech_stack"] = apollo_scanned.technologies or []
            lead["description"] = apollo_scanned.description
            lead["phone"] = apollo_scanned.phone
            lead["apollo_enriched"] = True
        else:
            logger.info(f"⚠️  Apollo: No data for {website_domain}, trying web enrichment...")
            lead["apollo_enriched"] = False

        if not apollo_scanned:
            enricher = WebEnricher()
            result = enricher.enrich(website_domain)
            if result:
                lead["apollo_scanned_url"] = _scanned_url_dict_from_web(
                    result, website_domain, default_company_name
                )
                lead["company_name"] = result.company_name or default_company_name
                lead["company_type"] = result.company_type or "Unknown"
                lead["industry"] = result.industry or "Unknown"
                lead["employee_count"] = result.employee_count
                lead["tech_stack"] = result.tech_stack or []
                lead["ecommerce_platform"] = result.ecommerce_platform
                lead["description"] = result.description
            else:
                lead["company_name"] = default_company_name
                lead["apollo_scanned_url"] = {
                    "company_name": default_company_name,
                    "domain": website_domain,
                    "revenue": None,
                    "revenue_range": None,
                    "employee_count": None,
                    "employee_range": None,
                    "industry": "Unknown",
                    "technologies": [],
                    "founded_year": None,
                    "headquarters": None,
                    "linkedin_url": None,
                    "phone": None,
                    "description": None,
                }

        # --- Contact company (when different): Apollo only ---
        if dual:
            apollo_contact = apollo_enricher.enrich_company(email_domain)
            if apollo_contact:
                logger.info(f"✅ Apollo enriched (contact): {email_domain} ({apollo_contact.company_name})")
                lead["apollo_contact_company"] = apollo_contact.to_dict()
                lead["apollo_dual_enrichment"] = True
            else:
                logger.info(f"⚠️  Apollo: No data for contact domain {email_domain}")

        # --- ICP (uses scanned-URL flat fields) ---
        from dataclasses import dataclass

        @dataclass
        class MockEnrichmentResult:
            domain: str
            company_name: str
            company_type: str
            industry: Optional[str]
            employee_count: Optional[int]
            tech_stack: List[str]
            ecommerce_platform: Optional[str]
            description: Optional[str]

        checker = ICPChecker()
        mock_result = MockEnrichmentResult(
            domain=website_domain,
            company_name=lead.get("company_name", ""),
            company_type=lead.get("company_type", "Unknown"),
            industry=lead.get("industry"),
            employee_count=lead.get("employee_count"),
            tech_stack=lead.get("tech_stack", []),
            ecommerce_platform=lead.get("ecommerce_platform"),
            description=lead.get("description"),
        )
        icp_result = checker.evaluate(
            enrichment=mock_result,
            employee_count=lead.get("employee_count"),
            revenue=lead.get("revenue"),
            sku_count=lead.get("sku_count"),
            linkedin_url=lead.get("linkedin_url"),
            job_title=lead.get("job_title"),
            has_meeting=lead.get("meeting_booked", False),
        )
        lead["icp_score"] = icp_result.score
        lead["priority"] = (
            icp_result.priority.value
            if hasattr(icp_result.priority, "value")
            else str(icp_result.priority)
        )
        lead["qualified"] = icp_result.is_qualified
        lead["disqualification_reasons"] = icp_result.reasons

    except Exception as e:
        logger.warning(f"Error enriching lead {website_domain}: {e}")
        lead["company_name"] = default_company_name
        lead["icp_score"] = 30
        lead["priority"] = "Medium"
        lead["qualified"] = True

    return lead


def sync_webflow_leads(days_back: int = 14, enrich: bool = True) -> Dict:
    """
    Fetch NEW leads from Webflow API (incremental sync).
    Only fetches submissions newer than last sync.
    Checks HubSpot to identify duplicates.
    
    Args:
        days_back: For initial sync, how many days back to fetch (default 14)
        enrich: Whether to enrich new leads with ICP data
    """
    if not WEBFLOW_API_TOKEN:
        return {"error": "WEBFLOW_API_TOKEN not configured", "new_leads": 0}
    
    logger.info("Starting incremental Webflow sync...")
    
    # Load existing leads
    pending, pushed, rejected, already_booked = load_leads()
    
    # Initialize HubSpot company manager for duplicate checking
    company_manager = None
    if HUBSPOT_AVAILABLE:
        try:
            company_manager = CompanyManager()
            logger.info("✅ HubSpot duplicate checking enabled")
        except Exception as e:
            logger.warning(f"⚠️  HubSpot not available for duplicate checking: {e}")
    
    # Get last sync timestamp
    last_sync = get_last_sync_timestamp()
    if last_sync:
        cutoff_date = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
        logger.info(f"Fetching submissions since: {last_sync}")
    else:
        # First sync - get last X days
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days_back)
        logger.info(f"First sync - fetching last {days_back} days")
    
    # Get all existing IDs to avoid duplicates
    existing_webflow_ids = set()
    existing_domains = set()
    
    for lead in pending + pushed + rejected + already_booked:
        if lead.get("webflow_id"):
            existing_webflow_ids.add(lead["webflow_id"])
        if lead.get("domain"):
            existing_domains.add(lead["domain"].lower())
    
    # Fetch forms
    forms = fetch_webflow_forms()
    
    new_leads = []
    skipped_old = 0
    skipped_duplicate = 0
    
    newest_submission_date = None
    
    for form in forms:
        form_id = form.get("id")
        form_name = form.get("displayName", "Unknown Form")
        
        submissions = fetch_webflow_submissions(form_id, limit=100)
        
        for submission in submissions:
            submission_date_str = submission.get("dateSubmitted")
            if not submission_date_str:
                continue
            
            # Parse submission date
            try:
                submission_date = datetime.fromisoformat(submission_date_str.replace('Z', '+00:00'))
            except:
                continue
            
            # Track newest submission
            if newest_submission_date is None or submission_date > newest_submission_date:
                newest_submission_date = submission_date
            
            # Skip if older than cutoff (already processed)
            if last_sync and submission_date <= cutoff_date:
                skipped_old += 1
                continue
            
            # Skip if already processed
            if submission.get("id") in existing_webflow_ids:
                skipped_duplicate += 1
                continue
            
            lead = parse_webflow_submission(submission, form_name)
            if not lead:
                continue
            
            # Skip if domain already exists in dashboard
            if lead.get("domain") and lead["domain"].lower() in existing_domains:
                skipped_duplicate += 1
                continue
            
            # Check if company already exists in HubSpot
            lead['in_hubspot'] = False
            lead['hubspot_company_id'] = None
            if company_manager and lead.get("domain"):
                try:
                    existing_company = company_manager.find_by_domain(lead["domain"])
                    if existing_company:
                        lead['in_hubspot'] = True
                        lead['hubspot_company_id'] = existing_company['id']
                        logger.info(f"🔍 HubSpot: {lead.get('domain')} → Already in CRM (ID: {existing_company['id']})")
                except Exception as e:
                    logger.warning(f"⚠️  Error checking HubSpot for {lead.get('domain')}: {e}")
            
            # Enrich the lead with ICP data
            if enrich:
                logger.info(f"Enriching new lead: {lead.get('domain')}")
                lead = enrich_lead_data(lead)
            
            # Do not add 2025 or blocklisted leads from Webflow sync
            if _is_2025_lead(lead) or _is_blocked_lead(lead):
                continue
            
            new_leads.append(lead)
            
            # Track to avoid duplicates within this batch
            if lead.get("domain"):
                existing_domains.add(lead["domain"].lower())
            existing_webflow_ids.add(submission.get("id"))
    
    # Add new leads to pending queue (newest first)
    if new_leads:
        new_leads.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        pending = new_leads + pending
        save_leads(pending, pushed, rejected, already_booked)
        logger.info(f"Added {len(new_leads)} new leads from Webflow API")
    
    # Update last sync timestamp
    if newest_submission_date:
        save_sync_timestamp(newest_submission_date.isoformat())
    
    return {
        "forms_checked": len(forms),
        "new_leads": len(new_leads),
        "skipped_old": skipped_old,
        "skipped_duplicate": skipped_duplicate,
        "total_pending": len(pending)
    }


class LeadData(BaseModel):
    """Lead data model"""
    id: str
    email: str
    website: str
    full_name: Optional[str] = None
    company_name: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    priority: str = "Medium"
    icp_score: int = 0
    is_agency: bool = False
    has_ecommerce: bool = False
    ecommerce_platform: Optional[str] = None
    employee_count: Optional[int] = None
    revenue: Optional[str] = None
    has_meeting: bool = False
    source: str = "webflow"
    created_at: str = ""
    status: str = "pending"
    hubspot_company_id: Optional[str] = None
    hubspot_contact_id: Optional[str] = None
    rejected_reason: Optional[str] = None
    rejected_at: Optional[str] = None


# Enhanced HTML Template
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zenyt Lead Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            /* Light Cursor-inspired theme with warm tones */
            --bg-primary: rgba(250, 248, 245, 0.92);
            --bg-secondary: rgba(255, 255, 255, 0.85);
            --bg-card: rgba(255, 255, 255, 0.95);
            --bg-hover: rgba(245, 243, 240, 0.95);
            --bg-glass: rgba(255, 255, 255, 0.75);
            
            /* Accent colors */
            --accent-primary: #6b5b95;
            --accent-secondary: #8b7cb5;
            --accent-success: #4a9d6b;
            --accent-warning: #c49a3d;
            --accent-danger: #c45a5a;
            --accent-info: #5a8dc4;
            --accent-green: #4a9d6b;
            --accent-purple: #8b7cb5;
            
            /* Text colors */
            --text-primary: #2d2d2d;
            --text-secondary: #5a5a5a;
            --text-muted: #8a8a8a;
            
            /* Borders */
            --border-color: rgba(0, 0, 0, 0.12);
            --border-subtle: rgba(0, 0, 0, 0.06);
            
            /* Priority colors */
            --priority-very-high: #4a9d6b;
            --priority-high: #6b5b95;
            --priority-medium: #c49a3d;
            --priority-low: #8a8a8a;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, sans-serif;
            min-height: 100vh;
            color: var(--text-primary);
            /* Mountain landscape wallpaper */
            background: url('https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=1920&q=80') center center / cover fixed;
        }
        
        body::before {
            content: '';
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: linear-gradient(180deg, 
                rgba(250, 248, 245, 0.3) 0%, 
                rgba(250, 248, 245, 0.5) 50%,
                rgba(250, 248, 245, 0.7) 100%);
            pointer-events: none;
            z-index: 0;
        }
        
        .container { 
            max-width: 1200px; margin: 0 auto; padding: 1.5rem 2rem; 
            position: relative; z-index: 1;
        }
        
        .main-panel {
            background: var(--bg-primary);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 16px;
            border: 1px solid var(--border-color);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            padding: 1.5rem;
        }
        
        header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 1.25rem; padding-bottom: 1rem; 
            border-bottom: 1px solid var(--border-subtle);
            flex-wrap: wrap; gap: 1rem;
        }
        
        .header-left { display: flex; align-items: center; gap: 1.5rem; }
        
        .logo { display: flex; align-items: center; gap: 0.6rem; }
        .logo-icon {
            width: 32px; height: 32px; 
            background: linear-gradient(135deg, var(--accent-purple), var(--accent-primary));
            border-radius: 8px; display: flex; align-items: center; justify-content: center;
            font-size: 0.9rem; font-weight: 700; color: white;
        }
        .logo h1 {
            font-size: 1.1rem; font-weight: 600; color: var(--text-primary);
        }
        
        .refresh-btn {
            padding: 0.5rem 0.9rem; 
            background: var(--accent-green);
            border: none; border-radius: 6px; color: white;
            font-family: inherit; font-size: 0.8rem; font-weight: 600;
            cursor: pointer; display: flex; align-items: center; gap: 0.4rem;
            transition: all 0.15s;
            box-shadow: 0 2px 8px rgba(74, 157, 107, 0.3);
        }
        .refresh-btn:hover { filter: brightness(1.05); transform: translateY(-1px); }
        .refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .refresh-btn.loading .refresh-icon { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        
        .last-refresh {
            font-size: 0.65rem; color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
        }
        
        .stats { display: flex; gap: 0.5rem; }
        .stat {
            text-align: center; padding: 0.4rem 0.9rem;
            background: var(--bg-glass); border-radius: 8px; 
            border: 1px solid var(--border-subtle);
            backdrop-filter: blur(10px);
        }
        .stat-value { font-size: 1.1rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
        .stat-label { font-size: 0.6rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.03em; }
        .stat.new .stat-value { color: var(--accent-green); }
        .stat.pending .stat-value { color: var(--accent-warning); }
        .stat.pushed .stat-value { color: var(--accent-success); }
        .stat.rejected .stat-value { color: var(--accent-danger); }
        .stat.already-booked .stat-value { color: #0ea5e9; }
        
        .tabs-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.25rem; flex-wrap: wrap; gap: 1rem; }
        .tabs { display: flex; gap: 0.2rem; background: var(--bg-glass); padding: 0.2rem; border-radius: 8px; border: 1px solid var(--border-subtle); }
        .tab {
            padding: 0.45rem 0.9rem; background: transparent;
            border: none; border-radius: 6px;
            color: var(--text-muted); cursor: pointer; transition: all 0.15s;
            font-family: inherit; font-size: 0.75rem; font-weight: 500;
        }
        .tab:hover { background: rgba(255,255,255,0.5); color: var(--text-primary); }
        .tab.active { background: white; color: var(--text-primary); box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
        .tab[data-tab="new"].active { color: var(--accent-green); }
        .tab[data-tab="pushed"].active { color: var(--accent-success); }
        .tab.rejected-tab.active { color: var(--accent-danger); }
        .tab.already-booked-tab.active { color: #0ea5e9; }
        
        .sort-controls { display: flex; gap: 0.5rem; align-items: center; }
        .sort-controls label { font-size: 0.8rem; color: var(--text-muted); }
        .sort-controls select {
            padding: 0.5rem 1rem; background: var(--bg-secondary);
            border: 1px solid var(--border-color); border-radius: 8px;
            color: var(--text-primary); font-family: inherit; font-size: 0.85rem; cursor: pointer;
        }
        .sort-controls select:focus { outline: none; border-color: var(--accent-primary); }
        
        .lead-date {
            font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;
            font-family: 'JetBrains Mono', monospace;
        }
        
        .research-link {
            display: inline-flex; align-items: center; gap: 0.25rem;
            padding: 0.4rem 0.75rem; background: var(--bg-secondary);
            border: 1px solid var(--border-color); border-radius: 6px;
            color: var(--accent-info); text-decoration: none;
            font-size: 0.8rem; font-family: 'JetBrains Mono', monospace;
            transition: all 0.2s;
        }
        .research-link:hover {
            background: var(--bg-hover); border-color: var(--accent-info);
            transform: translateY(-1px);
        }
        .research-link.email-domain {
            border-color: var(--accent-warning);
            color: var(--accent-warning);
        }
        .research-link.email-domain:hover {
            border-color: var(--accent-warning);
        }
        .research-link.linkedin {
            border-color: #0a66c2;
            color: #0a66c2;
        }
        .research-link.linkedin:hover {
            background: rgba(10, 102, 194, 0.1);
        }
        
        .lead-grid { display: grid; gap: 0.75rem; }
        
        .lead-card {
            background: var(--bg-card); 
            border: 1px solid var(--border-subtle);
            border-radius: 10px; padding: 1rem 1.25rem; 
            transition: all 0.2s;
            position: relative; overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        .lead-card::before {
            content: ''; position: absolute; top: 0; left: 0; width: 3px; height: 100%;
        }
        .lead-card.priority-very-high::before { background: var(--priority-very-high); }
        .lead-card.priority-high::before { background: var(--priority-high); }
        .lead-card.priority-medium::before { background: var(--priority-medium); }
        .lead-card.priority-low::before { background: var(--priority-low); }
        .lead-card.rejected-card { opacity: 0.6; border-color: var(--accent-danger); }
        .lead-card.rejected-card::before { background: var(--accent-danger); }
        .lead-card.already-booked-card { opacity: 0.85; border-color: #0ea5e9; }
        .lead-card.already-booked-card::before { background: #0ea5e9; }
        
        .lead-card:hover {
            border-color: var(--border-color); 
            box-shadow: 0 4px 12px rgba(0,0,0,0.08);
            transform: translateY(-1px);
        }
        
        .lead-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.6rem; }
        .lead-info h3 { font-size: 0.95rem; font-weight: 600; margin-bottom: 0.15rem; color: var(--text-primary); }
        .lead-info .email { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: var(--accent-primary); }
        
        .lead-badges { display: flex; gap: 0.35rem; flex-wrap: wrap; }
        .badge {
            padding: 0.2rem 0.45rem; border-radius: 4px; font-size: 0.6rem;
            font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em;
        }
        .badge.priority { color: white; }
        .badge.priority-very-high { background: var(--priority-very-high); }
        .badge.priority-high { background: var(--priority-high); }
        .badge.priority-medium { background: var(--priority-medium); color: white; }
        .badge.priority-low { background: var(--priority-low); color: white; }
        .badge.agency { background: rgba(196, 154, 61, 0.15); color: var(--accent-warning); }
        .badge.ecommerce { background: rgba(74, 157, 107, 0.15); color: var(--accent-green); }
        .badge.meeting { background: rgba(139, 124, 181, 0.15); color: var(--accent-purple); }
        .badge.apollo { background: linear-gradient(135deg, rgba(139, 92, 246, 0.15), rgba(167, 139, 250, 0.1)); color: #8b5cf6; border: 1px solid rgba(139, 92, 246, 0.3); }
        
        .company-details-section {
            margin-bottom: 1rem; padding: 0; background: var(--bg-secondary); 
            border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden;
        }
        .company-details-toggle {
            width: 100%; padding: 0.75rem; background: transparent; border: none;
            color: var(--text-primary); font-size: 0.85rem; font-weight: 600;
            cursor: pointer; display: flex; align-items: center; justify-content: space-between;
            transition: background 0.15s;
        }
        .company-details-toggle:hover { background: rgba(139, 92, 246, 0.05); }
        .company-details-content {
            padding: 1rem; border-top: 1px solid var(--border-color);
            display: none; animation: slideDown 0.2s ease-out;
        }
        .company-details-content.expanded { display: block; }
        .apollo-data-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem; margin-bottom: 1rem;
        }
        .apollo-data-item {
            display: flex; flex-direction: column; gap: 0.25rem;
        }
        .apollo-data-label {
            font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em;
            color: var(--text-secondary); font-weight: 600;
        }
        .apollo-data-value {
            color: var(--text-primary); font-size: 0.9rem; font-weight: 500;
        }
        .tech-stack-list {
            display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem;
        }
        .tech-badge {
            padding: 0.2rem 0.5rem; background: rgba(139, 92, 246, 0.1);
            border: 1px solid rgba(139, 92, 246, 0.2); border-radius: 4px;
            font-size: 0.7rem; color: #8b5cf6;
        }
        .apollo-demo-url-block {
            font-size: 0.9rem;
        }
        @keyframes slideDown {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .hs-badge {
            display: inline-flex; align-items: center; gap: 0.25rem;
            padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.65rem;
            font-weight: 600; text-decoration: none; transition: all 0.15s;
            cursor: pointer;
        }
        .hs-badge:hover { transform: translateY(-1px); }
        .hs-badge.new-lead {
            background: rgba(74, 157, 107, 0.12);
            color: var(--accent-green); border: 1px solid rgba(74, 157, 107, 0.3);
        }
        .hs-badge.in-hubspot {
            background: rgba(107, 91, 149, 0.12);
            color: var(--accent-primary); border: 1px solid rgba(107, 91, 149, 0.3);
        }
        .hs-badge.in-hubspot:hover { background: rgba(107, 91, 149, 0.2); }
        .hs-badge.contact-exists {
            background: rgba(90, 141, 196, 0.12);
            color: var(--accent-info); border: 1px solid rgba(90, 141, 196, 0.3);
        }
        .hs-badge.contact-exists:hover { background: rgba(90, 141, 196, 0.2); }
        .hs-badge.not-checked {
            background: rgba(138, 138, 138, 0.1);
            color: var(--text-muted); border: 1px solid rgba(138, 138, 138, 0.2);
        }
        .hs-badge .arrow { font-size: 0.7rem; }
        
        .lead-details {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 0.6rem; margin-bottom: 0.75rem; padding: 0.6rem;
            background: rgba(0,0,0,0.02); border-radius: 6px;
            border: 1px solid var(--border-subtle);
        }
        .detail { display: flex; flex-direction: column; gap: 0.1rem; }
        .detail-label { font-size: 0.6rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.02em; }
        .detail-value { font-size: 0.75rem; color: var(--text-primary); }
        .detail-value a { color: var(--accent-primary); text-decoration: none; }
        .detail-value a:hover { text-decoration: underline; }
        
        .icp-score { display: flex; align-items: center; gap: 0.5rem; }
        .score-bar { flex: 1; height: 6px; background: var(--bg-primary); border-radius: 3px; overflow: hidden; }
        .score-fill { height: 100%; border-radius: 3px; transition: width 0.5s ease; }
        .score-fill.high { background: var(--accent-success); }
        .score-fill.medium { background: var(--accent-warning); }
        .score-fill.low { background: var(--accent-danger); }
        .score-value { font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; font-weight: 600; min-width: 3rem; text-align: right; }
        
        .icp-breakdown {
            display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem;
            padding: 0.75rem; background: var(--bg-primary); border-radius: 8px; margin-top: 0.5rem;
        }
        .icp-item { text-align: center; }
        .icp-item-label { font-size: 0.65rem; color: var(--text-muted); text-transform: uppercase; }
        .icp-item-value { font-size: 0.8rem; font-weight: 600; }
        .icp-item-value.positive { color: var(--accent-success); }
        .icp-item-value.negative { color: var(--accent-danger); }
        .icp-item-value.neutral { color: var(--text-secondary); }
        
        .lead-actions { display: flex; gap: 0.4rem; justify-content: flex-end; }
        
        .btn {
            padding: 0.4rem 0.8rem; border-radius: 6px; font-family: inherit;
            font-size: 0.75rem; font-weight: 500; cursor: pointer;
            transition: all 0.15s; border: none; display: flex; align-items: center; gap: 0.35rem;
        }
        .btn-primary { background: var(--accent-primary); color: white; box-shadow: 0 2px 6px rgba(107, 91, 149, 0.25); }
        .btn-primary:hover { filter: brightness(1.1); transform: translateY(-1px); }
        .btn-success { background: var(--accent-green); color: white; box-shadow: 0 2px 6px rgba(74, 157, 107, 0.25); }
        .btn-success:hover { filter: brightness(1.05); transform: translateY(-1px); }
        .btn-danger { background: transparent; color: var(--accent-danger); border: 1px solid var(--border-color); }
        .btn-danger:hover { background: rgba(196, 90, 90, 0.08); border-color: var(--accent-danger); }
        .btn-secondary { background: white; color: var(--text-secondary); border: 1px solid var(--border-color); }
        .btn-secondary:hover { background: var(--bg-hover); color: var(--text-primary); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        
        .empty-state {
            text-align: center; padding: 4rem 2rem;
            background: var(--bg-card); border-radius: 16px; border: 1px dashed var(--border-color);
        }
        .empty-state h3 { font-size: 1.5rem; margin-bottom: 0.5rem; }
        .empty-state p { color: var(--text-muted); }
        
        .toast {
            position: fixed; bottom: 2rem; right: 2rem;
            padding: 1rem 1.5rem; background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: 12px; display: flex; align-items: center; gap: 0.75rem;
            transform: translateY(100px); opacity: 0; transition: all 0.3s; z-index: 1000;
        }
        .toast.show { transform: translateY(0); opacity: 1; }
        .toast.success { border-color: var(--accent-success); }
        .toast.error { border-color: var(--accent-danger); }
        
        .add-lead-form {
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: 16px; padding: 1.5rem; margin-bottom: 2rem;
        }
        .form-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 1rem; }
        .form-group { display: flex; flex-direction: column; gap: 0.5rem; }
        .form-group label { font-size: 0.8rem; color: var(--text-secondary); }
        .form-group input, .form-group select {
            padding: 0.75rem 1rem; background: var(--bg-secondary);
            border: 1px solid var(--border-color); border-radius: 8px;
            color: var(--text-primary); font-family: inherit; font-size: 0.9rem;
        }
        .form-group input:focus, .form-group select:focus { outline: none; border-color: var(--accent-primary); }
        
        .rejected-reason {
            background: rgba(239, 68, 68, 0.1); border: 1px solid var(--accent-danger);
            border-radius: 8px; padding: 0.75rem; margin-top: 0.5rem;
            font-size: 0.85rem; color: var(--accent-danger);
        }
        
        .pushed-badge {
            background: rgba(16, 185, 129, 0.2); color: var(--accent-success);
            padding: 0.5rem 1rem; border-radius: 8px; font-size: 0.85rem;
            display: flex; align-items: center; gap: 0.5rem;
        }
        
        .modal-overlay {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.7); display: none; align-items: center;
            justify-content: center; z-index: 1000; overflow-y: auto; padding: 2rem;
        }
        .modal-overlay.show { display: flex; }
        .modal {
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: 16px; padding: 2rem; max-width: 700px; width: 100%;
            max-height: 90vh; overflow-y: auto;
        }
        .modal h3 { margin-bottom: 1rem; }
        .modal textarea {
            width: 100%; padding: 0.75rem; background: var(--bg-secondary);
            border: 1px solid var(--border-color); border-radius: 8px;
            color: var(--text-primary); font-family: inherit; resize: vertical;
            min-height: 100px; margin-bottom: 1rem;
        }
        .modal-actions { display: flex; gap: 0.75rem; justify-content: flex-end; margin-top: 1.5rem; }
        
        .preview-section {
            background: var(--bg-secondary); border-radius: 8px; padding: 1rem;
            margin-bottom: 1rem;
        }
        .preview-section h4 {
            font-size: 0.9rem; color: var(--accent-info); margin-bottom: 0.75rem;
            display: flex; align-items: center; gap: 0.5rem;
        }
        .preview-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 0.75rem;
        }
        .preview-field { display: flex; flex-direction: column; gap: 0.25rem; }
        .preview-field label {
            font-size: 0.7rem; color: var(--text-muted);
            text-transform: uppercase; letter-spacing: 0.05em;
        }
        .preview-field input, .preview-field select {
            padding: 0.5rem 0.75rem; background: var(--bg-primary);
            border: 1px solid var(--border-color); border-radius: 6px;
            color: var(--text-primary); font-family: inherit; font-size: 0.85rem;
        }
        .preview-field input:focus, .preview-field select:focus {
            outline: none; border-color: var(--accent-primary);
        }
        
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .loading { animation: pulse 1.5s infinite; }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
    <div class="container">
        <div class="main-panel">
        <header>
            <div class="header-left">
                <div class="logo">
                    <div class="logo-icon">Z</div>
                    <h1>Zenyt Leads</h1>
                </div>
                <div>
                    <button class="refresh-btn" id="refresh-btn" onclick="refreshHubSpotStatus()">
                        <span class="refresh-icon">🔄</span> Refresh HubSpot Status
                    </button>
                    <div class="last-refresh" id="last-refresh">Not checked yet</div>
                </div>
            </div>
            <div class="stats">
                <div class="stat new">
                    <div class="stat-value" id="new-count">0</div>
                    <div class="stat-label">New Today</div>
                </div>
                <div class="stat pending">
                    <div class="stat-value" id="pending-count">0</div>
                    <div class="stat-label">Pending</div>
                </div>
                <div class="stat pushed">
                    <div class="stat-value" id="pushed-count">0</div>
                    <div class="stat-label">Pushed</div>
                </div>
                <div class="stat rejected">
                    <div class="stat-value" id="rejected-count">0</div>
                    <div class="stat-label">Rejected</div>
                </div>
                <div class="stat already-booked">
                    <div class="stat-value" id="already-booked-count">0</div>
                    <div class="stat-label">Already booked</div>
                </div>
            </div>
        </header>
        
        <div class="tabs-row">
            <div class="tabs">
                <button class="tab active" data-tab="new">🆕 New Today</button>
                <button class="tab" data-tab="pending">📋 All Pending</button>
                <button class="tab" data-tab="pushed">✅ Pushed</button>
                <button class="tab rejected-tab" data-tab="rejected">❌ Rejected</button>
                <button class="tab already-booked-tab" data-tab="already_booked">📅 Already booked</button>
                <button class="tab" data-tab="analytics" style="margin-left: auto; background: linear-gradient(135deg, #3b82f6, #2563eb); color: white;">📈 Analytics</button>
                <button class="tab" data-tab="post-performance" style="background: linear-gradient(135deg, #6a5b95, #8b7cb5); color: white;">📊 Post Performance</button>
            </div>
            <div class="sort-controls">
                <label>Sort by:</label>
                <select id="sort-select">
                    <option value="date-desc">📅 Newest First</option>
                    <option value="date-asc">📅 Oldest First</option>
                    <option value="icp-desc">⭐ ICP Score (High→Low)</option>
                    <option value="icp-asc">⭐ ICP Score (Low→High)</option>
                    <option value="priority">🎯 Priority</option>
                </select>
            </div>
        </div>
        
        <div class="lead-grid" id="lead-list"></div>
        
        <!-- Post Performance View -->
        <div id="post-performance-view" style="display: none; padding: 2rem;">
            <div id="post-performance-grid"></div>
        </div>
        
        <!-- Analytics Dashboard View -->
        <div id="analytics-view" style="display: none; padding: 2rem;">
            <div style="margin-bottom: 2rem;">
                <h2 style="color: var(--text-primary); margin: 0 0 0.5rem 0;">📈 Analytics Overview</h2>
                <p style="color: var(--text-muted); margin: 0; font-size: 0.9rem;" id="analytics-last-updated">Last updated: --</p>
            </div>
            
            <!-- Total Metrics Cards -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem;">
                <div style="background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(37, 99, 235, 0.05)); border: 1px solid rgba(59, 130, 246, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">Total Leads</div>
                    <div style="font-size: 2rem; font-weight: 700; color: #3b82f6;" id="total-leads">0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;"><span id="organic-count">0</span> Organic • <span id="post-sourced-count">0</span> From Posts</div>
                </div>
                <div style="background: linear-gradient(135deg, rgba(168, 85, 247, 0.1), rgba(147, 51, 234, 0.05)); border: 1px solid rgba(168, 85, 247, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">ICP Fit Leads</div>
                    <div style="font-size: 2rem; font-weight: 700; color: #a855f7;" id="total-icp">0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;"><span id="icp-rate">0</span>% of total</div>
                </div>
                <div style="background: linear-gradient(135deg, rgba(52, 211, 153, 0.1), rgba(16, 185, 129, 0.05)); border: 1px solid rgba(52, 211, 153, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">Pushed to CRM</div>
                    <div style="font-size: 2rem; font-weight: 700; color: var(--accent-success);" id="total-crm">0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;"><span id="crm-rate">0</span>% push rate</div>
                </div>
                <div style="background: linear-gradient(135deg, rgba(14, 165, 233, 0.1), rgba(14, 165, 233, 0.05)); border: 1px solid rgba(14, 165, 233, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">Already booked</div>
                    <div style="font-size: 2rem; font-weight: 700; color: #0ea5e9;" id="total-already-booked">0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">Meeting in Calendly etc.</div>
                </div>
                <div style="background: linear-gradient(135deg, rgba(245, 158, 11, 0.1), rgba(245, 158, 11, 0.05)); border: 1px solid rgba(245, 158, 11, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">Pending</div>
                    <div style="font-size: 2rem; font-weight: 700; color: #f59e0b;" id="total-pending">0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">Awaiting review (Oct 2025+)</div>
                </div>
                <div style="background: linear-gradient(135deg, rgba(239, 68, 68, 0.1), rgba(220, 38, 38, 0.05)); border: 1px solid rgba(239, 68, 68, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">Rejected</div>
                    <div style="font-size: 2rem; font-weight: 700; color: #ef4444;" id="total-rejected">0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">Filtered out (Oct 2025+)</div>
                </div>
                <div style="background: linear-gradient(135deg, rgba(251, 146, 60, 0.1), rgba(249, 115, 22, 0.05)); border: 1px solid rgba(251, 146, 60, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">Total Investment</div>
                    <div style="font-size: 2rem; font-weight: 700; color: #fb923c;" id="total-cost">$0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">Across all posts</div>
                </div>
                <div style="background: linear-gradient(135deg, rgba(239, 68, 68, 0.1), rgba(220, 38, 38, 0.05)); border: 1px solid rgba(239, 68, 68, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">Total Engagement</div>
                    <div style="font-size: 2rem; font-weight: 700; color: #ef4444;" id="total-reactions">0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">❤️ Reactions + 💬 Comments</div>
                </div>
                <div style="background: linear-gradient(135deg, rgba(34, 197, 94, 0.1), rgba(22, 163, 74, 0.05)); border: 1px solid rgba(34, 197, 94, 0.2); border-radius: 12px; padding: 1.5rem;">
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 0.5px;">Wins</div>
                    <div style="font-size: 2rem; font-weight: 700; color: #22c55e;" id="total-wins">0</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.5rem;">🏆 Closed Won</div>
                </div>
            </div>
            
            <!-- Period Toggle & Export -->
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
                <div style="display: flex; gap: 0.5rem; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 0.25rem;">
                    <button id="toggle-monthly" class="period-toggle active" style="padding: 0.5rem 1rem; border: none; background: var(--accent-primary); color: white; border-radius: 6px; cursor: pointer; font-family: 'Space Grotesk', sans-serif; font-weight: 500; font-size: 0.9rem;">📅 Monthly</button>
                    <button id="toggle-weekly" class="period-toggle" style="padding: 0.5rem 1rem; border: none; background: transparent; color: var(--text-secondary); border-radius: 6px; cursor: pointer; font-family: 'Space Grotesk', sans-serif; font-weight: 500; font-size: 0.9rem;">📆 Weekly</button>
                </div>
                <div style="display: flex; gap: 0.5rem;">
                    <button id="export-csv-btn" style="padding: 0.5rem 1rem; border: 1px solid var(--border); background: var(--bg-card); color: var(--text-primary); border-radius: 6px; cursor: pointer; font-family: 'Space Grotesk', sans-serif; font-weight: 500; font-size: 0.9rem;">📥 Export CSV</button>
                </div>
            </div>
            
            <!-- Charts Row -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 1.5rem; margin-bottom: 2rem;">
                <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem;">
                    <h3 style="margin: 0 0 1rem 0; color: var(--text-primary); font-size: 1.1rem;">Traffic Sources</h3>
                    <canvas id="traffic-chart" style="max-height: 300px;"></canvas>
                </div>
                <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem;">
                    <h3 style="margin: 0 0 1rem 0; color: var(--text-primary); font-size: 1.1rem;">Conversion Funnel</h3>
                    <canvas id="funnel-chart" style="max-height: 300px;"></canvas>
                </div>
            </div>
            
            <!-- Period Breakdown Chart -->
            <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 2rem;">
                <h3 style="margin: 0 0 1rem 0; color: var(--text-primary); font-size: 1.1rem;" id="breakdown-chart-title">📊 Monthly Breakdown</h3>
                <canvas id="period-chart" style="max-height: 400px;"></canvas>
            </div>
            
            <!-- Period Data Table -->
            <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem;">
                <h3 style="margin: 0 0 1rem 0; color: var(--text-primary); font-size: 1.1rem;" id="breakdown-table-title">📅 Monthly Details</h3>
                <div id="period-table" style="overflow-x: auto;"></div>
            </div>
        </div>
    </div>
    
    <!-- Reject Modal -->
    <div class="modal-overlay" id="reject-modal">
        <div class="modal">
            <h3>❌ Reject Lead</h3>
            <p style="color: var(--text-secondary); margin-bottom: 1rem;">Why are you rejecting this lead?</p>
            <textarea id="reject-reason" placeholder="e.g., Too small, Not ICP, Competitor, Test submission..."></textarea>
            <div class="modal-actions">
                <button class="btn btn-secondary" onclick="closeRejectModal()">Cancel</button>
                <button class="btn btn-danger" onclick="confirmReject()">Reject Lead</button>
            </div>
        </div>
    </div>
    
    <!-- Push to HubSpot Preview Modal -->
    <div class="modal-overlay" id="push-modal">
        <div class="modal" style="max-width: 800px;">
            <h3>🚀 Push to HubSpot - Preview & Edit</h3>
            <p style="color: var(--text-secondary); margin-bottom: 1.5rem;">Review and edit the data before pushing to HubSpot</p>
            
            <div class="preview-section">
                <h4>🏢 Company Information</h4>
                <div class="preview-grid">
                    <div class="preview-field">
                        <label>Company Domain *</label>
                        <input type="text" id="push-company-domain" placeholder="e.g. acme.com or leave blank to use email domain">
                    </div>
                    <div class="preview-field">
                        <label>Company Name *</label>
                        <input type="text" id="push-company-name">
                    </div>
                    <div class="preview-field">
                        <label>Company Owner</label>
                        <select id="push-company-owner">
                            <option value="">-- No Owner --</option>
                            <option value="159554519">Antoine Giacomini</option>
                            <option value="161038194">Arthur Pentecoste</option>
                            <option value="159554521">Guillaume Duvaux</option>
                            <option value="159928305">Adam Azoulay</option>
                            <option value="159502850">Raphael Rozenblum</option>
                            <option value="159554522">Chris Gomes Muffat</option>
                        </select>
                    </div>
                    <div class="preview-field">
                        <label>Priority</label>
                        <select id="push-priority">
                            <option value="Very high">Very high</option>
                            <option value="High">High</option>
                            <option value="Medium">Medium</option>
                            <option value="Low">Low</option>
                        </select>
                    </div>
                    <div class="preview-field">
                        <label>Demo Request</label>
                        <select id="push-demo-request">
                            <option value="true" selected>Yes</option>
                            <option value="false">No</option>
                        </select>
                    </div>
                    <div class="preview-field">
                        <label>AI Finding Status</label>
                        <select id="push-ai-status">
                            <option value="">Blank</option>
                            <option value="To Do">To Do</option>
                            <option value="Available">Available</option>
                            <option value="Need more results">Need more results</option>
                            <option value="Scraping launched">Scraping launched</option>
                            <option value="Done">Done</option>
                            <option value="Done after more results">Done after more results</option>
                            <option value="Failed">Failed</option>
                        </select>
                    </div>
                </div>
            </div>
            
            <div class="preview-section">
                <h4>👤 Contact Information</h4>
                <div class="preview-grid">
                    <div class="preview-field">
                        <label>Email *</label>
                        <input type="email" id="push-contact-email" readonly style="background: var(--bg-hover);">
                    </div>
                    <div class="preview-field">
                        <label>First Name</label>
                        <input type="text" id="push-contact-firstname">
                    </div>
                    <div class="preview-field">
                        <label>Last Name</label>
                        <input type="text" id="push-contact-lastname">
                    </div>
                    <div class="preview-field">
                        <label>Contact Owner</label>
                        <select id="push-contact-owner">
                            <option value="">-- No Owner --</option>
                            <option value="159554519">Antoine Giacomini</option>
                            <option value="161038194">Arthur Pentecoste</option>
                            <option value="159554521">Guillaume Duvaux</option>
                            <option value="159928305">Adam Azoulay</option>
                            <option value="159502850">Raphael Rozenblum</option>
                            <option value="159554522">Chris Gomes Muffat</option>
                        </select>
                    </div>
                </div>
            </div>
            
            <div class="modal-actions">
                <button class="btn btn-secondary" onclick="closePushModal()">Cancel</button>
                <button class="btn btn-success" id="confirm-push-btn" onclick="confirmPush()">
                    <span>🚀</span> Push to HubSpot
                </button>
            </div>
        </div>
    </div>
    
    <div class="toast" id="toast">
        <span id="toast-icon">✅</span>
        <span id="toast-message">Success!</span>
    </div>
    
    <script>
        let leads = { pending: [], pushed: [], rejected: [], already_booked: [] };
        let currentTab = 'new';  // Default to "New Today" view
        let currentSort = 'date-desc';
        let rejectingLeadId = null;
        let pushingLeadId = null;
        let alreadyBookingLeadId = null;
        let hubspotCache = {};
        
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                currentTab = tab.dataset.tab;
                
                // Show/hide appropriate view
                if (currentTab === 'analytics') {
                    document.getElementById('lead-list').style.display = 'none';
                    document.getElementById('post-performance-view').style.display = 'none';
                    document.getElementById('analytics-view').style.display = 'block';
                    document.querySelector('.sort-controls').style.display = 'none';
                    loadAnalytics();
                } else if (currentTab === 'post-performance') {
                    document.getElementById('lead-list').style.display = 'none';
                    document.getElementById('post-performance-view').style.display = 'block';
                    document.getElementById('analytics-view').style.display = 'none';
                    document.querySelector('.sort-controls').style.display = 'none';
                    loadPostPerformance();
                } else {
                    document.getElementById('lead-list').style.display = 'grid';
                    document.getElementById('post-performance-view').style.display = 'none';
                    document.getElementById('analytics-view').style.display = 'none';
                    document.querySelector('.sort-controls').style.display = 'flex';
                renderLeads();
                }
            });
        });
        
        document.getElementById('sort-select').addEventListener('change', (e) => {
            currentSort = e.target.value;
            renderLeads();
        });
        
        // Period toggle for analytics (weekly/monthly)
        let currentPeriod = 'monthly';
        let analyticsData = null;
        
        document.getElementById('toggle-monthly')?.addEventListener('click', () => {
            currentPeriod = 'monthly';
            document.getElementById('toggle-monthly').style.background = 'var(--accent-primary)';
            document.getElementById('toggle-monthly').style.color = 'white';
            document.getElementById('toggle-weekly').style.background = 'transparent';
            document.getElementById('toggle-weekly').style.color = 'var(--text-secondary)';
            renderPeriodData();
        });
        
        document.getElementById('toggle-weekly')?.addEventListener('click', () => {
            currentPeriod = 'weekly';
            document.getElementById('toggle-weekly').style.background = 'var(--accent-primary)';
            document.getElementById('toggle-weekly').style.color = 'white';
            document.getElementById('toggle-monthly').style.background = 'transparent';
            document.getElementById('toggle-monthly').style.color = 'var(--text-secondary)';
            renderPeriodData();
        });
        
        // CSV Export
        document.getElementById('export-csv-btn')?.addEventListener('click', async () => {
            try {
                const response = await fetch(`/api/analytics/export?period=${currentPeriod}`);
                if (!response.ok) throw new Error('Export failed');
                
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `zenyt_analytics_${currentPeriod}_${new Date().toISOString().split('T')[0]}.csv`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                a.remove();
                showToast('Analytics exported successfully!', 'success');
            } catch (err) {
                console.error('Export error:', err);
                showToast('Error exporting analytics', 'error');
            }
        });
        
        function sortLeads(leadsArray) {
            const sorted = [...leadsArray];
            switch(currentSort) {
                case 'date-desc':
                    return sorted.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
                case 'date-asc':
                    return sorted.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
                case 'icp-desc':
                    return sorted.sort((a, b) => b.icp_score - a.icp_score);
                case 'icp-asc':
                    return sorted.sort((a, b) => a.icp_score - b.icp_score);
                case 'priority':
                    const priorityOrder = {'Very High': 0, 'High': 1, 'Medium': 2, 'Low': 3};
                    return sorted.sort((a, b) => (priorityOrder[a.priority] || 4) - (priorityOrder[b.priority] || 4));
                default:
                    return sorted;
            }
        }
        
        function formatDate(dateStr) {
            if (!dateStr) return 'Unknown date';
            const date = new Date(dateStr);
            if (isNaN(date.getTime())) return 'Unknown date';
            
            const options = { 
                month: 'short', 
                day: 'numeric', 
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            };
            return date.toLocaleDateString('en-US', options);
        }
        
        function getEmailDomain(email) {
            if (!email || !email.includes('@')) return '';
            return email.split('@')[1].toLowerCase();
        }
        
        function normalizeWebsiteDomain(website) {
            if (!website) return '';
            let domain = website.toLowerCase();
            domain = domain.replace(/^https?:\\/\\//, '');
            domain = domain.replace(/^www\\./, '');
            domain = domain.split('/')[0];
            return domain;
        }
        
        function getScannedForDisplay(lead) {
            if (lead.apollo_scanned_url) return lead.apollo_scanned_url;
            if (!lead.apollo_enriched) return null;
            return {
                company_name: lead.company_name,
                domain: lead.domain || normalizeWebsiteDomain(lead.website),
                revenue_range: lead.revenue_range || lead.revenue,
                employee_count: lead.employee_count,
                employee_range: lead.employee_range,
                industry: lead.industry,
                linkedin_url: lead.linkedin_url,
                founded_year: lead.founded_year,
                headquarters: lead.headquarters,
                phone: lead.phone,
                description: lead.description,
                technologies: lead.tech_stack || []
            };
        }
        
        function getContactForDisplay(lead) {
            return lead.apollo_contact_company || null;
        }
        
        function toggleCompanyDetails(leadId, suffix) {
            const id = (suffix && suffix !== 'null') ? `details-${leadId}-${suffix}` : `details-${leadId}`;
            const toggleId = (suffix && suffix !== 'null') ? `toggle-${leadId}-${suffix}` : `toggle-${leadId}`;
            const content = document.getElementById(id);
            const icon = document.getElementById(toggleId);
            if (!content || !icon) return;
            if (content.classList.contains('expanded')) {
                content.classList.remove('expanded');
                icon.textContent = '▼';
            } else {
                content.classList.add('expanded');
                icon.textContent = '▲';
            }
        }
        
        function renderApolloBlocks(lead) {
            const scanned = getScannedForDisplay(lead);
            const contact = getContactForDisplay(lead);
            const dual = !!(lead.apollo_dual_enrichment && contact && scanned);
            let html = '';
            const fmt = (v) => v != null && v !== '' ? v : '?';
            const fmtNum = (v) => (v != null && typeof v === 'number') ? v.toLocaleString() : fmt(v);
            const link = (url) => url ? `<a href="${url}" target="_blank" style="color: #8b5cf6; font-weight: 600;">View →</a>` : '<span>?</span>';
            const grid = (d) => `
                <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; font-size: 0.9rem;">
                    <div><strong style="color: var(--text-secondary);">💰 Revenue:</strong> <span style="color: #8b5cf6; font-weight: 600;">${fmt(d.revenue_range || d.revenue)}</span></div>
                    <div><strong style="color: var(--text-secondary);">👥 Employees:</strong> <span style="color: #8b5cf6; font-weight: 600;">${fmtNum(d.employee_count)}</span></div>
                    <div><strong style="color: var(--text-secondary);">🏢 Industry:</strong> <span style="color: var(--text-primary);">${fmt(d.industry)}</span></div>
                    <div><strong style="color: var(--text-secondary);">🔗 LinkedIn:</strong> ${link(d.linkedin_url)}</div>
                </div>`;
            if (dual) {
                html += `<div style="margin-bottom: 1rem; padding: 1rem; background: linear-gradient(135deg, rgba(139, 92, 246, 0.1), rgba(168, 85, 247, 0.05)); border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 8px;">
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem;">
                        <span class="detail-label" style="color: #8b5cf6; font-weight: 600;">✨ Apollo – ${contact.company_name || contact.domain} (where they work)</span>
                    </div>${grid(contact)}</div>`;
                html += `<div class="apollo-demo-url-block" style="margin-bottom: 1rem; padding: 0.75rem 1rem; background: rgba(100, 116, 139, 0.08); border-left: 4px solid var(--accent-primary); border-radius: 0 8px 8px 0;">
                    <div style="margin-bottom: 0.5rem;"><span class="detail-label" style="color: var(--text-secondary); font-weight: 600;">Demo requested on – ${scanned.company_name || scanned.domain}</span></div>${grid(scanned)}</div>`;
            } else if (scanned) {
                html += `<div style="margin-bottom: 1rem; padding: 1rem; background: linear-gradient(135deg, rgba(139, 92, 246, 0.1), rgba(168, 85, 247, 0.05)); border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 8px;">
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem;">
                        <span class="detail-label" style="color: #8b5cf6; font-weight: 600;">✨ Apollo Enrichment Data</span>
                    </div>${grid(scanned)}</div>`;
            }
            if (!scanned) return html;
            const collapsible = (d, title, suf, showButton) => {
                const lid = suf ? `${lead.id}-${suf}` : lead.id;
                const sufArg = suf ? `'${suf}'` : 'null';
                const hasExtra = d.founded_year || d.headquarters || d.phone || d.description || (d.technologies && d.technologies.length);
                let inner = '';
                inner += '<div class="apollo-data-grid">';
                if (d.founded_year) inner += `<div class="apollo-data-item"><span class="apollo-data-label">📅 Founded</span><span class="apollo-data-value">${d.founded_year}</span></div>`;
                if (d.headquarters) inner += `<div class="apollo-data-item"><span class="apollo-data-label">📍 Headquarters</span><span class="apollo-data-value">${d.headquarters}</span></div>`;
                if (d.phone) inner += `<div class="apollo-data-item"><span class="apollo-data-label">☎️ Phone</span><span class="apollo-data-value">${d.phone}</span></div>`;
                if (d.revenue_range) inner += `<div class="apollo-data-item"><span class="apollo-data-label">💰 Revenue Range</span><span class="apollo-data-value">${d.revenue_range}</span></div>`;
                if (d.employee_count != null) inner += `<div class="apollo-data-item"><span class="apollo-data-label">👥 Employee Count</span><span class="apollo-data-value">${typeof d.employee_count === 'number' ? d.employee_count.toLocaleString() : d.employee_count}</span></div>`;
                if (d.industry) inner += `<div class="apollo-data-item"><span class="apollo-data-label">🏢 Industry</span><span class="apollo-data-value">${d.industry}</span></div>`;
                inner += '</div>';
                if (d.description) inner += `<div class="apollo-data-item" style="margin-bottom: 1rem;"><span class="apollo-data-label">📝 Company Description</span><span class="apollo-data-value" style="margin-top: 0.5rem; line-height: 1.5;">${d.description}</span></div>`;
                if (d.technologies && d.technologies.length) inner += `<div class="apollo-data-item"><span class="apollo-data-label">💻 Tech Stack</span><div class="tech-stack-list">${d.technologies.map(t => `<span class="tech-badge">${t}</span>`).join('')}</div></div>`;
                if (showButton) inner += `<button class="btn-secondary" onclick="reEnrichLead('${lead.id}')" style="width: 100%; margin-top: 1rem; padding: 0.75rem; background: linear-gradient(135deg, #8b5cf6, #a855f7); color: white; border: none; border-radius: 6px; font-weight: 600; cursor: pointer;">🔄 Re-enrich with Apollo</button>`;
                return `<div class="company-details-section"><button class="company-details-toggle" onclick="toggleCompanyDetails('${lead.id}', ${sufArg})"><span style="display: flex; align-items: center; gap: 0.5rem;"><span style="color: #8b5cf6;">✨</span><span>${title}</span></span><span class="toggle-icon" id="toggle-${lid}">▼</span></button><div class="company-details-content" id="details-${lid}">${inner}</div></div>`;
            };
            if (dual) {
                html += collapsible(contact, 'Apollo Company Details (employer)', 'employer', false);
                html += collapsible(scanned, 'Apollo Company Details (demo URL)', 'scanned', true);
            } else {
                html += collapsible(scanned, 'Apollo Company Details', null, true);
            }
            return html;
        }
        
        function normalizeWebsiteUrl(url) {
            if (!url) return '';
            // Remove any existing protocol (http://, https://, or malformed like https//)
            // Handle multiple protocols by removing them iteratively
            let previous;
            do {
                previous = url;
                // Remove http:// or https://
                url = url.replace(/^https?:\\/\\//i, '');
                // Remove malformed protocols like https// or http//
                url = url.replace(/^https?\\/\\//i, '');
                // Remove http:/ or https:/ (missing one slash)
                url = url.replace(/^https?:\\//i, '');
            } while (url !== previous);
            // Remove any leading slashes
            url = url.replace(/^\\/+/, '');
            // Add https:// if we have a valid domain
            if (url) {
                return 'https://' + url;
            }
            return '';
        }
        
        document.getElementById('add-lead-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const data = {
                email: formData.get('email'),
                website: formData.get('website'),
                full_name: formData.get('full_name') || null,
                has_meeting: formData.get('has_meeting') === 'true'
            };
            
            try {
                const response = await fetch('/api/leads/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                
                if (response.ok) {
                    const lead = await response.json();
                    leads.pending.unshift(lead);
                    renderLeads();
                    e.target.reset();
                    showToast('Lead analyzed and added!', 'success');
                } else {
                    const error = await response.json();
                    showToast(error.detail || 'Error adding lead', 'error');
                }
            } catch (err) {
                showToast('Error: ' + err.message, 'error');
            }
        });
        
        // Open push modal with preview
        // Extract company name from domain (remove TLD like .com, .fr, etc.)
        function domainToCompanyName(domain) {
            if (!domain) return '';
            // Remove protocol
            let d = domain.replace(/^https?:\\/\\//, '').replace(/^www\\./, '');
            // Remove path
            d = d.split('/')[0];
            // Remove TLD (last part after last dot)
            const parts = d.split('.');
            if (parts.length > 1) {
                parts.pop(); // Remove TLD like .com, .fr
            }
            // Join remaining parts and capitalize
            const name = parts.join('-');
            // Title case: first letter uppercase
            return name.charAt(0).toUpperCase() + name.slice(1);
        }
        
        function openPushModal(leadId) {
            pushingLeadId = leadId;
            const lead = leads.pending.find(l => l.id === leadId);
            if (!lead) return;
            
            // Parse name
            let firstName = '';
            let lastName = '';
            if (lead.full_name) {
                const parts = lead.full_name.split(' ');
                firstName = parts[0] || '';
                lastName = parts.slice(1).join(' ') || '';
            }
            
            // Map priority to HubSpot format (Title Case -> Title case for "Very high")
            const priorityMap = {
                'Very High': 'Very high',
                'Very high': 'Very high',
                'High': 'High',
                'Medium': 'Medium',
                'Low': 'Low'
            };
            const mappedPriority = priorityMap[lead.priority] || 'Medium';
            
            // Generate company name from domain (without TLD)
            // Always use domain-based name, unless company_name is a proper name (not a domain)
            let companyName = lead.company_name || '';
            // If company_name looks like a domain (contains .com, .fr, etc.), regenerate it
            if (!companyName || companyName.includes('.com') || companyName.includes('.fr') || 
                companyName.includes('.co') || companyName.includes('.io') || companyName.includes('.ai') ||
                companyName.includes('.net') || companyName.includes('.org')) {
                companyName = domainToCompanyName(lead.domain || lead.website);
            }
            
            // Populate form
            document.getElementById('push-company-domain').value = lead.domain || lead.website || '';
            document.getElementById('push-company-name').value = companyName;
            document.getElementById('push-priority').value = mappedPriority;
            document.getElementById('push-contact-email').value = lead.email || '';
            document.getElementById('push-contact-firstname').value = firstName;
            document.getElementById('push-contact-lastname').value = lastName;
            
            // Reset other fields to defaults
            document.getElementById('push-company-owner').value = '';
            document.getElementById('push-contact-owner').value = '';
            document.getElementById('push-demo-request').value = 'true';
            document.getElementById('push-ai-status').value = 'To Do';
            
            document.getElementById('push-modal').classList.add('show');
        }
        
        function closePushModal() {
            pushingLeadId = null;
            document.getElementById('push-modal').classList.remove('show');
        }
        
        async function confirmPush() {
            if (!pushingLeadId) return;
            
            const btn = document.getElementById('confirm-push-btn');
            btn.disabled = true;
            btn.innerHTML = '<span class="loading">⏳</span> Pushing...';
            
            const pushData = {
                company_domain: document.getElementById('push-company-domain').value,
                company_name: document.getElementById('push-company-name').value,
                company_owner: document.getElementById('push-company-owner').value,
                priority: document.getElementById('push-priority').value,
                demo_request: document.getElementById('push-demo-request').value,
                ai_status: document.getElementById('push-ai-status').value,
                contact_email: document.getElementById('push-contact-email').value,
                contact_firstname: document.getElementById('push-contact-firstname').value,
                contact_lastname: document.getElementById('push-contact-lastname').value,
                contact_owner: document.getElementById('push-contact-owner').value
            };
            
            try {
                const response = await fetch(`/api/leads/${pushingLeadId}/push`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(pushData)
                });
                
                if (response.ok) {
                    const result = await response.json();
                    showToast(`Pushed to HubSpot! Company: ${result.company_id}`, 'success');
                    closePushModal();
                    await loadLeads();
                } else {
                    const error = await response.json();
                    showToast(error.detail || 'Error pushing to HubSpot', 'error');
                }
            } catch (err) {
                showToast('Error: ' + err.message, 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>🚀</span> Push to HubSpot';
            }
        }
        
        function openRejectModal(leadId) {
            rejectingLeadId = leadId;
            document.getElementById('reject-modal').classList.add('show');
            document.getElementById('reject-reason').focus();
        }
        
        function closeRejectModal() {
            rejectingLeadId = null;
            document.getElementById('reject-modal').classList.remove('show');
            document.getElementById('reject-reason').value = '';
        }
        
        async function confirmReject() {
            if (!rejectingLeadId) return;
            
            const reason = document.getElementById('reject-reason').value || 'No reason provided';
            
            try {
                const response = await fetch(`/api/leads/${rejectingLeadId}/reject`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason })
                });
                
                if (response.ok) {
                    showToast('Lead rejected', 'success');
                    closeRejectModal();
                    await loadLeads();
                }
            } catch (err) {
                showToast('Error: ' + err.message, 'error');
            }
        }
        
        async function markLeadAlreadyBooked(leadId) {
            const reason = 'Meeting in Calendly';
            try {
                const response = await fetch(`/api/leads/${leadId}/already-booked`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason: reason })
                });
                if (response.ok) {
                    showToast('Lead marked as already booked', 'success');
                    await loadLeads();
                } else {
                    const err = await response.json();
                    showToast(err.detail || 'Error', 'error');
                }
            } catch (err) {
                showToast('Error: ' + err.message, 'error');
            }
        }
        
        // Re-enrich a lead with Apollo API
        async function reEnrichLead(leadId) {
            const button = event.target;
            const originalText = button.innerHTML;
            
            // Show loading state
            button.disabled = true;
            button.innerHTML = '🔄 Enriching...';
            button.style.opacity = '0.6';
            
            try {
                showToast('Enriching with Apollo...', 'info');
                
                const response = await fetch(`/api/leads/${leadId}/enrich`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showToast(result.message || 'Successfully enriched with Apollo!', 'success');
                    // Reload leads to show updated data
                    await loadLeads();
                } else {
                    showToast('Error: ' + (result.error || 'Failed to enrich'), 'error');
                    button.disabled = false;
                    button.innerHTML = originalText;
                    button.style.opacity = '1';
                }
            } catch (err) {
                showToast('Error: ' + err.message, 'error');
                button.disabled = false;
                button.innerHTML = originalText;
                button.style.opacity = '1';
            }
        }
        
        // Helper function to check if a lead is from today
        function isFromToday(dateStr) {
            if (!dateStr) return false;
            const today = new Date().toISOString().split('T')[0];
            return dateStr.startsWith(today);
        }
        
        // Get new leads (from today)
        function getNewLeads() {
            return leads.pending.filter(lead => isFromToday(lead.created_at));
        }
        
        function renderLeads() {
            const container = document.getElementById('lead-list');
            
            let currentLeads = [];
            if (currentTab === 'new') {
                currentLeads = getNewLeads();
            } else if (currentTab === 'pending') {
                currentLeads = leads.pending;
            } else if (currentTab === 'pushed') {
                currentLeads = leads.pushed;
            } else if (currentTab === 'rejected') {
                currentLeads = leads.rejected;
            } else if (currentTab === 'already_booked') {
                currentLeads = leads.already_booked;
            }
            
            currentLeads = sortLeads(currentLeads);
            
            // Update counts
            const newCount = getNewLeads().length;
            document.getElementById('new-count').textContent = newCount;
            document.getElementById('pending-count').textContent = leads.pending.length;
            document.getElementById('pushed-count').textContent = leads.pushed.length;
            document.getElementById('rejected-count').textContent = leads.rejected.length;
            document.getElementById('already-booked-count').textContent = (leads.already_booked || []).length;
            
            if (currentLeads.length === 0 && currentTab !== 'post-performance') {
                const messages = {
                    new: { title: 'No new leads today', desc: 'Click Refresh to sync latest Webflow submissions' },
                    pending: { title: 'No pending leads', desc: 'Add a lead above or wait for Webflow form submissions' },
                    pushed: { title: 'No pushed leads yet', desc: 'Push some leads to see them here' },
                    rejected: { title: 'No rejected leads', desc: 'Rejected leads will appear here for reference' },
                    already_booked: { title: 'No already booked leads', desc: 'Leads marked as already booked (e.g. meeting in Calendly) will appear here' }
                };
                container.innerHTML = `
                    <div class="empty-state">
                        <h3>${messages[currentTab].title}</h3>
                        <p>${messages[currentTab].desc}</p>
                    </div>
                `;
                return;
            }
            
            container.innerHTML = currentLeads.map((lead, index) => {
                const priorityClass = (lead.priority || 'Medium').toLowerCase().replace(' ', '-');
                const isRejected = currentTab === 'rejected';
                const isPushed = currentTab === 'pushed';
                const isAlreadyBooked = currentTab === 'already_booked';
                const hsStatus = hubspotCache[lead.id];
                
                return `
                <div class="lead-card priority-${priorityClass} ${isRejected ? 'rejected-card' : ''} ${isAlreadyBooked ? 'already-booked-card' : ''}" data-lead-id="${lead.id}">
                    <div class="lead-header">
                        <div class="lead-info">
                            <h3>${lead.company_name || lead.website}</h3>
                            <div class="email">${lead.email}</div>
                            <div class="lead-date">📅 ${formatDate(lead.created_at)}</div>
                        </div>
                        <div class="lead-badges">
                            <span class="badge priority priority-${priorityClass}">${lead.priority}</span>
                            ${lead.apollo_enriched ? '<span class="badge apollo">✨ Apollo Verified</span>' : ''}
                            ${lead.is_agency ? '<span class="badge agency">🏢 Agency</span>' : '<span class="badge ecommerce">🛒 E-commerce</span>'}
                            ${lead.has_meeting ? '<span class="badge meeting">📅 Meeting</span>' : ''}
                            ${lead.meeting_booked === 'calendly' ? '<span class="badge" style="background: #10b981; color: white;">📆 Meeting Booked</span>' : ''}
                            ${lead.meeting_completed ? '<span class="badge" style="background: #22c55e; color: white;">✅ Meeting Done</span>' : ''}
                            ${lead.is_fast_track ? '<span class="badge" style="background: #f59e0b; color: white;">⚡ Fast Track</span>' : ''}
                            ${lead.deal_status === 'won' ? '<span class="badge" style="background: #8b5cf6; color: white;">🏆 Won</span>' : ''}
                        </div>
                    </div>
                    
                    <div class="hubspot-status" id="hs-status-${lead.id}" style="margin-bottom: 1rem;">
                        ${renderHubSpotStatus(lead.id, hsStatus, lead)}
                    </div>
                    
                    ${lead.post_creator ? `
                    <div style="margin-bottom: 1rem; padding: 0.75rem; background: linear-gradient(135deg, rgba(106, 91, 149, 0.1), rgba(139, 124, 181, 0.05)); border: 1px solid rgba(106, 91, 149, 0.3); border-radius: 8px;">
                        <span class="detail-label" style="color: var(--accent-primary); font-weight: 600; margin-bottom: 0.5rem; display: block;">📱 LinkedIn Post Attribution</span>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.75rem; font-size: 0.85rem;">
                            <div>
                                <strong style="color: var(--text-secondary);">👤 Creator:</strong> 
                                <span style="color: var(--accent-primary); font-weight: 600; text-transform: capitalize;">${lead.post_creator}</span>
                            </div>
                            <div>
                                <strong style="color: var(--text-secondary);">📅 Date:</strong> 
                                <span style="color: var(--text-primary);">${lead.post_date}</span>
                            </div>
                            <div>
                                <strong style="color: var(--text-secondary);">🎯 Track:</strong> 
                                <span style="background: var(--accent-success); color: white; padding: 0.15rem 0.4rem; border-radius: 4px; font-weight: 600;">${lead.post_track}</span>
                            </div>
                        </div>
                    </div>
                    ` : ''}
                    
                    ${lead.apollo_enriched ? renderApolloBlocks(lead) : ''}
                    
                    <div class="lead-details">
                        <div class="detail">
                            <span class="detail-label">Website</span>
                            <span class="detail-value"><a href="${normalizeWebsiteUrl(lead.website)}" target="_blank">${lead.website}</a></span>
                        </div>
                        <div class="detail">
                            <span class="detail-label">Type</span>
                            <span class="detail-value">${lead.is_agency ? '🏢 Agency' : (lead.is_ecommerce ? '🛒 E-commerce' : '🏭 Brand')}</span>
                        </div>
                        <div class="detail">
                            <span class="detail-label">Industry</span>
                            <span class="detail-value">${lead.industry || 'Unknown'}</span>
                        </div>
                        <div class="detail">
                            <span class="detail-label">Revenue ${lead.apollo_enriched && lead.revenue_range ? '✨' : ''}</span>
                            <span class="detail-value" style="${lead.apollo_enriched && lead.revenue_range ? 'color: #8b5cf6; font-weight: 600;' : ''}">${lead.revenue_range || lead.revenue || '?'}</span>
                        </div>
                        <div class="detail">
                            <span class="detail-label">Employees ${lead.apollo_enriched && lead.employee_count ? '✨' : ''}</span>
                            <span class="detail-value" style="${lead.apollo_enriched && lead.employee_count ? 'color: #8b5cf6; font-weight: 600;' : ''}">${lead.employee_count ? lead.employee_count.toLocaleString() : (lead.employee_range || '?')}</span>
                        </div>
                        <div class="detail">
                            <span class="detail-label">LinkedIn ${lead.apollo_enriched && lead.linkedin_url ? '✨' : ''}</span>
                            <span class="detail-value">${lead.linkedin_url ? `<a href="${lead.linkedin_url}" target="_blank" style="${lead.apollo_enriched ? 'color: #8b5cf6; font-weight: 600;' : ''}">View →</a>` : '?'}</span>
                        </div>
                    </div>
                    
                    ${lead.email_domain_company ? `
                    <div class="email-domain-info" style="margin-bottom: 1rem; padding: 0.75rem; background: rgba(245, 158, 11, 0.1); border: 1px solid var(--accent-warning); border-radius: 8px;">
                        <span class="detail-label" style="color: var(--accent-warning);">📧 Email Domain: ${getEmailDomain(lead.email)}</span>
                        <div style="margin-top: 0.5rem; display: flex; gap: 1rem; flex-wrap: wrap;">
                            <span>${lead.email_domain_company}</span>
                            ${lead.email_domain_is_agency ? '<span class="badge agency" style="font-size: 0.65rem;">🏢 Agency</span>' : ''}
                            ${lead.email_domain_linkedin ? `<a href="${lead.email_domain_linkedin}" target="_blank" style="color: #0a66c2;">LinkedIn →</a>` : ''}
                        </div>
                    </div>
                    ` : ''}
                    
                    ${lead.icp_reasons && lead.icp_reasons.length > 0 ? `
                    <div class="icp-analysis" style="margin-bottom: 1rem; padding: 0.75rem; background: var(--bg-secondary); border-radius: 8px;">
                        <span class="detail-label" style="margin-bottom: 0.5rem; display: block;">📊 ICP Analysis (Score: ${lead.icp_score}/100)</span>
                        <div style="display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.8rem;">
                            ${lead.icp_reasons.slice(0, 6).map(r => `<span style="color: ${r.startsWith('✓') ? 'var(--accent-success)' : r.startsWith('✗') ? 'var(--accent-danger)' : 'var(--text-muted)'}">${r}</span>`).join('')}
                        </div>
                    </div>
                    ` : ''}
                    
                    ${!lead.apollo_enriched ? `
                    <div style="margin-bottom: 1rem;">
                        <button 
                            class="btn-secondary" 
                            onclick="reEnrichLead('${lead.id}')"
                            style="width: 100%; padding: 0.75rem; background: linear-gradient(135deg, #8b5cf6, #a855f7); color: white; border: none; border-radius: 6px; font-weight: 600; cursor: pointer; transition: all 0.2s; display: flex; align-items: center; justify-content: center; gap: 0.5rem;"
                            onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 4px 12px rgba(139, 92, 246, 0.4)'"
                            onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='none'"
                        >
                            <span>✨</span>
                            <span>Enrich with Apollo</span>
                        </button>
                    </div>
                    ` : ''}
                    
                    <div class="research-links" style="margin-bottom: 1rem;">
                        <span class="detail-label">Quick Research</span>
                        <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.5rem;">
                            <a href="https://www.google.com/search?q=revenue+${encodeURIComponent(lead.website)}" 
                               target="_blank" class="research-link">
                                🔍 revenue ${lead.website}
                            </a>
                            <a href="https://www.google.com/search?q=${encodeURIComponent(lead.website)}+company+size+employees" 
                               target="_blank" class="research-link">
                                👥 employees ${lead.website}
                            </a>
                            ${getEmailDomain(lead.email) && getEmailDomain(lead.email) !== normalizeWebsiteDomain(lead.website) && !['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com'].includes(getEmailDomain(lead.email)) ? `
                                <a href="https://www.google.com/search?q=revenue+${encodeURIComponent(getEmailDomain(lead.email))}" 
                                   target="_blank" class="research-link email-domain">
                                    🔍 revenue ${getEmailDomain(lead.email)}
                                </a>
                            ` : ''}
                            <a href="https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(lead.company_name || lead.website)}" 
                               target="_blank" class="research-link linkedin">
                                💼 LinkedIn
                            </a>
                        </div>
                    </div>
                    
                    ${isRejected ? `
                        <div class="rejected-reason">
                            <strong>Rejected:</strong> ${lead.rejected_reason || 'No reason provided'}
                            <br><small>${lead.rejected_at ? new Date(lead.rejected_at).toLocaleString() : ''}</small>
                        </div>
                    ` : ''}
                    
                    ${isPushed ? `
                        <div class="pushed-badge">
                            <span>✅</span> Pushed to HubSpot
                            ${lead.hubspot_company_id ? `(Company: ${lead.hubspot_company_id})` : ''}
                        </div>
                    ` : ''}
                    
                    ${isAlreadyBooked ? `
                        <div class="already-booked-badge" style="padding: 0.75rem; background: rgba(14, 165, 233, 0.15); border: 1px solid #0ea5e9; border-radius: 8px; color: #0ea5e9;">
                            <strong>📅 Already booked</strong>
                            ${(lead.already_booked_reason || lead.rejected_reason) ? `<br><span style="font-size: 0.9rem;">${lead.already_booked_reason || lead.rejected_reason}</span>` : ''}
                            <br><small style="opacity: 0.9;">${lead.already_booked_at ? new Date(lead.already_booked_at).toLocaleString() : ''}</small>
                        </div>
                    ` : ''}
                    
                    ${!isRejected && !isPushed && !isAlreadyBooked ? `
                        <div class="lead-actions">
                            <button class="btn btn-danger" onclick="openRejectModal('${lead.id}')">
                                <span>✕</span> Reject
                            </button>
                            <button class="btn" onclick="markLeadAlreadyBooked('${lead.id}')" style="background: #0ea5e9; color: white; border: none;">
                                <span>📅</span> Already booked
                            </button>
                            <button class="btn btn-success" onclick="openPushModal('${lead.id}')">
                                <span>🚀</span> Push to HubSpot
                            </button>
                        </div>
                    ` : ''}
                </div>
            `}).join('');
        }
        
        function renderHubSpotStatus(leadId, status, lead) {
            // First check if lead was marked as in_hubspot during sync
            if (lead && lead.in_hubspot) {
                return `<div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                    <a href="https://app.hubspot.com/contacts/243279017/record/0-2/${lead.hubspot_company_id}" 
                       target="_blank" class="hs-badge in-hubspot">
                        ✅ Already in HubSpot CRM <span class="arrow">→</span>
                    </a>
                </div>`;
            }
            
            if (!status) {
                return '<span class="hs-badge not-checked">⏸️ Click Refresh to check HubSpot</span>';
            }
            
            if (status.error) {
                return '<span style="font-size: 0.75rem; color: var(--text-muted);">❓ Could not check</span>';
            }
            
            let html = '<div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">';
            
            if (status.company_exists) {
                html += `<a href="https://app.hubspot.com/contacts/243279017/record/0-2/${status.company_id}" 
                           target="_blank" class="hs-badge in-hubspot">
                    🏢 Company in HubSpot <span class="arrow">→</span>
                </a>`;
            }
            
            if (status.contact_exists) {
                html += `<a href="https://app.hubspot.com/contacts/243279017/record/0-1/${status.contact_id}" 
                           target="_blank" class="hs-badge contact-exists">
                    👤 Contact in HubSpot <span class="arrow">→</span>
                </a>`;
            }
            
            if (!status.company_exists && !status.contact_exists) {
                html += '<span class="hs-badge new-lead">✨ New Lead</span>';
            }
            
            html += '</div>';
            return html;
        }
        
        async function refreshHubSpotStatus() {
            const btn = document.getElementById('refresh-btn');
            btn.disabled = true;
            btn.classList.add('loading');
            btn.innerHTML = '<span class="refresh-icon">🔄</span> Syncing from Webflow...';
            
            // STEP 1: Sync from Webflow API and reload leads
            let syncResult = null;
            try {
                const response = await fetch('/api/leads/reload', { method: 'POST' });
                if (response.ok) {
                    const data = await response.json();
                    const oldCount = leads.pending.length;
                    syncResult = data.sync_result;
                    leads = {
                        pending: data.pending || [],
                        pushed: data.pushed || [],
                        rejected: data.rejected || [],
                        already_booked: data.already_booked || []
                    };
                    const newCount = leads.pending.length;
                    
                    if (syncResult && syncResult.new_leads > 0) {
                        showToast(`✨ Found ${syncResult.new_leads} new lead(s) from Webflow!`, 'success');
                    } else if (syncResult && syncResult.error) {
                        showToast(`⚠️ ${syncResult.error}`, 'warning');
                    }
                    
                    // Re-render with new leads
                    renderLeads();
                }
            } catch (err) {
                console.error('Error syncing from Webflow:', err);
                showToast('Error syncing from Webflow', 'error');
            }
            
            // STEP 2: Check HubSpot status for all pending leads
            let checked = 0;
            const totalToCheck = leads.pending.length;
            
            for (const lead of leads.pending) {
                try {
                    btn.innerHTML = `<span class="refresh-icon">🔄</span> Checking HubSpot ${checked + 1}/${totalToCheck}...`;
                    
                    const response = await fetch(`/api/hubspot/check/${lead.id}`);
                    if (response.ok) {
                        const result = await response.json();
                        hubspotCache[lead.id] = result;
                        
                        // Update UI immediately
                        const container = document.getElementById(`hs-status-${lead.id}`);
                        if (container) {
                            container.innerHTML = renderHubSpotStatus(lead.id, result);
                        }
                    } else {
                        hubspotCache[lead.id] = { error: true };
                    }
                } catch (err) {
                    console.error(`Error checking lead ${lead.id}:`, err);
                    hubspotCache[lead.id] = { error: true };
                }
                
                checked++;
                
                // Small delay to not overwhelm API
                await new Promise(r => setTimeout(r, 150));
            }
            
            btn.disabled = false;
            btn.classList.remove('loading');
            btn.innerHTML = '<span class="refresh-icon">🔄</span> Refresh';
            
            const now = new Date().toLocaleTimeString();
            document.getElementById('last-refresh').textContent = `Last checked: ${now} (${leads.pending.length} pending)`;
        }
        
        async function loadPostPerformance() {
            const container = document.getElementById('post-performance-grid');
            container.innerHTML = '<div style="text-align: center; padding: 2rem; color: var(--text-muted);">Loading post performance...</div>';
            
            try {
                const response = await fetch('/api/post-performance');
                if (!response.ok) throw new Error('Failed to load');
                
                const data = await response.json();
                const posts = data.posts || [];
                
                if (posts.length === 0) {
                    container.innerHTML = `
                        <div style="text-align: center; padding: 3rem;">
                            <h2 style="color: var(--text-muted); margin-bottom: 1rem;">📊 No Posts Tracked Yet</h2>
                            <p style="color: var(--text-secondary);">Generate your first tracking link to start measuring post performance:</p>
                            <code style="display: block; margin-top: 1rem; padding: 1rem; background: var(--bg-secondary); border-radius: 8px;">
                                python3 scripts/generate_link.py --creator laura --date jan15 --track A3 --cost 400
                            </code>
                        </div>
                    `;
                    return;
                }
                
                container.innerHTML = posts.map(post => `
                    <div style="background: var(--bg-card); border-radius: 12px; padding: 1.5rem; border: 1px solid var(--border); margin-bottom: 1.5rem;">
                        <!-- Header -->
                        <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 1rem; padding-bottom: 1rem; border-bottom: 1px solid var(--border);">
                            <div>
                                <h3 style="margin: 0; color: var(--accent-primary); text-transform: capitalize; font-size: 1.25rem;">
                                    ${post.creator} - ${post.date}
                                </h3>
                                <div style="display: flex; gap: 0.75rem; margin-top: 0.5rem; align-items: center;">
                                    <span style="background: var(--accent-success); color: white; padding: 0.25rem 0.6rem; border-radius: 6px; font-weight: 600; font-size: 0.8rem;">
                                        Track ${post.track}
                                    </span>
                                    <span style="color: var(--accent-success); font-weight: 600;">$${post.cost}</span>
                                    ${post.link ? `<a href="${post.link}" target="_blank" style="color: var(--accent-primary); text-decoration: none; font-size: 0.85rem;">🔗 Link</a>` : ''}
                                    ${post.post_url ? `<a href="${post.post_url}" target="_blank" style="color: var(--accent-primary); text-decoration: none; font-size: 0.85rem;">📱 Post</a>` : ''}
                                </div>
                            </div>
                            ${post.hubspot_synced ? '<span style="background: rgba(52, 211, 153, 0.1); color: var(--accent-success); padding: 0.25rem 0.6rem; border-radius: 6px; font-size: 0.75rem; font-weight: 600;">✅ HubSpot Live</span>' : ''}
                        </div>
                        
                        <!-- Engagement Metrics -->
                        <div style="margin-bottom: 1.5rem;">
                            <h4 style="margin: 0 0 0.75rem 0; color: var(--text-secondary); font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.5px;">📊 Engagement</h4>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.75rem;">
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Reactions</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent-primary);">❤️ ${post.reactions || 0}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Comments</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent-primary);">💬 ${post.comments || 0}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Impressions</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent-primary);">👁️ ${post.impressions ? post.impressions.toLocaleString() : 0}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Pure CTA</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent-success);">🔗 ${post.pure_cta || 0}</div>
                                </div>
                            </div>
                        </div>
                        
                        <!-- Funnel Metrics -->
                        <div style="margin-bottom: 1.5rem;">
                            <h4 style="margin: 0 0 0.75rem 0; color: var(--text-secondary); font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.5px;">🎯 Conversion Funnel</h4>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.75rem;">
                                <div style="background: linear-gradient(135deg, rgba(106, 91, 149, 0.1), rgba(139, 124, 181, 0.05)); padding: 0.75rem; border-radius: 6px; border: 1px solid rgba(106, 91, 149, 0.2);">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Demos</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent-primary);">${post.total_demos}</div>
                                </div>
                                <div style="background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(37, 99, 235, 0.05)); padding: 0.75rem; border-radius: 6px; border: 1px solid rgba(59, 130, 246, 0.2);">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">ICP Fit</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: #3b82f6;">${post.icp_fit_leads}</div>
                                </div>
                                <div style="background: linear-gradient(135deg, rgba(251, 146, 60, 0.1), rgba(249, 115, 22, 0.05)); padding: 0.75rem; border-radius: 6px; border: 1px solid rgba(251, 146, 60, 0.2);">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Meeting Req.</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: #fb923c;">${post.meeting_requests || 0}</div>
                                </div>
                                <div style="background: linear-gradient(135deg, rgba(52, 211, 153, 0.1), rgba(16, 185, 129, 0.05)); padding: 0.75rem; border-radius: 6px; border: 1px solid rgba(52, 211, 153, 0.2);">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Meetings Done</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent-success);">${post.meetings_done || 0}</div>
                                </div>
                                <div style="background: linear-gradient(135deg, rgba(168, 85, 247, 0.1), rgba(147, 51, 234, 0.05)); padding: 0.75rem; border-radius: 6px; border: 1px solid rgba(168, 85, 247, 0.2);">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Fast Tracks</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: #a855f7;">⚡ ${post.fast_tracks || 0}</div>
                                </div>
                                <div style="background: linear-gradient(135deg, rgba(34, 197, 94, 0.1), rgba(22, 163, 74, 0.05)); padding: 0.75rem; border-radius: 6px; border: 1px solid rgba(34, 197, 94, 0.2);">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Wins</div>
                                    <div style="font-size: 1.5rem; font-weight: 700; color: #22c55e;">🏆 ${post.wins || 0}</div>
                                </div>
                            </div>
                        </div>
                        
                        <!-- Cost Metrics -->
                        <div>
                            <h4 style="margin: 0 0 0.75rem 0; color: var(--text-secondary); font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.5px;">💰 ROI</h4>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.75rem;">
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">$/Demo</div>
                                    <div style="font-size: 1.25rem; font-weight: 700; color: var(--accent-warning);">$${post.cost_per_demo || 0}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">$/ICP</div>
                                    <div style="font-size: 1.25rem; font-weight: 700; color: var(--accent-warning);">${post.cost_per_icp > 0 ? '$' + post.cost_per_icp : 'N/A'}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">$/Meeting</div>
                                    <div style="font-size: 1.25rem; font-weight: 700; color: var(--accent-warning);">${post.cost_per_meeting > 0 ? '$' + post.cost_per_meeting : 'N/A'}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">$/Win</div>
                                    <div style="font-size: 1.25rem; font-weight: 700; color: var(--accent-success);">${post.cost_per_win > 0 ? '$' + post.cost_per_win : 'N/A'}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 0.75rem; border-radius: 6px;">
                                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-bottom: 0.25rem;">Push Rate</div>
                                    <div style="font-size: 1.25rem; font-weight: 700; color: #3b82f6;">${post.push_rate}%</div>
                                </div>
                            </div>
                        </div>
                    </div>
                `).join('');
                
            } catch (err) {
                console.error('Error loading post performance:', err);
                container.innerHTML = '<div style="text-align: center; padding: 2rem; color: var(--accent-danger);">Error loading post performance</div>';
            }
        }
        
        let analyticsCharts = {
            traffic: null,
            funnel: null,
            monthly: null
        };
        
        async function loadAnalytics() {
            try {
                const response = await fetch('/api/analytics');
                if (!response.ok) throw new Error('Failed to load analytics');
                
                const data = await response.json();
                analyticsData = data;  // Store for period switching
                const totals = data.totals;
                const monthly = data.monthly;
                
                // Update total metrics cards
                document.getElementById('total-leads').textContent = totals.total_leads.toLocaleString();
                document.getElementById('organic-count').textContent = totals.organic.toLocaleString();
                document.getElementById('post-sourced-count').textContent = totals.post_sourced.toLocaleString();
                document.getElementById('total-icp').textContent = totals.icp_fit.toLocaleString();
                document.getElementById('icp-rate').textContent = totals.icp_fit_rate;
                document.getElementById('total-crm').textContent = totals.pushed_to_crm.toLocaleString();
                document.getElementById('crm-rate').textContent = totals.crm_push_rate;
                document.getElementById('total-already-booked').textContent = (totals.already_booked || 0).toLocaleString();
                document.getElementById('total-pending').textContent = (totals.pending || 0).toLocaleString();
                document.getElementById('total-rejected').textContent = (totals.rejected || 0).toLocaleString();
                document.getElementById('total-cost').textContent = '$' + totals.total_cost.toLocaleString();
                document.getElementById('total-reactions').textContent = (totals.reactions + totals.comments).toLocaleString();
                document.getElementById('total-wins').textContent = totals.wins.toLocaleString();
                document.getElementById('analytics-last-updated').textContent = 'Last updated: ' + new Date(data.last_updated).toLocaleTimeString();
                
                // Destroy existing charts
                if (analyticsCharts.traffic) analyticsCharts.traffic.destroy();
                if (analyticsCharts.funnel) analyticsCharts.funnel.destroy();
                if (analyticsCharts.period) analyticsCharts.period.destroy();
                
                // Traffic Sources Pie Chart
                const trafficCtx = document.getElementById('traffic-chart').getContext('2d');
                analyticsCharts.traffic = new Chart(trafficCtx, {
                    type: 'doughnut',
                    data: {
                        labels: ['Organic Traffic', 'Post-Sourced'],
                        datasets: [{
                            data: [totals.organic, totals.post_sourced],
                            backgroundColor: ['#3b82f6', '#a855f7'],
                            borderWidth: 0
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                position: 'bottom',
                                labels: { color: '#9ca3af', font: { size: 12 } }
                            }
                        }
                    }
                });
                
                // Conversion Funnel Bar Chart
                const funnelCtx = document.getElementById('funnel-chart').getContext('2d');
                analyticsCharts.funnel = new Chart(funnelCtx, {
                    type: 'bar',
                    data: {
                        labels: ['Leads', 'ICP Fit', 'CRM', 'Already Booked', 'Demos', 'Meetings', 'Wins'],
                        datasets: [{
                            label: 'Count',
                            data: [
                                totals.total_leads,
                                totals.icp_fit,
                                totals.pushed_to_crm,
                                totals.already_booked || 0,
                                totals.demos,
                                totals.meetings,
                                totals.wins
                            ],
                            backgroundColor: ['#3b82f6', '#a855f7', '#10b981', '#0ea5e9', '#fb923c', '#ef4444', '#22c55e'],
                            borderWidth: 0
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                ticks: { color: '#9ca3af' },
                                grid: { color: 'rgba(255, 255, 255, 0.05)' }
                            },
                            x: {
                                ticks: { color: '#9ca3af' },
                                grid: { display: false }
                            }
                        }
                    }
                });
                
                // Initial period data render
                renderPeriodData();
                
            } catch (err) {
                console.error('Error loading analytics:', err);
                showToast('Error loading analytics', 'error');
            }
        }
        
        function renderPeriodData() {
            if (!analyticsData) return;
            
            const periodData = currentPeriod === 'weekly' ? analyticsData.weekly : analyticsData.monthly;
            const labelKey = currentPeriod === 'weekly' ? 'week_label' : 'month_label';
            const titlePrefix = currentPeriod === 'weekly' ? '📆 Weekly' : '📅 Monthly';
            
            // Update titles
            document.getElementById('breakdown-chart-title').textContent = `📊 ${titlePrefix} Breakdown`;
            document.getElementById('breakdown-table-title').textContent = `${titlePrefix} Details`;
            
            // Destroy existing period chart
            if (analyticsCharts.period) analyticsCharts.period.destroy();
            
            if (periodData.length > 0) {
                // Period Breakdown Line Chart
                const periodCtx = document.getElementById('period-chart').getContext('2d');
                analyticsCharts.period = new Chart(periodCtx, {
                        type: 'line',
                        data: {
                            labels: periodData.map(m => m[labelKey]).reverse(),
                            datasets: [
                                {
                                    label: 'Organic',
                                    data: periodData.map(m => m.organic).reverse(),
                                    borderColor: '#3b82f6',
                                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                                    tension: 0.3,
                                    fill: true
                                },
                                {
                                    label: 'Post-Sourced',
                                    data: periodData.map(m => m.post_sourced).reverse(),
                                    borderColor: '#a855f7',
                                    backgroundColor: 'rgba(168, 85, 247, 0.1)',
                                    tension: 0.3,
                                    fill: true
                                },
                                {
                                    label: 'ICP Fit',
                                    data: periodData.map(m => m.icp_fit).reverse(),
                                    borderColor: '#10b981',
                                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                                    tension: 0.3,
                                    fill: true
                                },
                                {
                                    label: 'Already Booked',
                                    data: periodData.map(m => m.already_booked || 0).reverse(),
                                    borderColor: '#0ea5e9',
                                    backgroundColor: 'rgba(14, 165, 233, 0.1)',
                                    tension: 0.3,
                                    fill: true
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: true,
                            plugins: {
                                legend: {
                                    position: 'bottom',
                                    labels: { color: '#9ca3af', font: { size: 12 } }
                                }
                            },
                            scales: {
                                y: {
                                    beginAtZero: true,
                                    ticks: { color: '#9ca3af' },
                                    grid: { color: 'rgba(255, 255, 255, 0.05)' }
                                },
                                x: {
                                    ticks: { color: '#9ca3af' },
                                    grid: { display: false }
                                }
                            }
                        }
                    });
                    
                    // Period Table
                    const tableLabelHeader = currentPeriod === 'weekly' ? 'Week' : 'Month';
                    const tableHTML = `
                        <table style="width: 100%; border-collapse: collapse;">
                            <thead>
                                <tr style="background: var(--bg-secondary); border-bottom: 2px solid var(--border);">
                                    <th style="padding: 0.75rem; text-align: left; font-size: 0.85rem; color: var(--text-muted);">${tableLabelHeader}</th>
                                    <th style="padding: 0.75rem; text-align: center; font-size: 0.85rem; color: var(--text-muted);">Organic</th>
                                    <th style="padding: 0.75rem; text-align: center; font-size: 0.85rem; color: var(--text-muted);">Post-Sourced</th>
                                    <th style="padding: 0.75rem; text-align: center; font-size: 0.85rem; color: var(--text-muted);">ICP Fit</th>
                                    <th style="padding: 0.75rem; text-align: center; font-size: 0.85rem; color: var(--text-muted);">Pushed to CRM</th>
                                    <th style="padding: 0.75rem; text-align: center; font-size: 0.85rem; color: var(--text-muted);">Already Booked</th>
                                    <th style="padding: 0.75rem; text-align: center; font-size: 0.85rem; color: var(--text-muted);">Demos</th>
                                    <th style="padding: 0.75rem; text-align: center; font-size: 0.85rem; color: var(--text-muted);">Meetings</th>
                                    <th style="padding: 0.75rem; text-align: center; font-size: 0.85rem; color: var(--text-muted);">Cost</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${periodData.map(m => `
                                    <tr style="border-bottom: 1px solid var(--border);">
                                        <td style="padding: 0.75rem; font-weight: 600; color: var(--text-primary);">${m[labelKey]}</td>
                                        <td style="padding: 0.75rem; text-align: center; color: #3b82f6;">${m.organic}</td>
                                        <td style="padding: 0.75rem; text-align: center; color: #a855f7;">${m.post_sourced}</td>
                                        <td style="padding: 0.75rem; text-align: center; color: #10b981;">${m.icp_fit}</td>
                                        <td style="padding: 0.75rem; text-align: center; color: var(--accent-success);">${m.pushed_to_crm}</td>
                                        <td style="padding: 0.75rem; text-align: center; color: #0ea5e9;">${m.already_booked || 0}</td>
                                        <td style="padding: 0.75rem; text-align: center; color: #fb923c;">${m.demos}</td>
                                        <td style="padding: 0.75rem; text-align: center; color: #ef4444;">${m.meetings}</td>
                                        <td style="padding: 0.75rem; text-align: center; color: var(--accent-warning);">$${m.total_cost}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    `;
                    document.getElementById('period-table').innerHTML = tableHTML;
            }
        }
        
        function copyEmail(leadId) {
            const emailBody = document.getElementById(`email-body-${leadId}`);
            if (emailBody) {
                const subject = "Quick follow-up on your demo request";
                const body = emailBody.textContent;
                const fullEmail = `Subject: ${subject}\n\n${body}`;
                
                navigator.clipboard.writeText(fullEmail).then(() => {
                    showToast('Email copied to clipboard!', 'success');
                }).catch(err => {
                    const textArea = document.createElement('textarea');
                    textArea.value = fullEmail;
                    document.body.appendChild(textArea);
                    textArea.select();
                    document.execCommand('copy');
                    document.body.removeChild(textArea);
                    showToast('Email copied to clipboard!', 'success');
                });
            }
        }
        
        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            document.getElementById('toast-icon').textContent = type === 'success' ? '✅' : '❌';
            document.getElementById('toast-message').textContent = message;
            toast.className = `toast show ${type}`;
            setTimeout(() => toast.classList.remove('show'), 3000);
        }
        
        async function loadLeads() {
            try {
                const response = await fetch('/api/leads');
                if (response.ok) {
                    const data = await response.json();
                    leads = {
                        pending: data.pending || [],
                        pushed: data.pushed || [],
                        rejected: data.rejected || [],
                        already_booked: data.already_booked || []
                    };
                    renderLeads();
                }
            } catch (err) {
                console.error('Error loading leads:', err);
            }
        }
        
        // Auto-refresh leads every 10 seconds for real-time updates
        setInterval(loadLeads, 10000);
        loadLeads();
    </script>
        </div>
    </div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


@app.get("/api/leads")
async def get_leads():
    """Get all leads - always reload from file for real-time sync"""
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    return {
        "pending": lead_queue,
        "pushed": pushed_leads,
        "rejected": rejected_leads,
        "already_booked": already_booked_leads
    }


@app.post("/api/leads/reload")
async def reload_leads(enrich: bool = True):
    """
    Sync NEW leads from Webflow API - incremental sync.
    Only fetches submissions newer than last sync.
    """
    logger.info("Starting incremental Webflow sync...")
    
    # Sync only NEW leads from Webflow API
    sync_result = sync_webflow_leads(days_back=14, enrich=enrich)
    logger.info(f"Webflow sync result: {sync_result}")
    
    # Load the updated leads
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    
    return {
        "pending": lead_queue,
        "pushed": pushed_leads,
        "rejected": rejected_leads,
        "already_booked": already_booked_leads,
        "sync_result": sync_result
    }


@app.post("/api/webflow/initial-sync")
async def initial_webflow_sync(days: int = 14):
    """
    Initial sync - clears existing data and fetches last X days from Webflow.
    Use this once to set up, then use /api/leads/reload for incremental syncs.
    """
    logger.info(f"Starting INITIAL Webflow sync - last {days} days...")
    
    # Clear existing data (keep already_booked empty on initial sync)
    save_leads([], [], [], [])
    
    # Remove last sync timestamp to force full fetch
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
        if 'last_webflow_sync' in data:
            del data['last_webflow_sync']
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    
    # Sync with enrichment (slower but complete data)
    sync_result = sync_webflow_leads(days_back=days, enrich=True)
    
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    
    return {
        "message": f"Initial sync complete - fetched last {days} days",
        "sync_result": sync_result,
        "pending_count": len(lead_queue)
    }


@app.get("/api/webflow/status")
async def webflow_status():
    """Check Webflow sync status"""
    last_sync = get_last_sync_timestamp()
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    
    return {
        "last_sync": last_sync,
        "pending_count": len(lead_queue),
        "pushed_count": len(pushed_leads),
        "rejected_count": len(rejected_leads),
        "already_booked_count": len(already_booked_leads),
        "webflow_configured": bool(WEBFLOW_API_TOKEN)
    }


@app.get("/api/hubspot/check/{lead_id}")
async def check_hubspot_exists(lead_id: str):
    """Check if company/contact already exists in HubSpot"""
    # Reload leads from file
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    
    # Find the lead
    lead = None
    for l in lead_queue + pushed_leads + rejected_leads + already_booked_leads:
        if l['id'] == lead_id:
            lead = l
            break
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    result = {
        "company_exists": False,
        "company_id": None,
        "company_name": None,
        "contact_exists": False,
        "contact_id": None,
        "contact_name": None
    }
    
    if not HUBSPOT_AVAILABLE:
        logger.warning("HubSpot managers not available - returning empty result")
        return result
    
    try:
        company_manager = CompanyManager()
        contact_manager = ContactManager()
        
        # Check company by domain
        if lead.get('website'):
            domain = lead['website'].lower().replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
            company = company_manager.find_by_domain(domain)
            if company:
                result['company_exists'] = True
                result['company_id'] = company.get('id')
                result['company_name'] = company.get('properties', {}).get('name', domain)
        
        # Check contact by email
        if lead.get('email'):
            contact = contact_manager.find_by_email(lead['email'])
            if contact:
                result['contact_exists'] = True
                result['contact_id'] = contact.get('id')
                firstname = contact.get('properties', {}).get('firstname', '')
                lastname = contact.get('properties', {}).get('lastname', '')
                result['contact_name'] = f"{firstname} {lastname}".strip() or lead['email']
        
    except Exception as e:
        logger.error(f"Error checking HubSpot: {e}")
    
    return result


@app.post("/api/leads/analyze")
async def analyze_lead(request: Request):
    data = await request.json()
    email = data.get('email')
    website = data.get('website')
    full_name = data.get('full_name')
    has_meeting = data.get('has_meeting', False)

    if not email or not website:
        raise HTTPException(status_code=400, detail="Email and website are required")

    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    domain = extract_domain_from_url(website)
    if not domain:
        raise HTTPException(status_code=400, detail="Could not extract domain from website")

    lead = {
        "id": f"lead_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(lead_queue)}",
        "email": email,
        "website": normalize_website_url(website),
        "domain": domain,
        "full_name": full_name,
        "has_meeting": has_meeting,
        "meeting_booked": has_meeting,
        "source": "manual",
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    enrich_lead_data(lead)
    lead_queue.append(lead)
    save_leads(lead_queue, pushed_leads, rejected_leads, already_booked_leads)
    return lead


@app.post("/api/leads/{lead_id}/push")
async def push_to_hubspot(lead_id: str, request: Request):
    # Get the edited data from the modal
    push_data = await request.json()
    
    # Reload current leads
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    
    lead = None
    for l in lead_queue:
        if l['id'] == lead_id:
            lead = l
            break
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    if not HUBSPOT_AVAILABLE:
        raise HTTPException(status_code=503, detail="HubSpot integration not available. Please install hubspot_managers modules.")
    
    try:
        company_manager = CompanyManager()
        contact_manager = ContactManager()
        
        # Get values from modal
        ai_status = push_data.get('ai_status', 'To Do')
        if ai_status is None:
            ai_status = 'To Do'
        company_owner = push_data.get('company_owner', '')
        priority = push_data.get('priority', 'Medium')
        demo_request = push_data.get('demo_request', 'true')
        
        logger.info(f"Push data received: ai_status={ai_status!r}, priority={priority}, demo_request={demo_request}, company_owner={company_owner}")
        
        # Build company properties from modal values
        # IMPORTANT: Use correct HubSpot internal property names!
        # - demo_request: 'true' or 'false'
        # - ai_finding_status: only sent when not Blank (HubSpot has no blank enum value)
        # - priority1 (NOT priority!): 'Very high', 'High', 'Medium', 'Low'
        company_props = {
            'demo_request': demo_request,
            'priority1': priority,  # HubSpot uses priority1, not priority!
            'description': lead.get('description', '')
        }
        if ai_status and str(ai_status).strip():
            company_props['ai_finding_status'] = ai_status.strip()
        
        # Add company owner if provided
        if company_owner:
            company_props['hubspot_owner_id'] = company_owner
        
        if lead.get('is_agency'):
            company_props['is_agency'] = 'Yes'
        
        industry_hubspot = normalize_industry_for_hubspot(lead.get('industry') or '')
        if industry_hubspot:
            company_props['industry'] = industry_hubspot
        
        # Generate company name from domain (without TLD)
        def domain_to_company_name(domain):
            if not domain:
                return "Unknown"
            d = domain.replace('https://', '').replace('http://', '').replace('www.', '')
            d = d.split('/')[0]  # Remove path
            parts = d.split('.')
            if len(parts) > 1:
                parts.pop()  # Remove TLD
            name = '-'.join(parts)
            return name.capitalize()
        
        company_domain = (push_data.get('company_domain') or lead.get('domain') or lead.get('website') or '').strip()
        if not company_domain and lead.get('email'):
            # Derive domain from email (e.g. john@acme.com -> acme.com), skip consumer providers
            email_domain = lead['email'].split('@')[-1].strip().lower()
            skip_domains = {'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com', 'protonmail.com', 'mail.com', 'live.com', 'me.com', 'ymail.com', 'googlemail.com'}
            if email_domain not in skip_domains:
                company_domain = email_domain
                logger.info(f"No company domain; using email domain: {company_domain}")
        company_name = push_data.get('company_name') or domain_to_company_name(company_domain) or 'Unknown'
        
        logger.info(f"Creating/updating company '{company_name}' with domain '{company_domain}' and props: {company_props}")
        
        try:
            company = company_manager.find_or_create(
                name=company_name,
                domain=company_domain,
                industry=industry_hubspot,
                properties=company_props
            )
        except ValueError as ve:
            logger.error(f"Company create failed: {ve}")
            raise HTTPException(status_code=500, detail=f"Failed to create company in HubSpot: {ve}") from ve
        
        if not company:
            raise HTTPException(status_code=500, detail="Failed to create company in HubSpot (no company returned). Check server logs.")
        
        company_id = company['id']
        
        # Always update the company with all modal values (even if it existed)
        if company_id:
            update_props = {
                'demo_request': demo_request,
                'priority1': priority,  # HubSpot uses priority1!
            }
            if ai_status and str(ai_status).strip():
                update_props['ai_finding_status'] = ai_status.strip()
            if company_owner:
                update_props['hubspot_owner_id'] = company_owner
            
            logger.info(f"Updating company {company_id} with props: {update_props}")
            result = company_manager.update(company_id, update_props)
            if result:
                logger.info(f"Company {company_id} updated successfully with: {update_props}")
            else:
                logger.error(f"Failed to update company {company_id}")
        
        # Build contact properties from modal values
        first_name = push_data.get('contact_firstname')
        last_name = push_data.get('contact_lastname')
        
        contact_props = {}
        contact_owner = push_data.get('contact_owner')
        if contact_owner:
            contact_props['hubspot_owner_id'] = contact_owner
        
        # Add LinkedIn post tracking properties if available
        if lead.get('post_source_auto'):
            contact_props['linkedin_post_source'] = lead.get('post_source_auto')
        if lead.get('post_creator'):
            contact_props['linkedin_post_creator'] = lead.get('post_creator')
        if lead.get('post_date'):
            contact_props['linkedin_post_date'] = lead.get('post_date')
        if lead.get('post_track'):
            contact_props['linkedin_post_track'] = lead.get('post_track')
        
        logger.info(f"Creating contact with properties: {contact_props}")
        
        contact = contact_manager.find_or_create(
            email=push_data.get('contact_email') or lead['email'],
            firstname=first_name,
            lastname=last_name,
            company_id=company_id,
            properties=contact_props
        )
        
        contact_id = contact['id'] if contact else None
        
        # Update contact with owner if it existed
        if contact_id and contact_owner:
            contact_manager.update(contact_id, {'hubspot_owner_id': contact_owner})
            logger.info(f"Updated contact {contact_id} with owner: {contact_owner}")
        
        # Update lead with pushed info
        lead['status'] = 'pushed'
        lead['hubspot_company_id'] = company_id
        lead['hubspot_contact_id'] = contact_id
        lead['company_name'] = push_data.get('company_name') or lead.get('company_name')
        lead['priority'] = push_data.get('priority', lead['priority'])
        
        lead_queue.remove(lead)
        pushed_leads.append(lead)
        save_leads(lead_queue, pushed_leads, rejected_leads, already_booked_leads)
        
        # Auto-generate demo request email and prospect files if this is a demo request
        prospect_created = False
        if is_demo_request(lead) and PROSPECT_GENERATION_AVAILABLE:
            try:
                # Get settings for paths
                try:
                    settings = get_settings()
                    prospects_base_path = settings.prospect.prospects_base_path
                except Exception:
                    # Fallback path if settings not available
                    workspace_root = Path(__file__).parent.parent.parent.parent
                    prospects_base_path = workspace_root / "zenyt_sales" / "prospects"
                
                # Get contact and company info
                contact_email = push_data.get('contact_email') or lead.get('email', '')
                contact_firstname = push_data.get('contact_firstname') or lead.get('firstname', '')
                contact_lastname = push_data.get('contact_lastname') or lead.get('lastname', '')
                website_url = push_data.get('company_domain') or lead.get('website') or lead.get('domain', '')
                industry = lead.get('industry') or company_props.get('industry')
                
                # Generate email
                email_data = generate_demo_request_email(
                    contact_email=contact_email,
                    contact_firstname=contact_firstname,
                    contact_lastname=contact_lastname,
                    company_name=company_name,
                    website_url=website_url,
                    industry=industry,
                    company_country=None  # Could be added from Apollo data
                )
                
                # Create prospect folder
                prospect_folder = create_prospect_folder(
                    prospects_base_path=prospects_base_path,
                    company_name=company_name
                )
                
                # Create campaign overview
                contact_full_name = f"{contact_firstname} {contact_lastname}".strip() or contact_email.split('@')[0]
                create_campaign_overview(
                    folder_path=prospect_folder,
                    company_name=company_name,
                    company_domain=company_domain,
                    contact_name=contact_full_name,
                    contact_email=contact_email,
                    website_url=website_url,
                    industry=industry,
                    is_agency=email_data.get('is_agency', False),
                    agency_name=email_data.get('agency_name')
                )
                
                # Create touch 1 file
                create_touch_1_file(
                    folder_path=prospect_folder,
                    contact_firstname=contact_firstname,
                    contact_lastname=contact_lastname,
                    contact_email=contact_email,
                    company_name=company_name,
                    website_url=website_url,
                    email_data=email_data,
                    is_agency=email_data.get('is_agency', False),
                    agency_name=email_data.get('agency_name')
                )
                
                prospect_created = True
                logger.info(f"✅ Created demo request prospect: {prospect_folder}")
                
            except Exception as e:
                logger.error(f"Failed to create prospect files: {e}", exc_info=True)
                # Don't fail the push if prospect creation fails
        
        response_data = {
            "success": True,
            "company_id": company_id,
            "contact_id": contact_id,
            "prospect_created": prospect_created
        }
        
        if prospect_created:
            response_data["prospect_folder"] = str(prospect_folder)
        
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pushing to HubSpot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/leads/{lead_id}/reject")
async def reject_lead(lead_id: str, request: Request):
    data = await request.json()
    reason = data.get('reason', 'No reason provided')
    
    # Reload current leads
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    
    lead = None
    for l in lead_queue:
        if l['id'] == lead_id:
            lead = l
            break
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    lead['status'] = 'rejected'
    lead['rejected_reason'] = reason
    lead['rejected_at'] = datetime.now().isoformat()
    
    lead_queue.remove(lead)
    rejected_leads.append(lead)
    save_leads(lead_queue, pushed_leads, rejected_leads, already_booked_leads)
    
    return {"success": True}


@app.post("/api/leads/{lead_id}/already-booked")
async def mark_lead_already_booked(lead_id: str, request: Request):
    """Mark a pending lead as already booked (meeting in Calendly etc.)"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    reason = (data or {}).get('reason', '') or None
    
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    
    lead = None
    for l in lead_queue:
        if l['id'] == lead_id:
            lead = l
            break
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    lead['status'] = 'already_booked'
    lead['already_booked_at'] = datetime.now().isoformat()
    if reason:
        lead['already_booked_reason'] = reason
    # Keep rejected_reason if they were previously rejected with a booked reason (migration case)
    
    lead_queue.remove(lead)
    already_booked_leads.append(lead)
    save_leads(lead_queue, pushed_leads, rejected_leads, already_booked_leads)
    
    return {"success": True}


@app.post("/api/leads/{lead_id}/enrich")
async def enrich_lead_apollo(lead_id: str):
    """Manually trigger Apollo enrichment for a specific lead"""
    try:
        # Reload current leads
        lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
        
        # Find lead by ID across all lists
        lead = None
        lead_list = None
        
        for l in lead_queue:
            if l['id'] == lead_id:
                lead = l
                lead_list = 'pending'
                break
        
        if not lead:
            for l in pushed_leads:
                if l['id'] == lead_id:
                    lead = l
                    lead_list = 'pushed'
                    break
        
        if not lead:
            for l in rejected_leads:
                if l['id'] == lead_id:
                    lead = l
                    lead_list = 'rejected'
                    break
        
        if not lead:
            for l in already_booked_leads:
                if l['id'] == lead_id:
                    lead = l
                    lead_list = 'already_booked'
                    break
        
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        
        # Get domain from lead
        domain = lead.get('website') or lead.get('domain')
        if not domain:
            return {"success": False, "error": "No domain found for lead"}
        
        logger.info(f"🔄 Manual Apollo enrichment for {domain}")
        
        # Force re-enrichment by calling enrich_lead_data
        enrich_lead_data(lead)
        
        # Save the updated lead
        save_leads(lead_queue, pushed_leads, rejected_leads, already_booked_leads)
        
        logger.info(f"✅ Successfully re-enriched {domain}")
        
        return {
            "success": True,
            "lead": lead,
            "message": f"Successfully enriched {lead.get('company_name', domain)}"
        }
        
    except Exception as e:
        logger.error(f"❌ Error enriching lead {lead_id}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/leads/webhook")
async def receive_webhook_lead(request: Request):
    data = await request.json()
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    original_date = data.get('created_at') or datetime.now().isoformat()
    website = data.get('website', '')
    email = data.get('email', '')

    lead = {
        "id": f"lead_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(lead_queue)}",
        "email": email,
        "website": normalize_website_url(website) if website else website,
        "full_name": data.get('full_name'),
        "company_name": data.get('company_name'),
        "industry": data.get('industry'),
        "description": data.get('description'),
        "priority": data.get('priority', 'Medium'),
        "icp_score": data.get('icp_score', 0),
        "icp_reasons": data.get('icp_reasons', []),
        "is_agency": data.get('is_agency', False),
        "is_ecommerce": data.get('is_ecommerce', False),
        "employee_count": data.get('employee_count'),
        "employee_range": data.get('employee_range'),
        "revenue": data.get('revenue'),
        "revenue_range": data.get('revenue_range'),
        "linkedin_url": data.get('linkedin_url'),
        "email_domain_company": data.get('email_domain_company'),
        "email_domain_linkedin": data.get('email_domain_linkedin'),
        "email_domain_is_agency": data.get('email_domain_is_agency'),
        "has_meeting": data.get('has_meeting', False),
        "meeting_booked": data.get('meeting_booked', data.get('has_meeting', False)),
        "source": data.get('source', 'webflow'),
        "created_at": original_date,
        "status": "pending",
    }
    domain = extract_domain_from_url(lead.get("website") or "")
    if domain:
        lead["domain"] = domain
        enrich_lead_data(lead)
    lead_queue.append(lead)
    save_leads(lead_queue, pushed_leads, rejected_leads, already_booked_leads)
    logger.info(f"Received lead from webhook: {lead['email']} (date: {original_date})")
    return {"status": "received", "lead_id": lead["id"]}


@app.get("/api/post-performance")
async def get_post_performance():
    """
    Calculate performance metrics for each LinkedIn post with real-time HubSpot data.
    Returns: Reactions, Comments, Demos, Meetings, Fast Tracks, Wins, and all ROI metrics
    """
    # Load all leads
    lead_queue, pushed_leads, rejected_leads, already_booked_leads = load_leads()
    all_leads = lead_queue + pushed_leads + rejected_leads + already_booked_leads
    
    # Load post database
    post_db_path = Path(__file__).parent.parent / "post_database.json"
    posts = []
    if post_db_path.exists():
        with open(post_db_path, 'r') as f:
            post_db = json.load(f)
            posts = post_db.get('posts', [])
    
    # Load historical data
    historical_path = Path(__file__).parent.parent / "historical_data.json"
    if historical_path.exists():
        try:
            with open(historical_path, 'r') as f:
                historical_data = json.load(f)
                historical_posts = historical_data.get('posts', [])
                # Transform historical posts to match current format
                for hp in historical_posts:
                    hp['created_at'] = hp.get('date', '')
                    hp['link'] = hp.get('tracking_link', '')
                posts.extend(historical_posts)
                logger.info(f"Loaded {len(historical_posts)} historical posts for performance view")
        except Exception as e:
            logger.error(f"Error loading historical posts: {e}")
    
    if not posts:
        return {"posts": []}
    
    # Calculate metrics for each post
    post_metrics = []
    
    for post in posts:
        # Handle both historical (uses 'date') and current posts (uses 'date' or 'created_at')
        post_date = post.get('date') or post.get('created_at', '')
        post_id = f"{post['creator']}_{post_date}" if not post.get('post_id') else post.get('post_id')
        
        # Find all leads from this post
        post_leads = [
            lead for lead in all_leads 
            if lead.get('post_source_final') == post_id or 
               lead.get('post_source') == post_id or
               (lead.get('post_creator') == post['creator'] and lead.get('post_date') == post_date)
        ]
        
        # Calculate basic metrics
        total_demos = len(post_leads)
        icp_fit_leads = len([l for l in post_leads if l.get('in_hubspot') or l in pushed_leads])
        push_rate = (icp_fit_leads / total_demos * 100) if total_demos > 0 else 0
        cost_per_demo = (post['cost'] / total_demos) if total_demos > 0 else 0
        cost_per_icp = (post['cost'] / icp_fit_leads) if icp_fit_leads > 0 else 0
        
        # Get HubSpot metrics (real-time or from historical data)
        hubspot_metrics = {
            'meeting_requests': post.get('meetings_booked', 0),
            'meetings_done': post.get('meetings_done', 0),
            'fast_tracks': post.get('fast_tracks', 0),
            'wins': post.get('deals_won', 0),
            'total_deals': 0
        }
        
        # If this is not a historical post, fetch live HubSpot data
        if not post.get('post_id'):  # Historical posts have post_id field
            if hubspot_sync:
                try:
                    live_metrics = hubspot_sync.get_post_metrics_from_hubspot(
                        post_id,
                        post['creator'],
                        post.get('date', post.get('created_at', ''))
                    )
                    # Only override if we got real data
                    if live_metrics['total_deals'] > 0:
                        hubspot_metrics = live_metrics
                except Exception as e:
                    logger.error(f"Error fetching HubSpot metrics for {post_id}: {e}")
        
        # Calculate cost per meeting and cost per win
        cost_per_meeting = (post['cost'] / hubspot_metrics['meetings_done']) if hubspot_metrics['meetings_done'] > 0 else 0
        cost_per_win = (post['cost'] / hubspot_metrics['wins']) if hubspot_metrics['wins'] > 0 else 0
        
        post_metrics.append({
            "post_id": post_id,
            "creator": post['creator'],
            "date": post_date,
            "track": post['track'],
            "cost": post['cost'],
            "link": post.get('link', ''),
            "post_url": post.get('post_url', ''),
            
            # Manual metrics (from post database)
            "reactions": post.get('reactions', 0),
            "comments": post.get('comments', 0),
            "impressions": post.get('impressions', 0),
            
            # Pure CTA (tracking link)
            "pure_cta": total_demos,
            "total_demos": total_demos,
            "icp_fit_leads": icp_fit_leads,
            "push_rate": round(push_rate, 1),
            
            # HubSpot metrics (real-time)
            "meeting_requests": hubspot_metrics['meeting_requests'],
            "meetings_done": hubspot_metrics['meetings_done'],
            "fast_tracks": hubspot_metrics['fast_tracks'],
            "wins": hubspot_metrics['wins'],
            "total_deals": hubspot_metrics['total_deals'],
            
            # Cost metrics
            "cost_per_demo": round(cost_per_demo, 2) if cost_per_demo > 0 else 0,
            "cost_per_icp": round(cost_per_icp, 2) if cost_per_icp > 0 else 0,
            "cost_per_meeting": round(cost_per_meeting, 2) if cost_per_meeting > 0 else 0,
            "cost_per_win": round(cost_per_win, 2) if cost_per_win > 0 else 0,
            
            "created_at": post.get('created_at', ''),
            "hubspot_synced": hubspot_sync is not None
        })
    
    # Sort by date (most recent first)
    post_metrics.sort(key=lambda x: x['created_at'], reverse=True)
    
    return {"posts": post_metrics, "hubspot_connected": hubspot_sync is not None}


@app.post("/api/post-performance/{post_id}/update-metrics")
async def update_post_metrics(post_id: str, request: Request):
    """
    Update manual metrics for a post (reactions, comments, impressions)
    
    Args:
        post_id: Post ID (e.g., "laura_jan15")
        request: JSON body with metrics to update
    
    Returns:
        Success status
    """
    data = await request.json()
    
    # Load post database
    post_db_path = Path(__file__).parent.parent / "post_database.json"
    if not post_db_path.exists():
        raise HTTPException(status_code=404, detail="Post database not found")
    
    with open(post_db_path, 'r') as f:
        post_db = json.load(f)
    
    # Find and update the post
    post_found = False
    for post in post_db['posts']:
        if f"{post['creator']}_{post['date']}" == post_id:
            # Update manual metrics
            if 'reactions' in data:
                post['reactions'] = data['reactions']
            if 'comments' in data:
                post['comments'] = data['comments']
            if 'impressions' in data:
                post['impressions'] = data['impressions']
            if 'post_url' in data:
                post['post_url'] = data['post_url']
            
            post_found = True
            break
    
    if not post_found:
        raise HTTPException(status_code=404, detail=f"Post not found: {post_id}")
    
    # Save updated database
    with open(post_db_path, 'w') as f:
        json.dump(post_db, f, indent=2)
    
    logger.info(f"✅ Updated metrics for {post_id}")
    return {"success": True, "post_id": post_id}


@app.get("/api/analytics")
async def get_analytics():
    """
    Analytics for INBOUND STRATEGY ONLY (dashboard leads).
    Tracks organic + post-sourced traffic that came through Webflow form.
    Excludes other deals/meetings not related to dashboard leads.
    Filters to October 2025+ (when influencer work started).
    """
    from datetime import datetime, timezone
    from collections import defaultdict
    
    try:
        # Load all dashboard leads and tag each with bucket (single source of truth for header vs Overview)
        pending, pushed, rejected, already_booked = load_leads()
        for lead in pending:
            lead['_bucket'] = 'pending'
        for lead in pushed:
            lead['_bucket'] = 'pushed'
        for lead in rejected:
            lead['_bucket'] = 'rejected'
        for lead in already_booked:
            lead['_bucket'] = 'already_booked'
        all_leads = pending + pushed + rejected + already_booked
        
        # Filter to October 2025 onwards (when influencer work started)
        # Use date-only comparison to avoid timezone issues
        OCTOBER_2025_DATE = datetime(2025, 10, 1).date()
        filtered_leads = []
        for lead in all_leads:
            created_at = lead.get('created_at', '')
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    if dt.date() >= OCTOBER_2025_DATE:
                        filtered_leads.append(lead)
                except:
                    pass
        
        all_leads = filtered_leads
        
        # Build email list of dashboard leads for HubSpot filtering
        dashboard_emails = set()
        for lead in all_leads:
            email = lead.get('email') or lead.get('work_email')
            if email:
                dashboard_emails.add(email.lower())
        
        # Load posts (current)
        post_db_path = Path(__file__).parent.parent / "post_database.json"
        if post_db_path.exists():
            with open(post_db_path, 'r') as f:
                post_db = json.load(f)
                posts = post_db.get('posts', [])
        else:
            posts = []
        
        # Load historical data
        historical_path = Path(__file__).parent.parent / "historical_data.json"
        historical_posts = []
        if historical_path.exists():
            try:
                with open(historical_path, 'r') as f:
                    historical_data = json.load(f)
                    historical_posts = historical_data.get('posts', [])
                    logger.info(f"Loaded {len(historical_posts)} historical posts")
            except Exception as e:
                logger.error(f"Error loading historical data: {e}")
        
        # Combine posts and historical posts, filter to October 2025+
        # Use date-only comparison to avoid timezone issues
        OCTOBER_2025_DATE = datetime(2025, 10, 1).date()
        all_posts = []
        for post in (posts + historical_posts):
            created_at = post.get('date') or post.get('created_at', '')
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    # Compare dates only (not datetime with timezone)
                    if dt.date() >= OCTOBER_2025_DATE:
                        all_posts.append(post)
                except Exception as e:
                    logger.debug(f"Could not parse date {created_at}: {e}")
                    pass
        
        # Initialize aggregation structures
        monthly_data = defaultdict(lambda: {
            'organic': 0,
            'post_sourced': 0,
            'icp_fit': 0,
            'pushed_to_crm': 0,
            'already_booked': 0,
            'pending': 0,
            'rejected': 0,
            'demos': 0,
            'meetings': 0,
            'fast_tracks': 0,
            'wins': 0,
            'total_cost': 0,
            'reactions': 0,
            'comments': 0,
            'impressions': 0
        })
        
        # Totals (bucket-based counts align with header stats)
        totals = {
            'organic': 0,
            'post_sourced': 0,
            'icp_fit': 0,
            'pushed_to_crm': 0,
            'already_booked': 0,
            'pending': 0,
            'rejected': 0,
            'demos': 0,
            'meetings': 0,
            'fast_tracks': 0,
            'wins': 0,
            'total_cost': 0,
            'reactions': 0,
            'comments': 0,
            'impressions': 0,
            'total_leads': len(all_leads)
        }
        
        # Process leads
        for lead in all_leads:
            created_at = lead.get('created_at', '')
            if created_at:
                # Parse date to get month
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    month_key = dt.strftime('%Y-%m')  # e.g., "2026-01"
                except:
                    month_key = None
            else:
                month_key = None
            
            bucket = lead.get('_bucket', '')
            
            # Categorize: organic vs post-sourced
            post_source = lead.get('post_source_auto') or lead.get('post_source')
            if post_source:
                totals['post_sourced'] += 1
                if month_key:
                    monthly_data[month_key]['post_sourced'] += 1
            else:
                totals['organic'] += 1
                if month_key:
                    monthly_data[month_key]['organic'] += 1
            
            # ICP fit
            if lead.get('qualified', False) or lead.get('icp_score', 0) >= 70:
                totals['icp_fit'] += 1
                if month_key:
                    monthly_data[month_key]['icp_fit'] += 1
            
            # Bucket-based counts (same source of truth as header)
            if bucket == 'pushed':
                totals['pushed_to_crm'] += 1
                if month_key:
                    monthly_data[month_key]['pushed_to_crm'] += 1
            elif bucket == 'already_booked':
                totals['already_booked'] += 1
                if month_key:
                    monthly_data[month_key]['already_booked'] += 1
            elif bucket == 'pending':
                totals['pending'] += 1
                if month_key:
                    monthly_data[month_key]['pending'] += 1
            elif bucket == 'rejected':
                totals['rejected'] += 1
                if month_key:
                    monthly_data[month_key]['rejected'] += 1
            
            # Demos (if has meeting)
            if lead.get('has_meeting', False):
                totals['demos'] += 1
                if month_key:
                    monthly_data[month_key]['demos'] += 1
        
        # Process posts for cost and engagement metrics ONLY
        # (NOT meetings/deals - those come from actual dashboard leads)
        for post in all_posts:
            created_at = post.get('date') or post.get('created_at', '')
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    month_key = dt.strftime('%Y-%m')
                except:
                    continue
            else:
                continue
            
            cost = post.get('cost', 0)
            reactions = post.get('reactions', 0)
            comments = post.get('comments', 0)
            impressions = post.get('impressions', 0) or 0
            
            # Only add cost and engagement metrics, NOT meetings/deals
            # (those will be counted from actual dashboard leads)
            totals['total_cost'] += cost
            totals['reactions'] += reactions
            totals['comments'] += comments
            totals['impressions'] += impressions
            
            if month_key:
                monthly_data[month_key]['total_cost'] += cost
                monthly_data[month_key]['reactions'] += reactions
                monthly_data[month_key]['comments'] += comments
                monthly_data[month_key]['impressions'] += impressions
        
        # Get HubSpot metrics ONLY for dashboard leads (inbound strategy)
        if hubspot_sync and dashboard_emails:
            try:
                hubspot_client = hubspot_sync.client
                
                # For each dashboard lead email, check for deals in HubSpot
                for email in dashboard_emails:
                    try:
                        # Search for contact by email
                        search_result = hubspot_client.search_contacts(
                            filter_groups=[{
                                "filters": [{
                                    "propertyName": "email",
                                    "operator": "EQ",
                                    "value": email
                                }]
                            }],
                            properties=["email"],
                            limit=1
                        )
                        
                        if not search_result or not search_result.get('results'):
                            continue
                        
                        contact_id = search_result['results'][0]['id']
                        
                        # Get deals associated with this contact
                        associations = hubspot_client.client.crm.contacts.associations_api.get_all(
                            contact_id=contact_id,
                            to_object_type="deals"
                        )
                        
                        if not associations or not associations.results:
                            continue
                        
                        # Fetch each deal
                        for assoc in associations.results:
                            deal_id = assoc.to_object_id
                            deal = hubspot_client.client.crm.deals.basic_api.get_by_id(
                                deal_id=deal_id,
                                properties=["dealstage", "createdate", "closedate"]
                            )
                            
                            props = deal.properties
                            deal_stage = props.get('dealstage', '')
                            created_at = props.get('createdate', '')
                            
                            # Filter to October 2025+ (date-only comparison)
                            if created_at:
                                try:
                                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                    if dt.date() < OCTOBER_2025_DATE:
                                        continue
                                    month_key = dt.strftime('%Y-%m')
                                except:
                                    continue
                            else:
                                continue
                            
                            # Count meetings (any deal that progressed)
                            if deal_stage and deal_stage not in ['', 'appointmentscheduled']:
                                totals['meetings'] += 1
                                if month_key:
                                    monthly_data[month_key]['meetings'] += 1
                            
                            # Count fast tracks
                            if deal_stage and 'fast' in deal_stage.lower():
                                totals['fast_tracks'] += 1
                                if month_key:
                                    monthly_data[month_key]['fast_tracks'] += 1
                            
                            # Count wins
                            if deal_stage and 'closedwon' in deal_stage.lower():
                                totals['wins'] += 1
                                if month_key:
                                    monthly_data[month_key]['wins'] += 1
                    
                    except Exception as e:
                        # Skip individual contact errors
                        logger.debug(f"Could not fetch deals for {email}: {e}")
                        continue
                        
            except Exception as e:
                logger.error(f"Error fetching HubSpot deals for analytics: {e}")
        
        # Convert monthly_data to sorted list
        monthly_breakdown = []
        for month_key in sorted(monthly_data.keys(), reverse=True):
            data = monthly_data[month_key]
            monthly_breakdown.append({
                'month': month_key,
                'month_label': datetime.strptime(month_key, '%Y-%m').strftime('%B %Y'),
                **data
            })
        
        # Calculate WEEKLY aggregation
        from datetime import timedelta
        weekly_data = defaultdict(lambda: {
            'organic': 0,
            'post_sourced': 0,
            'icp_fit': 0,
            'pushed_to_crm': 0,
            'already_booked': 0,
            'pending': 0,
            'rejected': 0,
            'demos': 0,
            'meetings': 0,
            'fast_tracks': 0,
            'wins': 0,
            'total_cost': 0,
            'reactions': 0,
            'comments': 0,
            'impressions': 0
        })
        
        # Process leads by week (bucket-based counts align with header)
        for lead in all_leads:
            created_at = lead.get('created_at', '')
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    # Get week number (ISO week)
                    week_key = dt.strftime('%Y-W%W')  # e.g., "2026-W02"
                except:
                    continue
                
                bucket = lead.get('_bucket', '')
                
                # Categorize: organic vs post-sourced
                post_source = lead.get('post_source_auto') or lead.get('post_source')
                if post_source:
                    weekly_data[week_key]['post_sourced'] += 1
                else:
                    weekly_data[week_key]['organic'] += 1
                
                # ICP fit
                if lead.get('qualified', False) or lead.get('icp_score', 0) >= 70:
                    weekly_data[week_key]['icp_fit'] += 1
                
                # Bucket-based counts
                if bucket == 'pushed':
                    weekly_data[week_key]['pushed_to_crm'] += 1
                elif bucket == 'already_booked':
                    weekly_data[week_key]['already_booked'] += 1
                elif bucket == 'pending':
                    weekly_data[week_key]['pending'] += 1
                elif bucket == 'rejected':
                    weekly_data[week_key]['rejected'] += 1
                
                # Demos
                if lead.get('has_meeting', False):
                    weekly_data[week_key]['demos'] += 1
        
        # Process posts by week (cost and engagement only)
        for post in all_posts:
            created_at = post.get('date') or post.get('created_at', '')
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    week_key = dt.strftime('%Y-W%W')
                except:
                    continue
                
                cost = post.get('cost', 0)
                reactions = post.get('reactions', 0)
                comments = post.get('comments', 0)
                impressions = post.get('impressions', 0) or 0
                
                weekly_data[week_key]['total_cost'] += cost
                weekly_data[week_key]['reactions'] += reactions
                weekly_data[week_key]['comments'] += comments
                weekly_data[week_key]['impressions'] += impressions
        
        # Convert weekly_data to sorted list (last 12 weeks)
        weekly_breakdown = []
        for week_key in sorted(weekly_data.keys(), reverse=True)[:12]:
            data = weekly_data[week_key]
            # Convert week key to readable format
            try:
                year, week = week_key.split('-W')
                week_label = f"Week {week}, {year}"
            except:
                week_label = week_key
            
            weekly_breakdown.append({
                'week': week_key,
                'week_label': week_label,
                **data
            })
        
        # Calculate ratios
        organic_percent = round(totals['organic'] / totals['total_leads'] * 100, 1) if totals['total_leads'] > 0 else 0
        post_sourced_percent = round(totals['post_sourced'] / totals['total_leads'] * 100, 1) if totals['total_leads'] > 0 else 0
        icp_fit_rate = round(totals['icp_fit'] / totals['total_leads'] * 100, 1) if totals['total_leads'] > 0 else 0
        crm_push_rate = round(totals['pushed_to_crm'] / totals['total_leads'] * 100, 1) if totals['total_leads'] > 0 else 0
        
        return {
            'totals': {
                **totals,
                'organic_percent': organic_percent,
                'post_sourced_percent': post_sourced_percent,
                'icp_fit_rate': icp_fit_rate,
                'crm_push_rate': crm_push_rate
            },
            'monthly': monthly_breakdown,
            'weekly': weekly_breakdown,
            'hubspot_connected': hubspot_sync is not None,
            'last_updated': datetime.now().isoformat()
        }
    
    except Exception as e:
        logger.error(f"Error generating analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analytics/export")
async def export_analytics_csv(period: str = "monthly"):
    """
    Export analytics data as CSV
    
    Args:
        period: "weekly" or "monthly"
        
    Returns:
        CSV file download
    """
    from fastapi.responses import StreamingResponse
    import io
    import csv
    
    try:
        # Get analytics data
        analytics = await get_analytics()
        
        # Choose data based on period
        if period == "weekly":
            data_key = 'weekly'
            period_col = 'week'
            label_col = 'week_label'
        else:
            data_key = 'monthly'
            period_col = 'month'
            label_col = 'month_label'
        
        breakdown = analytics.get(data_key, [])
        
        if not breakdown:
            raise HTTPException(status_code=404, detail="No data available for export")
        
        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow([
            'Period',
            'Organic Leads',
            'Post-Sourced Leads',
            'ICP Fit',
            'Pushed to CRM',
            'Already Booked',
            'Pending',
            'Rejected',
            'Demos',
            'Meetings',
            'Fast Tracks',
            'Wins',
            'Total Cost ($)',
            'Reactions',
            'Comments',
            'Impressions'
        ])
        
        # Data rows
        for row in breakdown:
            writer.writerow([
                row.get(label_col, row.get(period_col)),
                row.get('organic', 0),
                row.get('post_sourced', 0),
                row.get('icp_fit', 0),
                row.get('pushed_to_crm', 0),
                row.get('already_booked', 0),
                row.get('pending', 0),
                row.get('rejected', 0),
                row.get('demos', 0),
                row.get('meetings', 0),
                row.get('fast_tracks', 0),
                row.get('wins', 0),
                row.get('total_cost', 0),
                row.get('reactions', 0),
                row.get('comments', 0),
                row.get('impressions', 0)
            ])
        
        # Add totals row
        totals = analytics.get('totals', {})
        writer.writerow([])  # Empty row
        writer.writerow([
            'TOTAL',
            totals.get('organic', 0),
            totals.get('post_sourced', 0),
            totals.get('icp_fit', 0),
            totals.get('pushed_to_crm', 0),
            totals.get('already_booked', 0),
            totals.get('pending', 0),
            totals.get('rejected', 0),
            totals.get('demos', 0),
            totals.get('meetings', 0),
            totals.get('fast_tracks', 0),
            totals.get('wins', 0),
            totals.get('total_cost', 0),
            totals.get('reactions', 0),
            totals.get('comments', 0),
            totals.get('impressions', 0)
        ])
        
        # Prepare file for download
        output.seek(0)
        filename = f"zenyt_analytics_{period}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        logger.error(f"Error exporting CSV: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    print("""
╔════════════════════════════════════════════════════════════╗
║            🚀 Zenyt Lead Dashboard                         ║
║   Review leads and push to HubSpot with one click         ║
╚════════════════════════════════════════════════════════════╝
    """)
    uvicorn.run(app, host="0.0.0.0", port=3000)
