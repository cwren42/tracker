"""Add SOC 2 phishing campaign and result tables."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_phishing_campaign (
                    id BIGSERIAL PRIMARY KEY,
                    campaign_key VARCHAR(120) UNIQUE NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    campaign_date DATE NOT NULL,
                    provider VARCHAR(200),
                    scope VARCHAR(200),
                    status VARCHAR(50) DEFAULT 'Completed',
                    scenario TEXT,
                    follow_up_training_topic VARCHAR(200),
                    summary TEXT,
                    evidence_reference VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_phishing_result (
                    id BIGSERIAL PRIMARY KEY,
                    campaign_id BIGINT NOT NULL REFERENCES soc2_phishing_campaign (id) ON DELETE CASCADE,
                    employee_id BIGINT REFERENCES employee (id) ON DELETE SET NULL,
                    result_key VARCHAR(120) UNIQUE NOT NULL,
                    employee_name VARCHAR(200) NOT NULL,
                    employee_email VARCHAR(200),
                    department VARCHAR(100),
                    delivered BOOLEAN DEFAULT TRUE,
                    opened BOOLEAN DEFAULT TRUE,
                    clicked BOOLEAN DEFAULT FALSE,
                    reported BOOLEAN DEFAULT FALSE,
                    training_completed BOOLEAN DEFAULT FALSE,
                    training_completed_on DATE,
                    outcome VARCHAR(100) DEFAULT 'Completed Follow-up Training',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_phishing_campaign_date ON soc2_phishing_campaign(campaign_date)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_phishing_result_campaign ON soc2_phishing_result(campaign_id)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_phishing_result_employee ON soc2_phishing_result(employee_id)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_phishing_result_outcome ON soc2_phishing_result(outcome)"))
            db.session.commit()
            print("✓ Created SOC 2 phishing tracking tables")
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