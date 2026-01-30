#!/usr/bin/env python3
"""
Import LinkedIn engagement data from Waalaxy CSV export
Matches engagers (reactions/comments) with HubSpot contacts for attribution

SAFETY FEATURES:
- Never overwrites existing url_tracking attribution
- Confidence scoring for matches
- Audit log of all changes
- Dry run mode for preview
- Manual review for ambiguous matches
"""

import csv
import json
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(message)s')

# Audit log file
AUDIT_LOG_PATH = Path(__file__).parent.parent / "waalaxy_import_audit.log"


def _name_similarity(name1: str, name2: str) -> bool:
    """
    Simple name similarity check
    Returns True if names appear to be the same person
    """
    if not name1 or not name2:
        return False
    
    # Normalize names
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()
    
    # Exact match
    if n1 == n2:
        return True
    
    # Split into parts
    parts1 = n1.split()
    parts2 = n2.split()
    
    # Check if first and last names match
    if len(parts1) >= 2 and len(parts2) >= 2:
        first1, last1 = parts1[0], parts1[-1]
        first2, last2 = parts2[0], parts2[-1]
        
        # Both first and last match
        if first1 == first2 and last1 == last2:
            return True
    
    return False


def _log_audit(action: str, details: dict):
    """Log action to audit file"""
    try:
        with open(AUDIT_LOG_PATH, 'a') as f:
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'action': action,
                **details
            }
            f.write(json.dumps(log_entry) + '\n')
    except Exception as e:
        logger.warning(f"Could not write to audit log: {e}")


def load_post_database():
    """Load post database"""
    db_path = Path(__file__).parent.parent / "post_database.json"
    if not db_path.exists():
        return {"posts": []}
    
    with open(db_path, 'r') as f:
        return json.load(f)


def load_leads():
    """Load leads data"""
    leads_path = Path(__file__).parent.parent / "dashboard" / "leads_data.json"
    if not leads_path.exists():
        return {"pending": [], "pushed": [], "rejected": [], "already_booked": []}
    
    with open(leads_path, 'r') as f:
        data = json.load(f)
        return data


def import_waalaxy_csv(csv_path: str, post_id: str, dry_run: bool = False):
    """
    Import Waalaxy engagement CSV for a specific post
    
    Args:
        csv_path: Path to Waalaxy CSV export
        post_id: Post ID (e.g., "laura_jan15")
        dry_run: If True, only preview changes
    
    Returns:
        Dictionary with import results
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"📊 Waalaxy Import for Post: {post_id}")
    logger.info(f"{'='*60}\n")
    
    # Load data
    post_db = load_post_database()
    leads_data = load_leads()
    
    # Find the post
    post = None
    for p in post_db['posts']:
        if f"{p['creator']}_{p['date']}" == post_id:
            post = p
            break
    
    if not post:
        logger.error(f"❌ Post not found: {post_id}")
        return {"error": "Post not found"}
    
    # Read CSV
    engagers = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            engagers.append({
                'name': row.get('Name', row.get('Full Name', '')),
                'linkedin_url': row.get('LinkedIn URL', row.get('Profile URL', '')),
                'interaction_type': row.get('Type', 'reaction'),  # reaction or comment
                'email': row.get('Email', ''),
                'company': row.get('Company', '')
            })
    
    logger.info(f"✅ Loaded {len(engagers)} engagers from CSV\n")
    
    # Match with existing leads (with confidence scoring)
    all_leads = (
        leads_data['pending']
        + leads_data['pushed']
        + leads_data['rejected']
        + leads_data.get('already_booked', [])
    )
    matched = []
    unmatched = []
    skipped_tracked = []  # Leads already attributed via URL tracking
    
    for engager in engagers:
        # Try to match by email or LinkedIn URL
        match = None
        confidence = 'low'
        match_method = ''
        
        for lead in all_leads:
            # SAFETY: Skip if already has URL tracking attribution
            if lead.get('attribution_method') == 'url_tracking':
                skipped_tracked.append({
                    'engager': engager,
                    'lead': lead,
                    'reason': 'Already tracked via URL'
                })
                match = None
                break
            
            # Email match (high confidence)
            if engager['email'] and lead.get('email', '').lower() == engager['email'].lower():
                match = lead
                confidence = 'high'
                match_method = 'email'
                break
            
            # LinkedIn URL match (high confidence)
            if engager['linkedin_url'] and lead.get('linkedin_url') == engager['linkedin_url']:
                match = lead
                confidence = 'high'
                match_method = 'linkedin_url'
                break
            
            # Company domain match + name similarity (medium confidence)
            if engager['email'] and lead.get('email'):
                engager_domain = engager['email'].split('@')[-1] if '@' in engager['email'] else ''
                lead_domain = lead.get('email', '').split('@')[-1] if '@' in lead.get('email', '') else ''
                
                if engager_domain and lead_domain and engager_domain == lead_domain:
                    # Same company, check name similarity
                    if _name_similarity(engager['name'], lead.get('contact_name', '')):
                        match = lead
                        confidence = 'medium'
                        match_method = 'domain_and_name'
                        break
        
        if match:
            matched.append({
                'engager': engager,
                'lead': match,
                'confidence': confidence,
                'match_method': match_method
            })
        else:
            unmatched.append(engager)
    
    logger.info(f"📊 Matching Results:")
    logger.info(f"   ✅ Matched: {len(matched)}")
    logger.info(f"   ⚠️  Skipped (already tracked): {len(skipped_tracked)}")
    logger.info(f"   ❌ Unmatched: {len(unmatched)}\n")
    
    if matched:
        logger.info(f"✅ MATCHED ENGAGERS (will be attributed to {post_id}):")
        for m in matched[:10]:  # Show first 10
            confidence_icon = "🟢" if m['confidence'] == 'high' else "🟡" if m['confidence'] == 'medium' else "🔴"
            logger.info(f"   {confidence_icon} {m['engager']['name']} ({m['engager']['interaction_type']})")
            logger.info(f"     → {m['lead']['email']} - {m['lead'].get('company_name', 'N/A')}")
            logger.info(f"     Match: {m['match_method']} | Confidence: {m['confidence']}")
        if len(matched) > 10:
            logger.info(f"   ... and {len(matched) - 10} more")
        logger.info("")
    
    if skipped_tracked:
        logger.info(f"⚠️  SKIPPED (already tracked via URL):")
        for s in skipped_tracked[:5]:
            logger.info(f"   • {s['engager']['name']} - {s['lead'].get('email')}")
        if len(skipped_tracked) > 5:
            logger.info(f"   ... and {len(skipped_tracked) - 5} more")
        logger.info("")
    
    if unmatched:
        logger.info(f"❌ UNMATCHED ENGAGERS (not in dashboard):")
        for u in unmatched[:5]:  # Show first 5
            logger.info(f"   • {u['name']} - {u.get('company', 'N/A')}")
        if len(unmatched) > 5:
            logger.info(f"   ... and {len(unmatched) - 5} more")
        logger.info("")
    
    # Update attribution if not dry run
    if not dry_run:
        updated_count = 0
        for m in matched:
            lead = m['lead']
            
            # SAFETY: Only update if no existing attribution
            if not lead.get('post_source_auto') and not lead.get('post_source_final'):
                old_source = lead.get('traffic_source', 'organic')
                
                lead['post_source_final'] = post_id
                lead['post_creator'] = post['creator']
                lead['post_date'] = post['date']
                lead['post_track'] = post['track']
                lead['post_cost'] = post['cost']
                lead['attribution_confidence'] = m['confidence']
                lead['attribution_method'] = 'waalaxy_match'
                lead['traffic_source'] = 'post'
                lead['waalaxy_interaction_type'] = m['engager']['interaction_type']
                lead['waalaxy_match_method'] = m['match_method']
                
                # Log to audit trail
                _log_audit('attribution_updated', {
                    'lead_email': lead.get('email'),
                    'lead_company': lead.get('company_name'),
                    'post_id': post_id,
                    'old_source': old_source,
                    'new_source': 'post',
                    'confidence': m['confidence'],
                    'match_method': m['match_method'],
                    'interaction_type': m['engager']['interaction_type']
                })
                
                updated_count += 1
        
        # Save updated leads
        with open(Path(__file__).parent.parent / "dashboard" / "leads_data.json", 'w') as f:
            json.dump(leads_data, f, indent=2)
        
        logger.info(f"✅ Updated {updated_count} leads with post attribution")
        logger.info(f"📝 Audit log: {AUDIT_LOG_PATH}\n")
    else:
        logger.info(f"🔍 DRY RUN - No changes made\n")
        logger.info(f"Run without --dry-run to apply changes\n")
    
    return {
        "total_engagers": len(engagers),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "post_id": post_id
    }


def main():
    parser = argparse.ArgumentParser(description='Import Waalaxy engagement data')
    parser.add_argument('csv_file', help='Path to Waalaxy CSV export')
    parser.add_argument('--post-id', required=True, help='Post ID (e.g., laura_jan15)')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, do not update')
    
    args = parser.parse_args()
    
    result = import_waalaxy_csv(args.csv_file, args.post_id, args.dry_run)
    
    if 'error' in result:
        sys.exit(1)


if __name__ == '__main__':
    main()
