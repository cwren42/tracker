#!/usr/bin/env python3
"""Ledger of password-expiry notices sent directly to employees.

Why a ledger: the warning is only useful if it arrives once per threshold.
Without this table a daily evaluator would re-mail every affected person every
day, which trains people to filter it -- the exact failure mode we are trying
to fix (users already ignore the Windows expiry toast).

The UNIQUE key deliberately includes `pwd_expires_at`. That value is AD's
constructed msDS-UserPasswordExpiryTimeComputed, so it MOVES the moment the
user changes their password. Tying the dedup to it means:
  * a person is mailed at most once per band per password cycle, and
  * the bands re-arm automatically on the next cycle, with no reset job.

Additive and idempotent.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text

DDL = [
    """CREATE TABLE IF NOT EXISTS pwd_expiry_notice (
           id                SERIAL PRIMARY KEY,
           employee_id       INTEGER NOT NULL,
           sam_account_name  TEXT,
           recipient         TEXT,
           band_days         INTEGER NOT NULL,
           pwd_expires_at    TIMESTAMP NOT NULL,
           sent_at           TIMESTAMP NOT NULL DEFAULT NOW()
       )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_pwd_expiry_notice_cycle
           ON pwd_expiry_notice (employee_id, band_days, pwd_expires_at)""",
    "CREATE INDEX IF NOT EXISTS ix_pwd_expiry_notice_sent_at ON pwd_expiry_notice (sent_at)",
]


def main():
    with app.app_context():
        for stmt in DDL:
            print("  " + " ".join(stmt.split())[:110])
            db.session.execute(text(stmt))
        db.session.commit()
        cols = db.session.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='pwd_expiry_notice' ORDER BY ordinal_position"
        )).fetchall()
        print("\npwd_expiry_notice:")
        for name, dtype in cols:
            print(f"  {name:<18} {dtype}")
        idx = db.session.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='pwd_expiry_notice' ORDER BY indexname"
        )).fetchall()
        print("indexes: " + ", ".join(i[0] for i in idx))


if __name__ == '__main__':
    main()
