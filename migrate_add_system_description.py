"""
Migration to add System Description table
"""
import sys
sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text

def migrate():
    with app.app_context():
        try:
            # Create system_description table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS system_description (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section_title TEXT NOT NULL,
                    section_level INTEGER DEFAULT 1,
                    section_order INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT,
                    auto_populated BOOLEAN DEFAULT 0,
                    template_content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by TEXT
                )
            """))
            
            db.session.commit()
            print("✓ Created system_description table")
            
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
