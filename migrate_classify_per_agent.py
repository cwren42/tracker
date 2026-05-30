#!/usr/bin/env python3
"""
Migration: Add agent_id to rmm_eagle_app_class for per-asset browser site rules.
Run once with:  python3 migrate_classify_per_agent.py
"""
import sys
from app import app
from extensions import db
from sqlalchemy import text

def run():
    with app.app_context():
        conn = db.session

        # 1. Add agent_id column (nullable FK to rmm_agent)
        conn.execute(text("""
            ALTER TABLE rmm_eagle_app_class
            ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)
            REFERENCES rmm_agent(agent_id) ON DELETE CASCADE
        """))
        print("✓ Added agent_id column")

        # 2. Drop the old global partial unique index on window_title_pattern
        conn.execute(text("DROP INDEX IF EXISTS rmm_eagle_app_class_wtp_uniq"))
        print("✓ Dropped old wtp_uniq index")

        # 3. Create new partial unique index for global site rules (agent_id IS NULL)
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS rmm_eagle_app_class_wtp_global_uniq
            ON rmm_eagle_app_class(window_title_pattern)
            WHERE window_title_pattern IS NOT NULL AND agent_id IS NULL
        """))
        print("✓ Created wtp_global_uniq index")

        # 4. Create new partial unique index for per-agent site rules (agent_id IS NOT NULL)
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS rmm_eagle_app_class_wtp_agent_uniq
            ON rmm_eagle_app_class(window_title_pattern, agent_id)
            WHERE window_title_pattern IS NOT NULL AND agent_id IS NOT NULL
        """))
        print("✓ Created wtp_agent_uniq index")

        db.session.commit()
        print("✓ Migration complete")

if __name__ == '__main__':
    try:
        run()
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)
