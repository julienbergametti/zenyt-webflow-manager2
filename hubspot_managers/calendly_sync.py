"""
Calendly API Integration for Meeting Tracking
Syncs meeting data from Calendly and matches with leads in the dashboard
"""

import os
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class CalendlySync:
    """Handles Calendly API integration for meeting tracking"""
    
    def __init__(self, api_token: Optional[str] = None):
        """
        Initialize Calendly sync
        
        Args:
            api_token: Calendly API token (if not provided, loads from env)
        """
        self.api_token = api_token or os.getenv('CALENDLY_API_TOKEN')
        self.base_url = "https://api.calendly.com"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        self.user_uri = None
        
        if not self.api_token:
            logger.warning("Calendly API token not found - meeting sync disabled")
        else:
            logger.info("✅ Calendly sync initialized")
            self._fetch_user_info()
    
    def _fetch_user_info(self):
        """Fetch current user info to get user URI"""
        try:
            response = requests.get(
                f"{self.base_url}/users/me",
                headers=self.headers
            )
            response.raise_for_status()
            data = response.json()
            self.user_uri = data['resource']['uri']
            logger.info(f"Calendly user URI: {self.user_uri}")
        except Exception as e:
            logger.error(f"Error fetching Calendly user info: {e}")
    
    def get_scheduled_events(
        self, 
        days_back: int = 90,
        event_status: str = "active"
    ) -> List[Dict]:
        """
        Get scheduled events from Calendly
        
        Args:
            days_back: How many days back to fetch events
            event_status: Status filter (active, canceled)
            
        Returns:
            List of scheduled events with invitee information
        """
        if not self.api_token or not self.user_uri:
            return []
        
        try:
            min_start_time = (datetime.now() - timedelta(days=days_back)).isoformat()
            
            response = requests.get(
                f"{self.base_url}/scheduled_events",
                headers=self.headers,
                params={
                    "user": self.user_uri,
                    "min_start_time": min_start_time,
                    "status": event_status,
                    "count": 100  # Max per page
                }
            )
            response.raise_for_status()
            
            events = response.json().get('collection', [])
            logger.info(f"Fetched {len(events)} Calendly events")
            
            # Enrich events with invitee data
            enriched_events = []
            for event in events:
                event_uri = event['uri']
                invitees = self._get_event_invitees(event_uri)
                
                if invitees:
                    event['invitees'] = invitees
                    enriched_events.append(event)
            
            return enriched_events
            
        except Exception as e:
            logger.error(f"Error fetching Calendly events: {e}")
            return []
    
    def _get_event_invitees(self, event_uri: str) -> List[Dict]:
        """Get invitees for a specific event"""
        try:
            response = requests.get(
                f"{self.base_url}/scheduled_events/{event_uri.split('/')[-1]}/invitees",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json().get('collection', [])
        except Exception as e:
            logger.error(f"Error fetching invitees for event: {e}")
            return []
    
    def match_events_to_leads(self, events: List[Dict], leads: List[Dict]) -> Dict:
        """
        Match Calendly events to leads by email
        
        IMPORTANT: Only keeps ONE meeting per email (most recent one)
        If a prospect had 3 meetings, only the most recent is tracked
        
        Args:
            events: List of Calendly events with invitees
            leads: List of leads from dashboard
            
        Returns:
            Dict mapping lead emails to meeting data (ONE per email)
        """
        meeting_data = {}
        
        for event in events:
            event_start = event.get('start_time', '')
            event_status = event.get('status', 'active')
            
            # Parse event status
            try:
                event_datetime = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                is_completed = event_datetime < datetime.now()
            except:
                is_completed = False
            
            for invitee in event.get('invitees', []):
                email = invitee.get('email', '').lower()
                
                if email:
                    # Check if we already have a meeting for this email
                    if email in meeting_data:
                        # Compare dates - keep only the most recent meeting
                        existing_date = meeting_data[email]['meeting_booked_date']
                        try:
                            existing_datetime = datetime.fromisoformat(existing_date.replace('Z', '+00:00'))
                            if event_datetime > existing_datetime:
                                # This event is more recent, replace it
                                pass
                            else:
                                # Existing event is more recent, skip this one
                                continue
                        except:
                            pass  # If date parsing fails, keep the new one
                    
                    # Store meeting data (UNIQUE per email)
                    meeting_data[email] = {
                        'calendly_event_uri': event['uri'],
                        'event_name': event.get('name', ''),
                        'meeting_booked_date': event_start,
                        'meeting_status': 'completed' if is_completed else 'scheduled',
                        'invitee_status': invitee.get('status', ''),
                        'canceled': invitee.get('canceled', False),
                        'rescheduled': invitee.get('rescheduled', False)
                    }
        
        logger.info(f"Matched {len(meeting_data)} UNIQUE prospects to Calendly events")
        return meeting_data
    
    def sync_meetings_to_leads(self, leads: List[Dict]) -> int:
        """
        Sync Calendly meetings to leads (updates leads in place)
        
        IMPORTANT RULES:
        - Only matches meetings to leads that exist in the dashboard (Webflow leads)
        - Counts UNIQUE prospects only (one meeting per lead, even if they had 3+ calls)
        - Does NOT count meetings with customers or contacts not in the dashboard
        
        Args:
            leads: List of leads from dashboard to update
            
        Returns:
            Number of leads updated with meeting data
        """
        if not self.api_token:
            return 0
        
        # Build a set of lead emails to ONLY match against dashboard leads
        lead_emails = {lead.get('email', '').lower() for lead in leads if lead.get('email')}
        logger.info(f"📋 Matching Calendly meetings against {len(lead_emails)} dashboard leads")
        
        # Fetch recent events
        events = self.get_scheduled_events(days_back=90)
        logger.info(f"📅 Found {len(events)} total Calendly events")
        
        # Filter events to ONLY include leads from dashboard
        filtered_events = []
        for event in events:
            for invitee in event.get('invitees', []):
                invitee_email = invitee.get('email', '').lower()
                if invitee_email in lead_emails:
                    filtered_events.append(event)
                    break  # Only add event once even if multiple invitees match
        
        logger.info(f"✅ Filtered to {len(filtered_events)} events matching dashboard leads")
        
        # Match filtered events to leads
        meeting_data = self.match_events_to_leads(filtered_events, leads)
        
        updated_count = 0
        skipped_count = 0
        
        for lead in leads:
            email = lead.get('email', '').lower()
            
            if email in meeting_data:
                meeting_info = meeting_data[email]
                
                # Update lead with meeting information (UNIQUE per lead)
                lead['meeting_booked'] = 'calendly'
                lead['meeting_booked_date'] = meeting_info['meeting_booked_date']
                lead['meeting_status'] = meeting_info['meeting_status']
                lead['calendly_event_uri'] = meeting_info['calendly_event_uri']
                lead['meeting_completed'] = meeting_info['meeting_status'] == 'completed'
                lead['has_meeting'] = True  # Mark as having a meeting
                
                if meeting_info['canceled']:
                    lead['meeting_status'] = 'canceled'
                elif meeting_info['rescheduled']:
                    lead['meeting_status'] = 'rescheduled'
                
                updated_count += 1
                logger.info(f"✅ Matched meeting for dashboard lead: {email}")
            else:
                skipped_count += 1
        
        logger.info(f"📊 Meeting sync complete: {updated_count} leads matched, {skipped_count} leads without meetings")
        
        return updated_count


def get_calendly_sync() -> Optional[CalendlySync]:
    """
    Get Calendly sync instance (creates if needed)
    
    Returns:
        CalendlySync instance or None if token not available
    """
    try:
        sync = CalendlySync()
        return sync if sync.api_token else None
    except Exception as e:
        logger.error(f"Error initializing Calendly sync: {e}")
        return None
