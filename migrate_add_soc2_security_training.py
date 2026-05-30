"""Add SOC 2 security training records table."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS soc2_security_training_record (
                    id BIGSERIAL PRIMARY KEY,
                    record_key VARCHAR(120) UNIQUE NOT NULL,
                    employee_id BIGINT REFERENCES employee (id) ON DELETE SET NULL,
                    trainee_name VARCHAR(200) NOT NULL,
                    trainee_email VARCHAR(200),
                    department VARCHAR(100),
                    role_title VARCHAR(100),
                    training_date DATE NOT NULL,
                    training_topic VARCHAR(200) NOT NULL,
                    provider_method VARCHAR(200),
                    duration VARCHAR(100),
                    completion_status VARCHAR(50) DEFAULT 'Completed',
                    score INTEGER,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_security_training_employee ON soc2_security_training_record(employee_id)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_security_training_date ON soc2_security_training_record(training_date)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_soc2_security_training_status ON soc2_security_training_record(completion_status)"))
            db.session.commit()
            print("✓ Created SOC 2 security training table")
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