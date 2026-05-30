"""Add SOC 2 management review tables."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_management_review (
                    id BIGSERIAL PRIMARY KEY,
                    review_key VARCHAR(120) UNIQUE NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    review_date DATE NOT NULL,
                    review_period_start DATE,
                    review_period_end DATE,
                    chairperson VARCHAR(200),
                    minute_taker VARCHAR(200),
                    location VARCHAR(200),
                    status VARCHAR(50) DEFAULT 'Planned',
                    attendees TEXT,
                    agenda_summary TEXT,
                    decisions_summary TEXT,
                    effectiveness_summary TEXT,
                    resource_summary TEXT,
                    evidence_reference VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_management_review_action (
                    id BIGSERIAL PRIMARY KEY,
                    review_id BIGINT NOT NULL REFERENCES soc2_management_review (id) ON DELETE CASCADE,
                    action_key VARCHAR(120) UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    owner VARCHAR(200),
                    due_date DATE,
                    status VARCHAR(50) DEFAULT 'Open',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_management_review_status ON soc2_management_review(status)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_management_review_action_status ON soc2_management_review_action(status)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_management_review_action_review ON soc2_management_review_action(review_id)"))

            db.session.commit()
            print("✓ Created SOC 2 management review tables")
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