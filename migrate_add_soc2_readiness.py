"""Add SOC 2 readiness tracking tables."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_readiness_item (
                    id BIGSERIAL PRIMARY KEY,
                    item_key VARCHAR(120) UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    domain VARCHAR(120),
                    audit_alignment TEXT,
                    priority VARCHAR(50) DEFAULT 'P2-High',
                    status VARCHAR(50) DEFAULT 'Not In Place',
                    owner VARCHAR(200),
                    frequency VARCHAR(100),
                    source_type VARCHAR(50) DEFAULT 'manual',
                    source_reference VARCHAR(500),
                    manual_reference VARCHAR(500),
                    evidence_reference VARCHAR(500),
                    next_step TEXT,
                    notes TEXT,
                    due_date DATE,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_readiness_update (
                    id BIGSERIAL PRIMARY KEY,
                    readiness_item_id BIGINT NOT NULL REFERENCES soc2_readiness_item (id) ON DELETE CASCADE,
                    update_type VARCHAR(50) DEFAULT 'status_change',
                    previous_status VARCHAR(50),
                    new_status VARCHAR(50),
                    note TEXT,
                    created_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_readiness_item_status ON soc2_readiness_item(status)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_readiness_item_priority ON soc2_readiness_item(priority)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_readiness_update_item ON soc2_readiness_update(readiness_item_id)"))

            db.session.commit()
            print("✓ Created SOC 2 readiness tables")
            return True
        except Exception as exc:
            db.session.rollback()
            print(f"Error: {exc}")
            return False


if __name__ == '__main__':
    if migrate():
        print("\nMigration completed successfully!")
    else:
        print("\nMigration failed!")
        sys.exit(1)