#!/usr/bin/env python3
"""
Auto-create HubSpot Contact Properties for LinkedIn Tracking System

This script creates all necessary custom properties in HubSpot to track:
- Traffic source (organic vs post)
- Post attribution details
- Meeting tracking
- Pipeline progress (fast track, deal status)
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hubspot_managers.hubspot_client import HubSpotClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

# Define all custom properties needed
CONTACT_PROPERTIES = [
    # Traffic Source Properties
    {
        "name": "traffic_source",
        "label": "Traffic Source",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Source of lead traffic: organic or from LinkedIn post",
        "options": [
            {"label": "Organic", "value": "organic"},
            {"label": "LinkedIn Post", "value": "post"}
        ]
    },
    {
        "name": "post_code",
        "label": "Post Tracking Code",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "LinkedIn post tracking code (e.g., l15, f10, n03)"
    },
    {
        "name": "post_creator",
        "label": "Post Creator",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "LinkedIn post creator",
        "options": [
            {"label": "Laura", "value": "laura"},
            {"label": "Freddie", "value": "freddie"},
            {"label": "Nathan", "value": "nathan"}
        ]
    },
    {
        "name": "post_track",
        "label": "Creator Track",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Which creator's track the post belongs to",
        "options": [
            {"label": "Laura Track", "value": "laura_track"},
            {"label": "Freddie Track", "value": "freddie_track"}
        ]
    },
    {
        "name": "attribution_method",
        "label": "Attribution Method",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "How the lead was attributed to a post",
        "options": [
            {"label": "URL Tracking", "value": "url_tracking"},
            {"label": "Waalaxy Match", "value": "waalaxy_match"}
        ]
    },
    {
        "name": "attribution_confidence",
        "label": "Attribution Confidence",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Confidence level of post attribution",
        "options": [
            {"label": "High", "value": "high"},
            {"label": "Medium", "value": "medium"},
            {"label": "Low", "value": "low"}
        ]
    },
    
    # Meeting Tracking Properties
    {
        "name": "meeting_booked",
        "label": "Meeting Booked Source",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "How the meeting was booked",
        "options": [
            {"label": "Calendly", "value": "calendly"},
            {"label": "None", "value": "none"}
        ]
    },
    {
        "name": "meeting_booked_date",
        "label": "Meeting Booked Date",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "contactinformation",
        "description": "Date when the meeting was booked"
    },
    {
        "name": "meeting_completed",
        "label": "Meeting Completed",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "groupName": "contactinformation",
        "description": "Whether the meeting was completed"
    },
    {
        "name": "meeting_completed_date",
        "label": "Meeting Completed Date",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "contactinformation",
        "description": "Date when the meeting was completed"
    },
    {
        "name": "calendly_event_id",
        "label": "Calendly Event ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Calendly event URI for reference"
    },
    
    # Pipeline Progress Properties
    {
        "name": "is_fast_track",
        "label": "Is Fast Track",
        "type": "bool",
        "fieldType": "booleancheckbox",
        "groupName": "contactinformation",
        "description": "Whether the deal is in fast track"
    },
    {
        "name": "fast_track_date",
        "label": "Fast Track Date",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "contactinformation",
        "description": "Date when moved to fast track"
    },
    {
        "name": "deal_status",
        "label": "Deal Status",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "contactinformation",
        "description": "Current deal status",
        "options": [
            {"label": "Open", "value": "open"},
            {"label": "Won", "value": "won"},
            {"label": "Lost", "value": "lost"}
        ]
    },
    {
        "name": "deal_closed_date",
        "label": "Deal Closed Date",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "contactinformation",
        "description": "Date when deal was closed (won or lost)"
    }
]


def create_property(client: HubSpotClient, property_def: dict) -> bool:
    """
    Create a single property in HubSpot
    
    Args:
        client: HubSpot client instance
        property_def: Property definition dict
        
    Returns:
        True if created successfully, False if already exists or error
    """
    try:
        url = "https://api.hubapi.com/crm/v3/properties/contacts"
        
        payload = {
            "name": property_def["name"],
            "label": property_def["label"],
            "type": property_def["type"],
            "fieldType": property_def["fieldType"],
            "groupName": property_def["groupName"],
            "description": property_def.get("description", "")
        }
        
        # Add options for enumeration fields
        if property_def["type"] == "enumeration" and "options" in property_def:
            payload["options"] = property_def["options"]
        
        response = client._make_request("POST", url, json=payload)
        
        if response:
            logger.info(f"✅ Created property: {property_def['name']}")
            return True
        else:
            logger.warning(f"⚠️  Could not create property: {property_def['name']}")
            return False
            
    except Exception as e:
        error_msg = str(e)
        if "already exists" in error_msg.lower() or "409" in error_msg:
            logger.info(f"ℹ️  Property already exists: {property_def['name']}")
            return True
        else:
            logger.error(f"❌ Error creating property {property_def['name']}: {e}")
            return False


def main():
    """Create all HubSpot properties"""
    print("\n" + "="*60)
    print("🚀 HubSpot Custom Properties Setup")
    print("="*60 + "\n")
    
    # Initialize HubSpot client
    hubspot_token = os.getenv("HUBSPOT_ACCESS_TOKEN")
    if not hubspot_token:
        logger.error("❌ HUBSPOT_ACCESS_TOKEN not found in .env file")
        return
    
    client = HubSpotClient(access_token=hubspot_token)
    
    # Create all properties
    created_count = 0
    failed_count = 0
    
    for prop_def in CONTACT_PROPERTIES:
        if create_property(client, prop_def):
            created_count += 1
        else:
            failed_count += 1
    
    print("\n" + "="*60)
    print(f"✅ Setup Complete!")
    print(f"   Properties created/verified: {created_count}/{len(CONTACT_PROPERTIES)}")
    if failed_count > 0:
        print(f"   ⚠️  Failed: {failed_count}")
    print("="*60 + "\n")
    
    print("Next steps:")
    print("1. Visit HubSpot Settings → Properties → Contact Properties")
    print("2. Verify all properties are visible")
    print("3. Start tracking leads with full attribution! 🎯")
    print()


if __name__ == "__main__":
    main()
