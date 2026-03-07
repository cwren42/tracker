#!/usr/bin/env python3
"""
Database schema validation and migration script.
Ensures all required columns exist before the application starts.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from sqlalchemy import text

def ensure_schema():
    """Ensure database schema is up to date"""
    with app.app_context():
        try:
            # Ensure all tables exist
            db.create_all()
            print("✓ All database tables verified")

            # Check and add theme column if missing
            result = db.session.execute(text("PRAGMA table_info(user)"))
            columns = [row[1] for row in result]
            if 'theme' not in columns:
                print("Adding 'theme' column to user table...")
                db.session.execute(text("ALTER TABLE user ADD COLUMN theme VARCHAR(30) DEFAULT 'default'"))
                db.session.commit()
                print("✓ Successfully added 'theme' column")
            else:
                print("✓ 'theme' column exists")

            # Check and add photo column if missing
            result = db.session.execute(text("PRAGMA table_info(employee)"))
            columns = [row[1] for row in result]
            if 'photo' not in columns:
                print("Adding 'photo' column to employee table...")
                db.session.execute(text("ALTER TABLE employee ADD COLUMN photo VARCHAR(255)"))
                db.session.commit()
                print("✓ Successfully added 'photo' column")
            else:
                print("✓ 'photo' column exists")

            # Support ticket: assigned_to_user_id
            result = db.session.execute(text("PRAGMA table_info(support_ticket)"))
            columns = [row[1] for row in result]
            if 'assigned_to_user_id' not in columns:
                print("Adding 'assigned_to_user_id' to support_ticket...")
                db.session.execute(text("ALTER TABLE support_ticket ADD COLUMN assigned_to_user_id INTEGER REFERENCES user(id)"))
                db.session.commit()
                print("✓ Added 'assigned_to_user_id'")
            else:
                print("✓ support_ticket.assigned_to_user_id exists")

            # Create ticket_note and ticket_activity tables
            db.create_all()
            print("✓ ticket_note / ticket_activity tables verified")

            # first_name / last_name on user table
            result = db.session.execute(text("PRAGMA table_info(user)"))
            columns = [row[1] for row in result]
            for col, typedef in [('first_name', 'VARCHAR(100)'), ('last_name', 'VARCHAR(100)')]:
                if col not in columns:
                    print(f"Adding '{col}' column to user table...")
                    db.session.execute(text(f"ALTER TABLE user ADD COLUMN {col} {typedef}"))
                    db.session.commit()
                    print(f"✓ Added '{col}'")
                else:
                    print(f"✓ user.{col} exists")

            # New support_ticket columns
            result = db.session.execute(text("PRAGMA table_info(support_ticket)"))
            st_cols = [row[1] for row in result]
            new_ticket_cols = [
                ('category',       "VARCHAR(50) DEFAULT 'General'"),
                ('merged_into_id', 'INTEGER'),
                ('csat_token',     'VARCHAR(64)'),
                ('csat_score',     'INTEGER'),
                ('csat_comment',   'TEXT'),
            ]
            for col, typedef in new_ticket_cols:
                if col not in st_cols:
                    print(f"Adding support_ticket.{col}...")
                    db.session.execute(text(f"ALTER TABLE support_ticket ADD COLUMN {col} {typedef}"))
                    db.session.commit()
                    print(f"✓ Added '{col}'")
                else:
                    print(f"✓ support_ticket.{col} exists")

            # Create asset_loan, installed_app tables
            db.create_all()
            print("✓ asset_loan / installed_app tables verified")

            return True
            
        except Exception as e:
            print(f"✗ Error ensuring schema: {e}")
            return False

if __name__ == "__main__":
    success = ensure_schema()
    sys.exit(0 if success else 1)
