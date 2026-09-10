#!/usr/bin/env python3
"""Add `vpn_auth_event` - a record of every SSTP/RADIUS auth outcome on the CHR.

Why: between 2026-09-03 and 09-09 the VPN produced 23 auth failures across 5 users
and IT found out every single time from the user, not from a system. The CHR logs
each one within seconds and ships it here by syslog, but nothing read it. This table
plus vpn_auth_monitor.py turns that into an alert.

Additive and idempotent.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text

DDL = [
    """CREATE TABLE IF NOT EXISTS vpn_auth_event (
           id           SERIAL PRIMARY KEY,
           occurred_at  TIMESTAMP NOT NULL,
           username     VARCHAR(128),
           outcome      VARCHAR(24)  NOT NULL,
           reason       VARCHAR(64),
           diagnosis    TEXT,
           client_ip    VARCHAR(64),
           assigned_ip  VARCHAR(64),
           raw_line     TEXT,
           dedup_key    VARCHAR(200) UNIQUE,
           created_at   TIMESTAMP DEFAULT NOW()
       )""",
    "CREATE INDEX IF NOT EXISTS ix_vpn_auth_event_occurred ON vpn_auth_event (occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_vpn_auth_event_user ON vpn_auth_event (username)",
    "CREATE INDEX IF NOT EXISTS ix_vpn_auth_event_outcome ON vpn_auth_event (outcome)",
]


def main():
    with app.app_context():
        for stmt in DDL:
            print("  " + " ".join(stmt.split())[:96])
            db.session.execute(text(stmt))
        db.session.commit()
        cols = db.session.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='vpn_auth_event' ORDER BY ordinal_position")).fetchall()
        print("\nvpn_auth_event columns:")
        for n, t in cols:
            print(f"  {n:<14} {t}")


if __name__ == '__main__':
    main()
