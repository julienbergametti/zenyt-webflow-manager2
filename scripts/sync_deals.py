#!/usr/bin/env python3
"""
Deal Progress Sync CLI Tool

Syncs HubSpot deal progression back to contact properties:
- meeting_completed
- is_fast_track  
- deal_won

Run this periodically to keep dashboard metrics accurate!
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import logging

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hubspot_managers.hubspot_client import HubSpotClient
from hubspot_managers.deal_sync import DealProgressSync

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")


def main():
    """Run deal progress sync"""
    print("\n" + "="*60)
    print("🔄 HubSpot Deal Progress Sync")
    print("   Updating contact properties from deal stages")
    print("="*60 + "\n")
    
    # Get HubSpot token
    hubspot_token = os.getenv("HUBSPOT_ACCESS_TOKEN")
    if not hubspot_token:
        print("❌ HUBSPOT_ACCESS_TOKEN not found in .env file")
        return 1
    
    try:
        # Initialize HubSpot client
        print("🔌 Connecting to HubSpot...")
        client = HubSpotClient(access_token=hubspot_token)
        
        # Initialize deal sync
        deal_sync = DealProgressSync(client)
        
        # Run sync
        print("📊 Fetching and processing deals...\n")
        stats = deal_sync.sync_all_deals()
        
        # Print results
        print("\n" + "="*60)
        print("✅ Sync Complete!")
        print("="*60)
        print(f"   Deals processed: {stats['deals_processed']}")
        print(f"   Contacts updated: {stats['contacts_updated']}")
        print()
        print("   Properties updated:")
        print(f"   • meeting_completed: {stats['meeting_completed']} contacts")
        print(f"   • is_fast_track: {stats['fast_track']} contacts")
        print(f"   • deal_won: {stats['deal_won']} contacts")
        print("="*60 + "\n")
        
        print("💡 Tip: Run this script regularly to keep metrics up-to-date!")
        print("   Or add to crontab for automatic sync every hour.\n")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error during sync: {e}\n")
        logger.exception("Full error details:")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
