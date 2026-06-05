#!/usr/bin/env python3
"""
Migration: Add Employee.offboarded_at (DateTime, nullable) to the Employee table.

This timestamp starts the 30-day AD-deletion retention clock. It is stamped ONLY by
the offboard approval flow at the moment the AD disable succeeds — it is intentionally
NOT backfilled for existing already-disabled employees, so long-disabled legacy accounts
are never swept into the delayed-deletion queue.

Idempotent (checks information_schema before the ADD COLUMN; uses ADD COLUMN IF NOT EXISTS).
Run once: venv/bin/python migrate_add_offboarded_at.py
"""
from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        print("Adding offboarded_at to Employee table...")
        col, coltype = ("offboarded_at", "TIMESTAMP")
        exists = db.session.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name='employee' AND column_name=:col"
        ), {"col": col}).scalar()
        if exists:
            print(f"  {col}: already exists, skipped")
        else:
            db.session.execute(text(
                f'ALTER TABLE employee ADD COLUMN IF NOT EXISTS "{col}" {coltype}'))
            db.session.commit()
            print(f"  {col}: added")
        print("Migration complete.")


if __name__ == "__main__":
    migrate()
