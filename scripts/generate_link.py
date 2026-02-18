#!/usr/bin/env python3
"""
Generate clean tracking links for LinkedIn posts.

Links look like natural landing pages (no query params, no codes, no numbers).
The slug is a real-looking page path. Attribution is stored only in post_database.json.

Usage:
  python3 generate_link.py --creator eli --slug ecommerce-qa --track A --cost 2400
  python3 generate_link.py --creator barney --slug retail-quality --track B --cost 3000
  python3 generate_link.py --creator laura --slug product-accuracy --track A --cost 0
"""
import json
import sys
from datetime import datetime
from pathlib import Path


def generate_link(creator, slug, track, cost):
    """Generate a clean tracking link and save to post database."""

    # Clean the slug (lowercase, strip leading/trailing slashes)
    slug = slug.lower().strip("/")

    link = f"https://zenyt.ai/{slug}"

    post_data = {
        "slug": slug,
        "creator": creator.lower(),
        "track": track.upper(),
        "cost": int(cost),
        "link": link,
        "post_url": "",          # Fill once the LinkedIn post is live
        "created_at": datetime.now().isoformat()
    }

    # Load existing database
    db_path = Path(__file__).parent.parent / "post_database.json"
    if db_path.exists():
        with open(db_path, "r") as f:
            db = json.load(f)
    else:
        db = {"posts": []}

    # Check for duplicate slugs
    existing_slugs = [p.get("slug", p.get("code", "")) for p in db.get("posts", [])]
    if slug in existing_slugs:
        print(f"Warning: Slug '{slug}' already exists. Pick a different one.")
        return None

    # Add post to database
    db["posts"].append(post_data)

    # Save database
    with open(db_path, "w") as f:
        json.dump(db, f, indent=2)

    # Print output
    print(f"""
Link created.

  Link:    {link}
  Slug:    {slug}
  Creator: {creator}
  Track:   {track}
  Cost:    ${cost}

Send this to {creator}:
  {link}

Saved to post_database.json
    """)

    return post_data


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate clean LinkedIn post tracking links")
    parser.add_argument("--creator", required=True, help="Creator name (e.g., eli, barney, laura)")
    parser.add_argument("--slug", required=True, help="Clean URL path (e.g., ecommerce-qa, retail-quality)")
    parser.add_argument("--track", required=True, help="Track name (e.g., A, B)")
    parser.add_argument("--cost", type=int, required=True, help="Promotion cost in $ (e.g., 2400)")

    args = parser.parse_args()

    generate_link(args.creator, args.slug, args.track, args.cost)
