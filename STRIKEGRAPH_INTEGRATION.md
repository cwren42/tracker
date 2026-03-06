# StrikeGraph Evidence Repository Integration

## Overview
Successfully integrated StrikeGraph SOC2 evidence repository with the Asset Tracker's automated compliance system.

## Data Summary

### Evidence Repository
- **Total Evidence Items**: 96
- **Mapped to SOC2 Controls**: 14 items (14.6%)
- **Automated (M365/Intune)**: 10 items
- **ISMS Policies**: 9 items
- **Manual Submission**: 77 items

### Evidence by Type
- **Policy**: 19 items (written policies and procedures)
- **Sample**: 21 items (examples of control execution)
- **General**: 37 items (lists, reports, configurations)
- **Settings**: 18 items (system configuration screenshots)
- **Population**: 1 item (parameter definitions)

## Automated Evidence Collection

### M365/Intune Integration (10 items)
Currently automated through Microsoft Graph API:

1. **Administrator Access to Application**
2. **Administrator Access to Database**
3. **Administrator Access to Network/Cloud**
4. **Administrator Access to Operating System**
5. **Application User List**
6. **Database User List**
7. **Antivirus Configuration - Server**
8. **Antivirus Configuration - Workstation**
9. **Asset Inventory**
10. **Device Disk Encryption**

### ISMS Policy References (9 items)
Mapped to ISMS-Manual2025v1.docx:

1. **Acceptable Use Policy** → IS-CIRQ-P-012-G
2. **Access Removal Procedures** → IS-CIRQ-P-008-G
3. **Backup Policy** → IS-CIRQ-P-010-G
4. **Backup Restoration Procedures** → IS-CIRQ-P-010-G
5. **Change Management Policy** → IS-CIRQ-PR-013-G
6. **Code of Conduct** → IS-CIRQ-P-006-G
7. **Data Classification Policy** → IS-CIRQ-P-009-G
8. **Data Management Policy** → IS-CIRQ-P-009-G
9. **Incident Response Plan** → IS-CIRQ-PR-011-G

## Control Mapping

### Evidence Items per SOC2 Control
- **Change Management Policy**: 7 items
- **Administrator Access**: 4 items
- **Incident Response**: 4 items
- **Business Continuity**: 4 items
- **Security Configuration**: 4 items
- **System Documentation**: 4 items
- **Policies & Training**: 8 items
- **Antivirus Protection**: 2 items
- **User Access Provisioning**: 2 items
- **User Access Termination**: 2 items
- **User Authentication**: 2 items
- **Risk Assessment**: 2 items
- **Data Protection**: 3 items
- **Asset Inventory**: 1 item

## Features Implemented

### 1. Database Model
- Created `StrikeGraphEvidence` table
- Stores evidence name, description, type, expiration dates
- Links to SOC2 controls via `control_id` foreign key
- Tracks automation source (M365/Intune, ISMS, Manual)
- Tracks submission status and owner

### 2. Data Loading Script
- `load_strikegraph_evidence.py` - Parses CSV and loads into database
- Automatically maps evidence to controls
- Determines automation source for each item
- Shows statistics and coverage analysis

### 3. StrikeGraph Evidence Page
- **Route**: `/soc2/strikegraph`
- **Features**:
  - Statistics dashboard (total, automated, ISMS, manual, mapped, expiring)
  - Expiring evidence alerts (< 30 days)
  - Filter by type (Policy, Sample, General, Settings)
  - Filter by source (Automated, ISMS, Manual)
  - Evidence table with descriptions, controls, owners
  - Direct links to SOC2 control evidence pages

### 4. Dashboard Integration
- Added "StrikeGraph Evidence" button to SOC2 dashboard
- Links from StrikeGraph items to control evidence pages
- Shows automation status badges

## Evidence Gaps

### 47 Unmapped Items
These StrikeGraph items don't yet have corresponding SOC2 controls:

**High Priority**:
- Password Policy & Settings (5 items)
- Vulnerability Management (3 items)
- Patch Management (2 items)
- Risk Assessment & Management (2 items)
- Vendor Management (4 items)
- Security Monitoring (3 items)

**Could Be Automated**:
- Network/Cloud User List
- Operating System User List
- Periodic Logical Access Review
- Intrusion Detection Configuration
- Server Encryption
- Patch Scan

## Next Steps

### Phase 1: Control Expansion
1. Add new SOC2 controls for unmapped evidence:
   - Password Management
   - Vulnerability Management
   - Patch Management
   - Vendor Risk Management
   - Security Monitoring

### Phase 2: Additional Automation
1. **Azure NSG/Firewall Rules**: Automate "Firewall Rules" evidence
2. **Azure Policy**: Automate "Security Configuration Standards"
3. **Defender for Endpoint**: Automate "Vulnerability Scan Results"
4. **Update Management**: Automate "Patch Scan" and "Server Scan and Patch"

### Phase 3: Manual Evidence Workflow
1. Add file upload for manual evidence
2. Track submission status and dates
3. Expiration notifications for evidence owners
4. Evidence package generation for StrikeGraph submission

### Phase 4: Scheduled Automation
1. Automatic evidence collection on expiration schedule
2. Email notifications for evidence owners
3. Evidence approval workflow
4. Integration with StrikeGraph API (if available)

## Usage

### Viewing Evidence Repository
1. Go to SOC2 Dashboard: https://tracker.corp.cirque.com/soc2
2. Click "StrikeGraph Evidence" button
3. Filter by type or automation source
4. Click control names to view collected evidence

### Running Data Sync
```bash
cd /var/www/tracker
python3 load_strikegraph_evidence.py
```

### Checking Evidence Status
```bash
cd /var/www/tracker
python3 parse_strikegraph_evidence.py
```

## Database Schema

```sql
CREATE TABLE strikegraph_evidence (
    id INTEGER PRIMARY KEY,
    control_id INTEGER REFERENCES soc2_control(id),
    evidence_name VARCHAR(255) UNIQUE NOT NULL,
    evidence_description TEXT,
    evidence_type VARCHAR(50),  -- Policy, Sample, General, Settings, Population
    expiration_schedule INTEGER,  -- Days until expiration
    expiration_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    owner VARCHAR(255),
    submission_status VARCHAR(50) DEFAULT 'Not Submitted',
    last_submitted_date DATETIME,
    file_path VARCHAR(500),
    automation_source VARCHAR(100),  -- M365/Intune, ISMS, Manual
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## Evidence Owners

Key stakeholders identified in StrikeGraph:
- **brenda.milian@cirque.com**: IT operations, employee management (11 items)
- **chris.wren@cirque.com**: Business continuity, vendor management (7 items)
- **Unassigned**: 78 items need owner assignment

## Files Modified

1. **soc2_models.py** - Added StrikeGraphEvidence model
2. **app.py** - Added /soc2/strikegraph route
3. **templates/soc2_strikegraph.html** - New evidence repository page
4. **templates/soc2_dashboard.html** - Added StrikeGraph button
5. **load_strikegraph_evidence.py** - Data loading script
6. **parse_strikegraph_evidence.py** - Analysis and mapping script

## Automation Coverage

**Current State**: 10.4% of StrikeGraph evidence is automated
**Target State**: 25-30% automation potential with additional API integrations

The remaining 70-75% will require manual documentation, screenshots, signed policies, and sample tickets - appropriate for SOC2 audit requirements.
