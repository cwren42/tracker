"""Add SOC 2 vendor management tables."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_vendor (
                    id BIGSERIAL PRIMARY KEY,
                    vendor_key VARCHAR(120) UNIQUE NOT NULL,
                    vendor_name VARCHAR(200) NOT NULL,
                    service_description TEXT,
                    vendor_type VARCHAR(100),
                    criticality VARCHAR(50) DEFAULT 'Medium',
                    risk_level VARCHAR(50) DEFAULT 'Medium',
                    owner VARCHAR(200),
                    data_access_scope TEXT,
                    contract_status VARCHAR(50) DEFAULT 'Active',
                    assurance_status VARCHAR(100),
                    last_review_date DATE,
                    next_review_date DATE,
                    evidence_reference VARCHAR(500),
                    notes TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_vendor_review (
                    id BIGSERIAL PRIMARY KEY,
                    vendor_id BIGINT NOT NULL REFERENCES soc2_vendor (id) ON DELETE CASCADE,
                    review_date DATE NOT NULL,
                    review_type VARCHAR(100) DEFAULT 'Annual Review',
                    status VARCHAR(50) DEFAULT 'Completed',
                    reviewer VARCHAR(200),
                    summary TEXT,
                    findings TEXT,
                    evidence_reference VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_vendor_active ON soc2_vendor(is_active)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_vendor_risk ON soc2_vendor(risk_level)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_vendor_review_vendor ON soc2_vendor_review(vendor_id)"))

            db.session.commit()
            print("✓ Created SOC 2 vendor management tables")
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