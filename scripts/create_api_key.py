#!/usr/bin/env python3
"""Create an API key in Tracker's shared SQLite database.

Example:
  /var/www/tracker/venv/bin/python scripts/create_api_key.py \
    --user-id 1 --name "Tray Tickets" --permissions create_tickets

Notes:
- This writes into `/var/www/tracker/assets.db` (table: api_keys).
- Store the printed API key securely; it can't be recovered (only rotated).
"""

import argparse
import os
import sys


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from api_system import create_api_key


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Tracker API key")
    parser.add_argument("--user-id", type=int, required=True, help="Owner user_id (from user table)")
    parser.add_argument("--name", required=True, help="Key name (display label)")
    parser.add_argument(
        "--permissions",
        nargs="+",
        default=["create_tickets"],
        help="Space-separated permissions (default: create_tickets)",
    )
    parser.add_argument("--rate-limit", type=int, default=100, help="Requests per hour (default: 100)")
    parser.add_argument(
        "--expires-days",
        type=int,
        default=365,
        help="Days until expiry; set 0 for no expiry (default: 365)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    expires_days = None if args.expires_days == 0 else args.expires_days
    result = create_api_key(
        user_id=args.user_id,
        key_name=args.name,
        permissions=args.permissions,
        rate_limit=args.rate_limit,
        expires_days=expires_days,
    )

    print("Created API key")
    print(f"  id: {result['id']}")
    print(f"  api_key: {result['api_key']}")


if __name__ == "__main__":
    main()
