#!/usr/bin/env python3
"""Add password-expiry columns to `employee`.

Additive and nullable, so it is safe to run before the code that populates it.
Source of truth is AD's constructed msDS-UserPasswordExpiryTimeComputed (see
ldap_service.get_all_users), which already accounts for fine-grained password
policies and for "password never expires".

Idempotent: uses IF NOT EXISTS so re-running is harmless.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text

DDL = [
    "ALTER TABLE employee ADD COLUMN IF NOT EXISTS pwd_last_set TIMESTAMP",
    "ALTER TABLE employee ADD COLUMN IF NOT EXISTS pwd_expires_at TIMESTAMP",
    "ALTER TABLE employee ADD COLUMN IF NOT EXISTS pwd_never_expires BOOLEAN DEFAULT FALSE",
    "CREATE INDEX IF NOT EXISTS ix_employee_pwd_expires_at ON employee (pwd_expires_at)",
]


def main():
    with app.app_context():
        for stmt in DDL:
            print(f"  {stmt}")
            db.session.execute(text(stmt))
        db.session.commit()
        cols = db.session.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='employee' AND column_name LIKE 'pwd%' ORDER BY column_name"
        )).fetchall()
        print("\nemployee password columns now:")
        for name, dtype in cols:
            print(f"  {name:<20} {dtype}")


if __name__ == '__main__':
    main()
