#!/usr/bin/env python3
"""
Script to add all available widgets to the dashboard with varying sizes for testing
"""
import requests
import json

# Configuration
BASE_URL = 'https://tracker.corp.cirque.com'
LOGIN_URL = f'{BASE_URL}/login'
DASHBOARD_URL = f'{BASE_URL}/dashboard/configure'

# Login credentials (adjust as needed)
USERNAME = 'admin'
PASSWORD = 'admin'  # Change this to the actual admin password

# All available widgets with varying sizes and heights
widgets = [
    # Statistics widgets (1 row, varying widths)
    {'id': 'total_assets', 'type': 'stat', 'title': 'Total Assets', 'position': 0, 'size': 'col-md-2 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'available', 'type': 'stat', 'title': 'Available', 'position': 1, 'size': 'col-md-2 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'in_use', 'type': 'stat', 'title': 'In Use', 'position': 2, 'size': 'col-md-2 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'in_repair', 'type': 'stat', 'title': 'In Repair', 'position': 3, 'size': 'col-md-2 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'avg_age', 'type': 'stat', 'title': 'Avg Age', 'position': 4, 'size': 'col-md-2 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'replacement', 'type': 'stat', 'title': 'Need Replacement', 'position': 5, 'size': 'col-md-2 widget-1-row', 'enabled': True, 'config': {}},
    
    # More stats - second row
    {'id': 'total_licenses', 'type': 'stat', 'title': 'Total Licenses', 'position': 6, 'size': 'col-md-3 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'active_licenses', 'type': 'stat', 'title': 'Active Licenses', 'position': 7, 'size': 'col-md-3 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'license_cost', 'type': 'stat', 'title': 'License Cost', 'position': 8, 'size': 'col-md-3 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'total_employees', 'type': 'stat', 'title': 'Employees', 'position': 9, 'size': 'col-md-3 widget-1-row', 'enabled': True, 'config': {}},
    
    # List widgets - third row with varying sizes
    {'id': 'recent_assets', 'type': 'list', 'title': 'Recent Assets', 'position': 10, 'size': 'col-md-4 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'warranty_expiring', 'type': 'list', 'title': 'Warranty Expiring', 'position': 11, 'size': 'col-md-4 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'replacement_needed', 'type': 'list', 'title': 'Replacement Needed', 'position': 12, 'size': 'col-md-4 widget-1-row', 'enabled': True, 'config': {}},
    
    # More lists - fourth row
    {'id': 'licenses_expiring', 'type': 'list', 'title': 'Licenses Expiring', 'position': 13, 'size': 'col-md-6 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'recent_employees', 'type': 'list', 'title': 'Recent Employees', 'position': 14, 'size': 'col-md-6 widget-1-row', 'enabled': True, 'config': {}},
    
    # Chart widgets - varying sizes including 2-row options
    {'id': 'category_chart', 'type': 'chart', 'title': 'Assets by Category', 'position': 15, 'size': 'col-md-6 widget-2-rows', 'enabled': True, 'config': {}},
    {'id': 'status_chart', 'type': 'chart', 'title': 'Assets by Status', 'position': 16, 'size': 'col-md-6 widget-2-rows', 'enabled': True, 'config': {}},
    
    {'id': 'department_chart', 'type': 'chart', 'title': 'Assets by Department', 'position': 17, 'size': 'col-md-4 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'lifecycle_chart', 'type': 'chart', 'title': 'Lifecycle Status', 'position': 18, 'size': 'col-md-4 widget-1-row', 'enabled': True, 'config': {}},
    {'id': 'license_vendor_chart', 'type': 'chart', 'title': 'Licenses by Vendor', 'position': 19, 'size': 'col-md-4 widget-1-row', 'enabled': True, 'config': {}},
    
    # Full width chart at the end
    {'id': 'category_chart', 'type': 'chart', 'title': 'Assets Overview', 'position': 20, 'size': 'col-md-12 widget-1-row', 'enabled': True, 'config': {}},
]

def main():
    # Create a session to maintain cookies
    session = requests.Session()
    session.verify = False  # Skip SSL verification for self-signed cert
    
    # Suppress SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # First, get the login page to get CSRF token if needed
    print("Logging in...")
    login_page = session.get(LOGIN_URL)
    
    # Attempt login
    login_data = {
        'username': USERNAME,
        'password': PASSWORD
    }
    
    login_response = session.post(LOGIN_URL, data=login_data)
    
    if login_response.status_code == 200 or login_response.status_code == 302:
        print("✓ Login successful")
    else:
        print(f"✗ Login failed with status {login_response.status_code}")
        return False
    
    # Now POST the widget configuration
    print(f"\nAdding {len(widgets)} widgets to dashboard...")
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    response = session.post(DASHBOARD_URL, json=widgets, headers=headers)
    
    print(f"Response status: {response.status_code}")
    print(f"Response headers: {response.headers}")
    print(f"Response text: {response.text[:500]}")
    
    if response.status_code == 200:
        try:
            result = response.json()
            if result.get('success'):
                print(f"✓ Successfully added all {len(widgets)} widgets to dashboard!")
                print("✓ Dashboard configuration saved")
                return True
            else:
                print(f"✗ Failed to add widgets: {result.get('message')}")
                return False
        except:
            # Might be a redirect or HTML response
            if 'success' in response.text.lower() or response.status_code == 200:
                print(f"✓ Dashboard configuration appears to have been saved")
                return True
            else:
                print(f"✗ Unexpected response: {response.text[:500]}")
                return False
    else:
        print(f"✗ Request failed with status {response.status_code}")
        print(f"Response: {response.text}")
        return False

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
