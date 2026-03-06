"""
Migration to add Policy and Procedure management tables
"""
import sys
sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text

def migrate():
    with app.app_context():
        try:
            # Create policies table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS policy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT,
                    division TEXT,
                    standard_type TEXT,
                    version TEXT,
                    effective_date TEXT,
                    review_date TEXT,
                    approved_by TEXT,
                    content TEXT,
                    file_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by TEXT
                )
            """))
            
            # Create policy_sections table for parsed sections
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS policy_section (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_id INTEGER NOT NULL,
                    section_number TEXT,
                    section_title TEXT NOT NULL,
                    section_content TEXT,
                    section_order INTEGER,
                    FOREIGN KEY (policy_id) REFERENCES policy (id) ON DELETE CASCADE
                )
            """))
            
            # Create policy_control_mapping table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS policy_control_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_id INTEGER NOT NULL,
                    control_id TEXT NOT NULL,
                    mapping_type TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (policy_id) REFERENCES policy (id) ON DELETE CASCADE
                )
            """))
            
            # Create policy_system_description_mapping table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS policy_system_desc_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_id INTEGER NOT NULL,
                    system_desc_id INTEGER NOT NULL,
                    relevance_score INTEGER DEFAULT 5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (policy_id) REFERENCES policy (id) ON DELETE CASCADE,
                    FOREIGN KEY (system_desc_id) REFERENCES system_description (id) ON DELETE CASCADE
                )
            """))
            
            db.session.commit()
            print("✓ Created policy management tables")
            
        except Exception as e:
            print(f"Error: {e}")
            db.session.rollback()
            return False
    
    return True

if __name__ == '__main__':
    if migrate():
        print("\nMigration completed successfully!")
    else:
        print("\nMigration failed!")
        sys.exit(1)
