"""Add SOC 2 internal audit tracking tables."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_internal_audit (
                    id BIGSERIAL PRIMARY KEY,
                    audit_key VARCHAR(120) UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    scope TEXT,
                    status VARCHAR(50) DEFAULT 'Planned',
                    owner VARCHAR(200),
                    audit_period_start DATE,
                    audit_period_end DATE,
                    planned_date DATE,
                    performed_date DATE,
                    summary TEXT,
                    evidence_reference VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_internal_audit_finding (
                    id BIGSERIAL PRIMARY KEY,
                    audit_id BIGINT NOT NULL REFERENCES soc2_internal_audit (id) ON DELETE CASCADE,
                    readiness_item_id BIGINT REFERENCES soc2_readiness_item (id) ON DELETE SET NULL,
                    finding_key VARCHAR(120) UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    severity VARCHAR(50) DEFAULT 'Minor',
                    status VARCHAR(50) DEFAULT 'Open',
                    criteria_reference VARCHAR(200),
                    owner VARCHAR(200),
                    due_date DATE,
                    description TEXT,
                    recommendation TEXT,
                    evidence_reference VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_internal_audit_status ON soc2_internal_audit(status)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_internal_audit_finding_status ON soc2_internal_audit_finding(status)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_internal_audit_finding_audit ON soc2_internal_audit_finding(audit_id)"))

            db.session.commit()
            print("✓ Created SOC 2 internal audit tables")
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