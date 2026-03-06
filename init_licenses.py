from app import app, db, License, LicenseAssignment

with app.app_context():
    # Create the new tables
    db.create_all()
    print("License tables created successfully!")
    
    # Verify tables exist
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    print(f"Available tables: {tables}")
    
    if 'license' in tables and 'license_assignment' in tables:
        print("✓ License and LicenseAssignment tables are ready!")
    else:
        print("✗ Tables not found!")
