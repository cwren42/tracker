#!/usr/bin/env python3
"""
Migration: Add Active Directory and M365 validation fields to Employee table.
Run once: python migrate_add_ad_employee_fields.py
"""
from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        print("Adding AD / M365 fields to Employee table...")
        columns = [
            ("sam_account_name",     "VARCHAR(100)"),
            ("ad_guid",              "VARCHAR(36)"),
            ("ad_dn",                "VARCHAR(500)"),
            ("ad_enabled",           "BOOLEAN"),
            ("ad_last_sync",         "TIMESTAMP"),
            ("m365_id",              "VARCHAR(100)"),
            ("m365_account_enabled", "BOOLEAN"),
            ("m365_validated_at",    "TIMESTAMP"),
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

        # Unique partial index — only index non-NULL ad_guid values
        try:
            db.session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_employee_ad_guid "
                "ON employee (ad_guid) WHERE ad_guid IS NOT NULL"
            ))
            db.session.commit()
            print("  ix_employee_ad_guid: unique index ensured")
        except Exception as e:
            db.session.rollback()
            print(f"  ix_employee_ad_guid: {e}")

        print("Migration complete.")


if __name__ == "__main__":
    migrate()
