#!/usr/bin/env python3
"""Enroll an RMM agent by creating a row in rmm_agent.

This generates a one-time token. Store it securely on the endpoint.

Example:
  /var/www/tracker/venv/bin/python scripts/enroll_rmm_agent.py \
    --agent-id PC-01 --asset-id 123

Then the agent connects:
  ws(s)://<gateway-host>/ws/agent/PC-01?token=<printed_token>
"""

import argparse
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime

DB_PATH = "/var/www/tracker/assets.db"
if not os.path.exists(DB_PATH):
    raise SystemExit(
        "DEPRECATED: this helper targets the pre-migration SQLite DB (assets.db), "
        "which has been retired. RMM agent enrollment now goes through Postgres via "
        "the app (POST /api/rmm/enroll)."
    )


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enroll a Tracker RMM agent")
    p.add_argument("--agent-id", required=True, help="Unique agent id (e.g. hostname or GUID)")
    p.add_argument("--asset-id", type=int, default=None, help="Optional asset.id to link")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    token = "agent_" + secrets.token_urlsafe(32)
    token_hash = sha256_hex(token)

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO rmm_agent (agent_id, asset_id, agent_token_sha256, enabled, created_at, last_seen_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
              asset_id=excluded.asset_id,
              agent_token_sha256=excluded.agent_token_sha256,
              enabled=1,
              last_seen_at=excluded.last_seen_at
            """,
            (args.agent_id, args.asset_id, token_hash, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    print("Enrolled agent")
    print(f"  agent_id: {args.agent_id}")
    print(f"  token: {token}")


if __name__ == "__main__":
    # Ensure executable bit is optional; keep script simple.
    main()
