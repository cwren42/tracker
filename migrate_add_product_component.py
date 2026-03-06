from app import app, db
from sqlalchemy import text

with app.app_context():
    # Check if column already exists
    inspector = db.inspect(db.engine)
    columns = [col['name'] for col in inspector.get_columns('license_assignment')]
    
    if 'product_component' not in columns:
        print("Adding product_component column to license_assignment table...")
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE license_assignment ADD COLUMN product_component VARCHAR(200)'))
            conn.commit()
        print("✓ Column added successfully!")
    else:
        print("✓ Column already exists, no migration needed.")
    
    # Verify
    columns = [col['name'] for col in inspector.get_columns('license_assignment')]
    print(f"\nLicense Assignment columns: {columns}")
