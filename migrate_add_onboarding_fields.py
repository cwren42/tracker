#!/usr/bin/env python3
"""
Migration: Add new-hire onboarding / access-request fields to the Employee table.
Idempotent (checks information_schema before each ADD COLUMN).
Run once: venv/bin/python migrate_add_onboarding_fields.py
"""
from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        print("Adding onboarding fields to Employee table...")
        columns = [
            ("job_title",      "VARCHAR(150)"),
            ("manager",        "VARCHAR(150)"),
            ("start_date",     "DATE"),
            ("work_type",      "VARCHAR(20)"),
            ("onboard_status", "VARCHAR(20)"),
        ]
        for col, coltype in columns:
            exists = db.session.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name='employee' AND column_name=:col"
            ), {"col": col}).scalar()
            if exists:
                print(f"  {col}: already exists, skipped")
            else:
                db.session.execute(text(f'ALTER TABLE employee ADD COLUMN "{col}" {coltype}'))
                db.session.commit()
                print(f"  {col}: added")
        print("Migration complete.")


if __name__ == "__main__":
    migrate()
