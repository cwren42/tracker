"""
Test M365 Connection - Verify Azure credentials work
"""
from app import app, Setting
from m365_service import M365Service

def test_connection():
    with app.app_context():
        print("🔌 Testing Microsoft 365 Connection...")
        print("=" * 60)
        
        # Get credentials
        tenant = Setting.query.filter_by(key='m365_tenant_id').first()
        client = Setting.query.filter_by(key='m365_client_id').first()
        secret = Setting.query.filter_by(key='m365_client_secret').first()
        
        if not all([tenant, client, secret]):
            print("❌ M365 credentials not configured")
            print("\n📝 Add credentials first:")
            print("   INSERT INTO setting (key, value, updated_by) VALUES")
            print("   ('m365_tenant_id', 'YOUR_TENANT_ID', 'admin'),")
            print("   ('m365_client_id', 'YOUR_CLIENT_ID', 'admin'),")
            print("   ('m365_client_secret', 'YOUR_CLIENT_SECRET', 'admin');")
            return False
        
        print("✅ Credentials found in settings")
        print(f"   Tenant ID: {tenant.value[:8]}...")
        print(f"   Client ID: {client.value[:8]}...")
        print(f"   Secret: {'*' * 32}...")
        
        # Initialize service
        print("\n🔐 Authenticating with Microsoft...")
        service = M365Service(tenant.value, client.value, secret.value)
        
        # Test connection
        result = service.test_connection()
        
        print("\n" + "=" * 60)
        if result['success']:
            print("✅ SUCCESS!")
            print(f"   Organization: {result['organization']}")
            print(f"   Message: {result['message']}")
            
            # Try to get user count
            print("\n📊 Testing data access...")
            try:
                users = service.get_all_users()
                print(f"   ✅ Found {len(users)} users")
                
                devices = service.get_managed_devices()
                print(f"   ✅ Found {len(devices)} managed devices")
                
                print("\n🎉 All tests passed! Ready to run first sync.")
            except Exception as e:
                print(f"   ⚠️ Data access error: {e}")
        else:
            print("❌ FAILED!")
            print(f"   Error: {result.get('error', 'Unknown error')}")
            print(f"   Message: {result['message']}")
            print("\n📝 Check:")
            print("   - Tenant ID is correct")
            print("   - Client ID is correct")
            print("   - Client Secret is valid")
            print("   - Admin consent granted in Azure Portal")
        
        print("=" * 60)
        return result['success']

if __name__ == '__main__':
    test_connection()
