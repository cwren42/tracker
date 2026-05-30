"""Add core ISMS document/version/export tables."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text


def migrate():
    with app.app_context():
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS isms_document (
                    id BIGSERIAL PRIMARY KEY,
                    slug VARCHAR(255) UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    doc_type VARCHAR(50) DEFAULT 'policy',
                    category VARCHAR(100),
                    status VARCHAR(20) DEFAULT 'draft',
                    source_path VARCHAR(500) UNIQUE,
                    current_version_id BIGINT,
                    created_by VARCHAR(100),
                    updated_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS isms_document_version (
                    id BIGSERIAL PRIMARY KEY,
                    document_id BIGINT NOT NULL REFERENCES isms_document (id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL,
                    markdown_body TEXT NOT NULL,
                    rendered_html TEXT,
                    change_summary TEXT,
                    is_restore BOOLEAN DEFAULT FALSE,
                    restored_from_version_id BIGINT REFERENCES isms_document_version (id) ON DELETE SET NULL,
                    created_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (document_id, version_number)
                )
            """))

            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS isms_export_run (
                    id BIGSERIAL PRIMARY KEY,
                    document_id BIGINT NOT NULL REFERENCES isms_document (id) ON DELETE CASCADE,
                    document_version_id BIGINT REFERENCES isms_document_version (id) ON DELETE SET NULL,
                    export_format VARCHAR(20) NOT NULL,
                    status VARCHAR(20) DEFAULT 'pending',
                    output_path VARCHAR(500),
                    generated_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            db.session.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'fk_isms_document_current_version'
                    ) THEN
                        ALTER TABLE isms_document
                        ADD CONSTRAINT fk_isms_document_current_version
                        FOREIGN KEY (current_version_id)
                        REFERENCES isms_document_version (id)
                        ON DELETE SET NULL;
                    END IF;
                END $$;
            """))

            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_isms_document_status ON isms_document(status)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_isms_document_doc_type ON isms_document(doc_type)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_isms_document_version_document ON isms_document_version(document_id)"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_isms_export_run_document ON isms_export_run(document_id)"))

            db.session.commit()
            print("✓ Created ISMS module tables")
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