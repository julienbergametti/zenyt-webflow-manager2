#!/usr/bin/env python3
"""
Generate stealth tracking links for LinkedIn posts
Usage: python3 generate_link.py --creator laura --date jan12 --track A3 --cost 400
"""
import json
import sys
from datetime import datetime
from pathlib import Path

def generate_link(creator, date, track, cost):
    """Generate tracking link and save to post database"""
    
    # Generate short code: first letter + day number (e.g., "l12")
    code = f"{creator[0].lower()}{date.replace('jan', '').replace('feb', '').replace('mar', '').replace('apr', '').replace('may', '').replace('jun', '').replace('jul', '').replace('aug', '').replace('sep', '').replace('oct', '').replace('nov', '').replace('dec', '')}"
    
    link = f"https://zenyt.ai/?s={code}"
    
    post_data = {
        "code": code,
        "creator": creator.lower(),
        "date": date.lower(),
        "track": track.upper(),
        "cost": int(cost),
        "link": link,
        "post_url": "",  # To be filled manually
        "created_at": datetime.now().isoformat()
    }
    
    # Load existing database
    db_path = Path(__file__).parent.parent / "post_database.json"
    if db_path.exists():
        with open(db_path, 'r') as f:
            db = json.load(f)
    else:
        db = {"posts": []}
    
    # Check for duplicate codes
    existing_codes = [p['code'] for p in db.get('posts', [])]
    if code in existing_codes:
        print(f"⚠️  Warning: Code '{code}' already exists!")
        print(f"   Use a different creator name or date.")
        return None
    
    # Add post to database
    db['posts'].append(post_data)
    
    # Save database
    with open(db_path, 'w') as f:
        json.dump(db, f, indent=2)
    
    # Print output
    print(f"""
✅ Tracking Link Created!
━━━━━━━━━━━━━━━━━━━━━━━━
🔗 Link: {link}
📋 Code: {code}
👤 Creator: {creator}
📅 Date: {date}
🎯 Track: {track}
💰 Cost: ${cost}

📎 Send this to {creator}:
━━━━━━━━━━━━━━━━━━━━━━━━
{link}

💾 Saved to post_database.json
    """)
    
    return post_data

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate LinkedIn post tracking links')
    parser.add_argument("--creator", required=True, help="Creator name (e.g., laura, nathan)")
    parser.add_argument("--date", required=True, help="Date (e.g., jan12, feb05)")
    parser.add_argument("--track", required=True, help="Track name (e.g., A3, B1)")
    parser.add_argument("--cost", type=int, required=True, help="Promotion cost in $ (e.g., 400)")
    
    args = parser.parse_args()
    
    generate_link(args.creator, args.date, args.track, args.cost)
