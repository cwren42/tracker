# SOC2 Compliance Automation - Implementation Guide

## Overview
This guide covers the implementation of automated SOC2 evidence collection from Microsoft 365 and Intune for annual StrikeGraph audits.

## Architecture

### Components
1. **Database Models** (`soc2_models.py`) - 9 new tables for compliance tracking
2. **M365 Service** (`m365_service.py`) - Microsoft Graph API integration
3. **Sync Service** (`soc2_sync_service.py`) - Automated evidence collection
4. **Control Initialization** (`init_soc2_controls.py`) - Populate controls from StrikeGraph

### New Database Tables
- `soc2_control` - Control definitions from StrikeGraph
- `evidence_snapshot` - Historical evidence for audit trail
- `m365_user` - Microsoft 365 user snapshots
- `intune_device` - Intune device snapshots
- `device_software` - Software inventory
- `admin_role_snapshot` - Admin role assignments over time
- `compliance_report` - Generated reports for StrikeGraph
- `soc2_audit_log` - Audit trail for all SOC2 actions

## Phase 1: Azure App Registration (15-30 minutes)

### Step 1: Create Azure App Registration
1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Azure Active Directory** → **App registrations** → **New registration**
3. Name: `Asset Tracker SOC2 Integration`
4. Supported account types: **Single tenant**
5. Click **Register**

### Step 2: Configure API Permissions
Add the following **Application permissions** (not Delegated):

**Microsoft Graph API:**
- `User.Read.All` - Read all users
- `Group.Read.All` - Read all groups
- `Directory.Read.All` - Read directory data
- `RoleManagement.Read.Directory` - Read admin roles
- `DeviceManagementManagedDevices.Read.All` - Read Intune devices
- `DeviceManagementApps.Read.All` - Read installed apps
- `DeviceManagementConfiguration.Read.All` - Read device configuration
- `AuditLog.Read.All` - Read audit logs (optional, for provisioning tracking)

**Grant admin consent** for all permissions.

### Step 3: Create Client Secret
1. Go to **Certificates & secrets** → **New client secret**
2. Description: `SOC2 Integration`
3. Expiry: **24 months** (or custom)
4. **Copy the secret value** - you won't see it again!

### Step 4: Get Your Credentials
Note these values:
- **Tenant ID**: From Overview page
- **Client ID** (Application ID): From Overview page
- **Client Secret**: From previous step

## Phase 2: Database Setup (10 minutes)

### Step 1: Import Models into app.py
Add to the top of `app.py` after existing imports:

```python
# Import SOC2 models
from soc2_models import (
    SOC2Control, EvidenceSnapshot, M365User, IntuneDevice,
    DeviceSoftware, AdminRoleSnapshot, ComplianceReport, AuditLog
)
```

### Step 2: Create Database Tables
```bash
cd /var/www/tracker
source venv/bin/activate
python3 << EOF
from app import app, db
from soc2_models import *

with app.app_context():
    db.create_all()
    print("✅ SOC2 tables created successfully")
