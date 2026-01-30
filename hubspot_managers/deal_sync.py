"""
Deal Progress Sync - Automatically Update Contact Properties from Deals

This module syncs HubSpot deal progression back to contact properties:
- meeting_completed: Set to true if deal progressed past appointment
- is_fast_track: Set to true if deal is in fast track stage
- deal_won: Set to true if deal closed won

IMPORTANT: This keeps your dashboard metrics accurate by tracking real pipeline progress
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class DealProgressSync:
    """Syncs deal progress back to contact properties"""
    
    def __init__(self, hubspot_client):
        """
        Initialize deal sync
        
        Args:
            hubspot_client: HubSpotClient instance
        """
        self.client = hubspot_client
        logger.info("Deal Progress Sync initialized")
    
    def sync_all_deals(self) -> Dict[str, int]:
        """
        Sync all deals and update contact properties
        
        Returns:
            Dict with counts of updated contacts
        """
        logger.info("🔄 Starting deal progress sync...")
        
        # Fetch all deals
        deals = self.client.get_all_deals(properties=[
            'dealstage',
            'closedate',
            'createdate',
            'dealname',
            'amount',
            'pipeline'
        ])
        
        logger.info(f"📊 Found {len(deals)} deals in HubSpot")
        
        # Track updates
        stats = {
            'deals_processed': 0,
            'contacts_updated': 0,
            'meeting_completed': 0,
            'fast_track': 0,
            'deal_won': 0
        }
        
        for deal in deals:
            try:
                updated = self._process_deal(deal)
                stats['deals_processed'] += 1
                
                if updated:
                    stats['contacts_updated'] += 1
                    if updated.get('meeting_completed'):
                        stats['meeting_completed'] += 1
                    if updated.get('is_fast_track'):
                        stats['fast_track'] += 1
                    if updated.get('deal_won'):
                        stats['deal_won'] += 1
                        
            except Exception as e:
                logger.error(f"Error processing deal {deal.get('id')}: {e}")
                continue
        
        logger.info(f"✅ Sync complete: {stats}")
        return stats
    
    def _process_deal(self, deal: Dict) -> Optional[Dict]:
        """
        Process a single deal and update associated contact
        
        Args:
            deal: Deal dictionary from HubSpot
            
        Returns:
            Dict of properties updated, or None if no update
        """
        deal_id = deal.get('id')
        props = deal.get('properties', {})
        
        # Get deal stage and pipeline
        deal_stage = props.get('dealstage', '').lower()
        pipeline = props.get('pipeline', '')
        
        # Determine property values
        properties_to_update = {}
        
        # 1. Meeting completed: If deal progressed past "appointmentscheduled"
        # Common stages: appointmentscheduled → qualifiedtobuy → presentationscheduled → etc.
        meeting_stages = [
            'appointmentscheduled', 
            'appointment scheduled',
            'meeting scheduled'
        ]
        if deal_stage and deal_stage not in meeting_stages:
            # Deal progressed past meeting stage = meeting happened
            properties_to_update['meeting_completed'] = True
        
        # 2. Fast track: Check if deal is in fast track pipeline or stage
        fast_track_keywords = ['fast', 'fast track', 'fast-track', 'fasttrack', 'express']
        if any(keyword in deal_stage for keyword in fast_track_keywords) or \
           any(keyword in pipeline.lower() for keyword in fast_track_keywords):
            properties_to_update['is_fast_track'] = True
        
        # 3. Deal won: Check if stage is "closedwon"
        won_stages = ['closedwon', 'closed won', 'won']
        if any(stage in deal_stage for stage in won_stages):
            properties_to_update['deal_won'] = True
        
        # If no properties to update, skip
        if not properties_to_update:
            return None
        
        # Get associated contacts
        try:
            # Get associations from deal to contacts using direct API
            import requests
            url = f"https://api.hubapi.com/crm/v4/objects/deals/{deal_id}/associations/contacts"
            headers = {
                "Authorization": f"Bearer {self.client.access_token}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            contact_ids = [result['toObjectId'] for result in data.get('results', [])]
            
            if not contact_ids:
                logger.warning(f"Deal {deal_id} has no associated contacts")
                return None
            
            # Update each associated contact using direct API
            for contact_id in contact_ids:
                try:
                    url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}"
                    headers = {
                        "Authorization": f"Bearer {self.client.access_token}",
                        "Content-Type": "application/json"
                    }
                    payload = {"properties": properties_to_update}
                    
                    response = requests.patch(url, headers=headers, json=payload)
                    response.raise_for_status()
                    
                    logger.info(f"✅ Updated contact {contact_id} from deal {deal_id}: {properties_to_update}")
                except Exception as e:
                    logger.error(f"Error updating contact {contact_id}: {e}")
            
            return properties_to_update
            
        except Exception as e:
            logger.error(f"Error getting associations for deal {deal_id}: {e}")
            return None
    
    def sync_contact_deals(self, contact_id: str) -> Dict[str, bool]:
        """
        Sync deals for a specific contact
        
        Args:
            contact_id: HubSpot contact ID
            
        Returns:
            Dict of properties updated
        """
        try:
            # Get deals associated with contact using direct API
            import requests
            url = f"https://api.hubapi.com/crm/v4/objects/contacts/{contact_id}/associations/deals"
            headers = {
                "Authorization": f"Bearer {self.client.access_token}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            deal_ids = [result['toObjectId'] for result in data.get('results', [])]
            
            if not deal_ids:
                return {}
            
            # Fetch deal details
            properties_to_update = {}
            
            for deal_id in deal_ids:
                deal = self.client.get_deal(deal_id)
                if deal:
                    # Process this deal's impact on contact
                    updated = self._extract_properties_from_deal(deal)
                    # Merge updates (OR logic - if any deal has it, contact gets it)
                    for key, value in updated.items():
                        if value:  # Only set true values
                            properties_to_update[key] = True
            
            # Update contact if needed using direct API
            if properties_to_update:
                url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}"
                headers = {
                    "Authorization": f"Bearer {self.client.access_token}",
                    "Content-Type": "application/json"
                }
                payload = {"properties": properties_to_update}
                
                response = requests.patch(url, headers=headers, json=payload)
                response.raise_for_status()
                
                logger.info(f"✅ Updated contact {contact_id}: {properties_to_update}")
            
            return properties_to_update
            
        except Exception as e:
            logger.error(f"Error syncing deals for contact {contact_id}: {e}")
            return {}
    
    def _extract_properties_from_deal(self, deal: Dict) -> Dict[str, bool]:
        """Extract property values from a deal"""
        props = deal.get('properties', {})
        deal_stage = props.get('dealstage', '').lower()
        pipeline = props.get('pipeline', '').lower()
        
        result = {
            'meeting_completed': False,
            'is_fast_track': False,
            'deal_won': False
        }
        
        # Meeting completed
        meeting_stages = ['appointmentscheduled', 'appointment scheduled', 'meeting scheduled']
        if deal_stage and deal_stage not in meeting_stages:
            result['meeting_completed'] = True
        
        # Fast track
        fast_track_keywords = ['fast', 'fast track', 'fast-track', 'fasttrack']
        if any(kw in deal_stage for kw in fast_track_keywords) or \
           any(kw in pipeline for kw in fast_track_keywords):
            result['is_fast_track'] = True
        
        # Deal won
        won_stages = ['closedwon', 'closed won', 'won']
        if any(stage in deal_stage for stage in won_stages):
            result['deal_won'] = True
        
        return result


def get_deal_sync(hubspot_client):
    """Get deal sync instance"""
    try:
        return DealProgressSync(hubspot_client)
    except Exception as e:
        logger.error(f"Error initializing deal sync: {e}")
        return None
