"""
Run First Sync - Collect initial evidence from M365/Intune
"""
from app import app, db
from soc2_sync_service import SOC2SyncService
import time

def run_first_sync():
    print("🚀 Running First SOC2 Sync")
    print("=" * 60)
    print("This will collect evidence from Microsoft 365 and Intune")
    print("This may take several minutes depending on your organization size...")
    print()
    
    sync = SOC2SyncService(app, db)
    
    try:
        print("1️⃣ Syncing M365 Users and Admin Roles...")
        start = time.time()
        user_result = sync.sync_m365_users()
        elapsed = time.time() - start
        
        if user_result['success']:
            print(f"   ✅ Synced {user_result['users_synced']} users")
            print(f"   ✅ Found {user_result['admins']} admin role assignments")
            print(f"   ⏱️ Completed in {elapsed:.1f} seconds")
        else:
            print(f"   ❌ Failed: {user_result.get('error')}")
            return False
        
        print("\n2️⃣ Syncing Intune Devices...")
        start = time.time()
        device_result = sync.sync_intune_devices()
        elapsed = time.time() - start
        
        if device_result['success']:
            print(f"   ✅ Synced {device_result['devices_synced']} devices")
            print(f"   ✅ {device_result['compliant']} devices compliant")
            print(f"   ⏱️ Completed in {elapsed:.1f} seconds")
        else:
            print(f"   ❌ Failed: {device_result.get('error')}")
            return False
        
        print("\n3️⃣ Syncing Software Inventory...")
        start = time.time()
        software_result = sync.sync_software_inventory()
        elapsed = time.time() - start
        
        if software_result['success']:
            print(f"   ✅ Found {software_result['apps']} unique applications")
            print(f"   ✅ Tracked {software_result['installations']} installations")
            print(f"   ⏱️ Completed in {elapsed:.1f} seconds")
        else:
            print(f"   ❌ Failed: {software_result.get('error')}")
            return False
        
        print("\n" + "=" * 60)
        print("🎉 First Sync Complete!")
        print("\n📊 Summary:")
        print(f"   👥 Users: {user_result['users_synced']}")
        print(f"   🔐 Admins: {user_result['admins']}")
        print(f"   💻 Devices: {device_result['devices_synced']}")
        print(f"   📦 Software: {software_result['apps']} apps")
        print(f"   ✅ Compliant: {device_result['compliant']}/{device_result['devices_synced']}")
        print("\n📝 Next Steps:")
        print("   1. Review data: python3 verify_soc2_setup.py")
        print("   2. Check database tables for collected evidence")
        print("   3. Set up automated scheduling in app.py")
        print("   4. Restart tracker service: sudo systemctl restart tracker")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ Sync failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    run_first_sync()
