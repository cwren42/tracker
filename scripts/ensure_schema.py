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
            
            return True
            
        except Exception as e:
            print(f"✗ Error ensuring schema: {e}")
            return False

if __name__ == "__main__":
    success = ensure_schema()
    sys.exit(0 if success else 1)
