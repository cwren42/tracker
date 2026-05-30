"""Add SOC 2 policy acknowledgement records table."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_policy_acknowledgement (
                    id BIGSERIAL PRIMARY KEY,
                    acknowledgement_key VARCHAR(120) UNIQUE NOT NULL,
                    employee_id BIGINT REFERENCES employee (id) ON DELETE SET NULL,
                    person_name VARCHAR(200) NOT NULL,
                    person_email VARCHAR(200),
                    department VARCHAR(100),
                    acknowledgement_type VARCHAR(100) DEFAULT 'Security Policy',
                    policy_name VARCHAR(200) NOT NULL,
                    policy_version VARCHAR(50),
                    acknowledged_on DATE NOT NULL,
                    status VARCHAR(50) DEFAULT 'Acknowledged',
                    evidence_reference VARCHAR(500),
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_policy_ack_employee ON soc2_policy_acknowledgement(employee_id)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_policy_ack_status ON soc2_policy_acknowledgement(status)"))
            db.session.commit()
            print("✓ Created SOC 2 policy acknowledgement table")
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