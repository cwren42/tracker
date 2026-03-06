"""
Verify SOC2 Setup - Check all components are properly configured
"""
from app import app, db, Setting
from soc2_models import SOC2Control, M365User, IntuneDevice
import sys

def verify_setup():
    """Run complete setup verification"""
    
    with app.app_context():
        print("🔍 SOC2 Setup Verification")
        print("=" * 60)
        
        # Check database tables
        print("\n1️⃣ Database Tables:")
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        required_tables = [
            'soc2_control', 'evidence_snapshot', 'm365_user',
            'intune_device', 'device_software', 'admin_role_snapshot',
            'compliance_report', 'soc2_audit_log'
        ]
        
        all_tables_ok = True
        for table in required_tables:
            status = "✅" if table in tables else "❌"
            print(f"   {status} {table}")
            if table not in tables:
                all_tables_ok = False
        
        if not all_tables_ok:
            print("\n⚠️ Some tables are missing. Run: python3 init_soc2_controls.py")
            return False
        
        # Check controls
        print("\n2️⃣ SOC2 Controls:")
        controls = SOC2Control.query.all()
        automated_controls = SOC2Control.query.filter_by(automation_enabled=True).all()
        
        print(f"   ✅ Total controls: {len(controls)}")
        print(f"   ✅ Automated controls: {len(automated_controls)}")
        
        if len(controls) == 0:
            print("   ⚠️ No controls found. Run: python3 init_soc2_controls.py")
            return False
        
        print("\n   📋 Automated Controls:")
        for ctrl in automated_controls:
            freq_icon = {"Daily": "📅", "Weekly": "📆", "Annually": "🗓️"}.get(ctrl.control_frequency, "🔄")
            print(f"      {freq_icon} {ctrl.control_name} ({ctrl.control_frequency})")
        
        # Check M365 credentials
        print("\n3️⃣ Microsoft 365 Configuration:")
        tenant_id = Setting.query.filter_by(key='m365_tenant_id').first()
        client_id = Setting.query.filter_by(key='m365_client_id').first()
        client_secret = Setting.query.filter_by(key='m365_client_secret').first()
        
        if tenant_id and client_id and client_secret:
            print("   ✅ Tenant ID configured")
            print("   ✅ Client ID configured")
            print("   ✅ Client Secret configured")
            credentials_ok = True
        else:
            print("   ❌ M365 credentials not configured")
            print("\n   📝 To configure, add to settings table:")
            print("   INSERT INTO setting (key, value, updated_by) VALUES")
            print("   ('m365_tenant_id', 'YOUR_TENANT_ID', 'admin'),")
            print("   ('m365_client_id', 'YOUR_CLIENT_ID', 'admin'),")
            print("   ('m365_client_secret', 'YOUR_CLIENT_SECRET', 'admin');")
            credentials_ok = False
        
        # Check if sync has been run
        print("\n4️⃣ Data Collection Status:")
        m365_users = M365User.query.count()
        intune_devices = IntuneDevice.query.count()
        
        print(f"   📊 M365 Users synced: {m365_users}")
        print(f"   📊 Intune Devices synced: {intune_devices}")
        
        if m365_users == 0 and intune_devices == 0:
            print("   ℹ️  No data synced yet (expected before first sync)")
        
        # Summary
        print("\n" + "=" * 60)
        print("📊 Setup Summary:")
        
        if all_tables_ok and len(controls) > 0:
            print("   ✅ Database: Ready")
            print("   ✅ Controls: Initialized")
            
            if credentials_ok:
                print("   ✅ Credentials: Configured")
                print("\n🎉 Setup Complete! Ready to run first sync.")
                print("\n📝 Next Steps:")
                print("   1. Test connection: python3 test_m365_connection.py")
                print("   2. Run first sync: python3 run_first_sync.py")
                print("   3. Restart tracker service: sudo systemctl restart tracker")
            else:
                print("   ⚠️  Credentials: Not configured")
                print("\n📝 Next Steps:")
                print("   1. Create Azure App Registration")
                print("   2. Add credentials to settings table")
                print("   3. Test connection and run first sync")
        else:
            print("   ❌ Setup incomplete")
            print("\n📝 Fix Issues:")
            print("   - Run: python3 init_soc2_controls.py")
            print("   - Check SOC2_IMPLEMENTATION_GUIDE.md")
        
        print("=" * 60)
        return all_tables_ok and len(controls) > 0

if __name__ == '__main__':
    success = verify_setup()
    sys.exit(0 if success else 1)
