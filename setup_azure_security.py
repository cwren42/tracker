#!/usr/bin/env python3
"""
Setup Azure Subscription ID for Security evidence collection
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db, Setting

def setup_azure_subscription():
    """Configure Azure subscription ID"""
    
    with app.app_context():
        print("="*80)
        print("AZURE SECURITY SETUP")
        print("="*80)
        print()
        
        # Check existing M365 credentials
        tenant = Setting.query.filter_by(key='m365_tenant_id').first()
        client = Setting.query.filter_by(key='m365_client_id').first()
        
        if not tenant or not client:
            print("❌ M365 credentials not configured!")
            print("   Please configure M365 settings first.")
            return
        
        print(f"✓ M365 Tenant ID: {tenant.value}")
        print(f"✓ M365 Client ID: {client.value}")
        print()
        
        # Check/set subscription ID
        subscription = Setting.query.filter_by(key='azure_subscription_id').first()
        
        if subscription:
            print(f"Current Azure Subscription ID: {subscription.value}")
            print()
            update = input("Update? (y/N): ").strip().lower()
            if update != 'y':
                return
        else:
            print("No Azure Subscription ID configured.")
            print()
        
        print("To find your Azure Subscription ID:")
        print("1. Go to https://portal.azure.com")
        print("2. Search for 'Subscriptions'")
        print("3. Copy your subscription ID")
        print()
        
        sub_id = input("Enter Azure Subscription ID: ").strip()
        
        if not sub_id:
            print("❌ Subscription ID required")
            return
        
        # Validate format (should be a GUID)
        if len(sub_id) != 36 or sub_id.count('-') != 4:
            print("⚠ Warning: Subscription ID doesn't look like a GUID")
            confirm = input("Continue anyway? (y/N): ").strip().lower()
            if confirm != 'y':
                return
        
        # Save to database
        if subscription:
            subscription.value = sub_id
        else:
            subscription = Setting(key='azure_subscription_id', value=sub_id)
            db.session.add(subscription)
        
        db.session.commit()
        
        print()
        print("✓ Azure Subscription ID saved!")
        print()
        print("="*80)
        print("REQUIRED AZURE API PERMISSIONS")
        print("="*80)
        print()
        print("Your Azure App Registration needs these permissions:")
        print()
        print("APPLICATION PERMISSIONS:")
        print("  • Microsoft Graph:")
        print("    - User.Read.All")
        print("    - Directory.Read.All")
        print("    - DeviceManagementManagedDevices.Read.All")
        print("    - DeviceManagementApps.Read.All")
        print()
        print("  • Azure Service Management:")
        print("    - user_impersonation")
        print()
        print("ROLE ASSIGNMENTS (on Subscription):")
        print("  • Reader (to read all resources)")
        print("  • Security Reader (to read security assessments)")
        print()
        print("To grant permissions:")
        print("1. Go to Azure Portal → Azure Active Directory → App registrations")
        print("2. Select your app: 'Asset Tracker SOC2'")
        print("3. Go to 'API permissions' → Add the above permissions")
        print("4. Click 'Grant admin consent'")
        print("5. Go to Subscriptions → Access control (IAM)")
        print("6. Add role assignment → Select 'Reader' and 'Security Reader'")
        print("7. Assign to your app's service principal")
        print()
        print("✓ Setup complete! You can now run Azure Security sync from the SOC2 dashboard.")

if __name__ == '__main__':
    setup_azure_subscription()
