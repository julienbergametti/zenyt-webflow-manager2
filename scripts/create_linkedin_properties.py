#!/usr/bin/env python3
"""
Create LinkedIn Post Tracking Properties in HubSpot
"""
import os
import sys
import requests
import logging
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

# LinkedIn tracking properties
LINKEDIN_PROPERTIES = [
    {
        "name": "linkedin_post_source",
        "label": "LinkedIn Post Source",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Post tracking code (e.g., l15)"
    },
    {
        "name": "linkedin_post_creator",
        "label": "LinkedIn Post Creator",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Creator of the LinkedIn post (e.g., laura, freddie)"
    },
    {
        "name": "linkedin_post_date",
        "label": "LinkedIn Post Date",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Date identifier of the post (e.g., jan15)"
    },
    {
        "name": "linkedin_post_track",
        "label": "LinkedIn Post Track",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
        "description": "Post track code (e.g., L15)"
    }
]

def create_property(access_token: str, property_def: dict) -> bool:
    """Create a single property in HubSpot"""
    try:
        url = "https://api.hubapi.com/crm/v3/properties/contacts"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "name": property_def["name"],
            "label": property_def["label"],
            "type": property_def["type"],
            "fieldType": property_def["fieldType"],
            "groupName": property_def["groupName"],
            "description": property_def.get("description", "")
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 201:
            logger.info(f"✅ Created property: {property_def['name']}")
            return True
        elif response.status_code == 409:
            logger.info(f"ℹ️  Property already exists: {property_def['name']}")
            return True
        else:
            logger.warning(f"⚠️  Could not create property {property_def['name']}: {response.status_code}")
            logger.warning(f"    Response: {response.text}")
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
    print("\n" + "="*60)
    print("📱 Creating LinkedIn Post Tracking Properties")
    print("="*60 + "\n")
    
    hubspot_token = os.getenv("HUBSPOT_ACCESS_TOKEN")
    if not hubspot_token:
        logger.error("❌ HUBSPOT_ACCESS_TOKEN not found in .env file")
        return
    
    created_count = 0
    for prop_def in LINKEDIN_PROPERTIES:
        print(f"Creating: {prop_def['label']}...")
        if create_property(hubspot_token, prop_def):
            created_count += 1
    
    print("\n" + "="*60)
    print(f"✅ Complete! Properties: {created_count}/{len(LINKEDIN_PROPERTIES)}")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
