#!/usr/bin/env python3
"""
Generate clean tracking links for LinkedIn posts.

Each link has two identifiers:
  slug  = clean URL path shown to the user (e.g. "catch")
  code  = value used in the Webflow ?s= parameter for tracking

By default code equals slug. Use --code to override when the Webflow
redirect uses a different value (e.g. /start -> /?s=l04).

The Webflow JavaScript captures the ?s= value into a hidden form field
(post_source_auto). The dashboard matches that value against both the
'code' and 'slug' fields in post_database.json.

Usage:
  python3 generate_link.py --creator barney --slug catch --track B --cost 3000
  python3 generate_link.py --creator eli --slug try --code e09 --track A --cost 2400
  python3 generate_link.py --creator laura --slug start --code l04 --track A2 --cost 0
"""
import json
import sys
from datetime import datetime
from pathlib import Path


def generate_link(creator, slug, track, cost, code=None):
    """Generate a clean tracking link and save to post database."""

    slug = slug.lower().strip("/")
    code = (code or slug).lower().strip("/")

    link = f"https://zenyt.ai/{slug}"

    post_data = {
        "slug": slug,
        "code": code,
        "creator": creator.lower(),
        "track": track.upper(),
        "cost": int(cost),
        "link": link,
        "post_url": "",
        "created_at": datetime.now().isoformat()
    }

    db_path = Path(__file__).parent.parent / "post_database.json"
    if db_path.exists():
        with open(db_path, "r") as f:
            db = json.load(f)
    else:
        db = {"posts": []}

    existing_slugs = [p.get("slug", p.get("code", "")) for p in db.get("posts", [])]
    existing_codes = [p.get("code", p.get("slug", "")) for p in db.get("posts", [])]
    if slug in existing_slugs:
        print(f"Warning: Slug '{slug}' already exists. Pick a different one.")
        return None
    if code in existing_codes:
        print(f"Warning: Code '{code}' already exists. Pick a different one.")
        return None

    db["posts"].append(post_data)

    with open(db_path, "w") as f:
        json.dump(db, f, indent=2)

    print(f"""
Link created.

  Link:    {link}
  Slug:    {slug}
  Code:    {code}  (Webflow redirect: /{slug} -> /?s={code})
  Creator: {creator}
  Track:   {track}
  Cost:    ${cost}

Send this to {creator}:
  {link}

Webflow setup needed:
  Add 301 redirect: /{slug} -> /?s={code}

Saved to post_database.json
    """)

    return post_data


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate clean LinkedIn post tracking links")
    parser.add_argument("--creator", required=True, help="Creator name (e.g., eli, barney, laura)")
    parser.add_argument("--slug", required=True, help="Clean URL path (e.g., catch, try, start)")
    parser.add_argument("--code", required=False, default=None, help="Webflow ?s= tracking code (defaults to slug)")
    parser.add_argument("--track", required=True, help="Track name (e.g., A, B)")
    parser.add_argument("--cost", type=int, required=True, help="Promotion cost in $ (e.g., 2400)")

    args = parser.parse_args()

    generate_link(args.creator, args.slug, args.track, args.cost, args.code)
