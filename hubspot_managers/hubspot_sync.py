#!/usr/bin/env python3
"""
HubSpot Real-time Sync for Post Performance
Fetches deal data, meeting stats, and win rates from HubSpot
"""

import os
import logging
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path

try:
    from .hubspot_client import HubSpotClient
except ImportError:
    from hubspot_client import HubSpotClient

logger = logging.getLogger(__name__)


class HubSpotPostSync:
    """Sync post performance metrics with HubSpot in real-time"""
    
    def __init__(self, access_token: str):
        """
        Initialize HubSpot sync client
        
        Args:
            access_token: HubSpot API token
        """
        self.client = HubSpotClient(access_token)
        logger.info("HubSpot Post Sync initialized")
    
    def get_post_metrics_from_hubspot(self, post_id: str, post_creator: str, post_date: str) -> Dict:
        """
        Fetch real-time metrics for a post from HubSpot
        
        Args:
            post_id: Post identifier (e.g., "laura_jan15")
            post_creator: Creator name
            post_date: Post date
        
        Returns:
            Dictionary with HubSpot metrics:
            - meeting_requests: Count of deals in "Meeting Scheduled" stage
            - meetings_done: Count of deals that completed meetings
            - fast_tracks: Count of fast-tracked deals
            - wins: Count of closed won deals
            - total_deals: Total deal count from this post
        """
        try:
            # Search for contacts/companies with this post_source
            companies = self.client.search_companies({
                'filterGroups': [{
                    'filters': [{
                        'propertyName': 'post_source',
                        'operator': 'EQ',
                        'value': post_id
                    }]
                }]
            })
            
            metrics = {
                'meeting_requests': 0,
                'meetings_done': 0,
                'fast_tracks': 0,
                'wins': 0,
                'total_deals': 0,
                'hubspot_company_ids': []
            }
            
            # Get all deals associated with these companies
            for company in companies:
                company_id = company['id']
                metrics['hubspot_company_ids'].append(company_id)
                
                # Get deals for this company
                deals = self.client.get_company_deals(company_id)
                
                for deal in deals:
                    metrics['total_deals'] += 1
                    deal_stage = deal['properties'].get('dealstage', '')
                    is_fast_track = deal['properties'].get('is_fast_track', False)
                    
                    # Count meeting requests (deals in meeting scheduled stage)
                    if 'meeting' in deal_stage.lower() or deal_stage == 'appointmentscheduled':
                        metrics['meeting_requests'] += 1
                    
                    # Count meetings done (deals past meeting stage)
                    if deal_stage in ['qualifiedtobuy', 'presentationscheduled', 'decisionmakerboughtin', 'contractsent', 'closedwon']:
                        metrics['meetings_done'] += 1
                    
                    # Count fast tracks
                    if is_fast_track or deal['properties'].get('fast_track', False):
                        metrics['fast_tracks'] += 1
                    
                    # Count wins
                    if deal_stage == 'closedwon':
                        metrics['wins'] += 1
            
            logger.info(f"✅ Fetched HubSpot metrics for {post_id}: {metrics['total_deals']} deals, {metrics['wins']} wins")
            return metrics
            
        except Exception as e:
            logger.error(f"Error fetching HubSpot metrics for {post_id}: {e}")
            return {
                'meeting_requests': 0,
                'meetings_done': 0,
                'fast_tracks': 0,
                'wins': 0,
                'total_deals': 0,
                'hubspot_company_ids': [],
                'error': str(e)
            }
    
    def sync_all_posts(self, posts: List[Dict]) -> List[Dict]:
        """
        Sync all posts with HubSpot metrics
        
        Args:
            posts: List of post dictionaries
        
        Returns:
            List of posts enriched with HubSpot metrics
        """
        enriched_posts = []
        
        for post in posts:
            post_id = f"{post['creator']}_{post['date']}"
            
            # Fetch real-time metrics from HubSpot
            hubspot_metrics = self.get_post_metrics_from_hubspot(
                post_id,
                post['creator'],
                post['date']
            )
            
            # Merge HubSpot metrics into post data
            enriched_post = {**post, **hubspot_metrics}
            enriched_posts.append(enriched_post)
        
        return enriched_posts
    
    def sync_creator_posts_object(self, post_data: Dict) -> Optional[str]:
        """
        Sync post to HubSpot Creator Posts custom object
        
        Args:
            post_data: Post data to sync
        
        Returns:
            HubSpot Creator Post ID or None
        """
        try:
            # Check if post already exists
            existing = self.client.search_custom_objects(
                'p243279017_creator_posts',
                {
                    'filterGroups': [{
                        'filters': [{
                            'propertyName': 'post_url',
                            'operator': 'EQ',
                            'value': post_data.get('link', '')
                        }]
                    }]
                }
            )
            
            if existing:
                # Update existing post
                post_id = existing[0]['id']
                self.client.update_custom_object_record(
                    'p243279017_creator_posts',
                    post_id,
                    {
                        'meetings_generated': post_data.get('total_demos', 0),
                        'impressions': post_data.get('impressions', 0),
                        'cost': post_data.get('cost', 0)
                    }
                )
                logger.info(f"✅ Updated Creator Post {post_id} in HubSpot")
                return post_id
            else:
                # Create new post
                result = self.client.create_custom_object_record(
                    'p243279017_creator_posts',
                    {
                        'creator_name': post_data.get('creator', ''),
                        'creator_track': post_data.get('track', ''),
                        'post_date': post_data.get('created_at', ''),
                        'post_url': post_data.get('link', ''),
                        'cost': post_data.get('cost', 0),
                        'meetings_generated': post_data.get('total_demos', 0),
                        'impressions': post_data.get('impressions', 0)
                    }
                )
                post_id = result['id']
                logger.info(f"✅ Created Creator Post {post_id} in HubSpot")
                return post_id
                
        except Exception as e:
            logger.error(f"Error syncing to Creator Posts object: {e}")
            return None


def get_hubspot_sync() -> Optional[HubSpotPostSync]:
    """Get HubSpot sync client if token is available"""
    try:
        from dotenv import load_dotenv
        
        # Try loading from multiple possible locations
        env_paths = [
            Path(__file__).parent.parent / '.env',
            Path(__file__).parent.parent / 'config' / '.env',
            Path.home() / 'Documents' / 'Zenyt' / 'zenyt-docs' / 'hubspot-crm' / 'config' / '.env'
        ]
        
        for env_path in env_paths:
            if env_path.exists():
                load_dotenv(env_path)
                break
        
        token = os.getenv('HUBSPOT_ACCESS_TOKEN')
        if not token:
            logger.warning("HubSpot token not found - real-time sync disabled")
            return None
        
        return HubSpotPostSync(token)
        
    except Exception as e:
        logger.error(f"Failed to initialize HubSpot sync: {e}")
        return None
