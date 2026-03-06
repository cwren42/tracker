# StrikeGraph Evidence File Generation

## Overview
The Asset Tracker now automatically generates downloadable evidence files for StrikeGraph compliance submissions. Each piece of automated evidence (M365/Intune, Azure) can be exported as a professionally formatted Excel file.

## Features

### Automated Evidence Files
- **M365 Users**: Complete user lists with job titles, departments, admin status
- **Administrator Access**: Filtered admin user lists with role assignments
- **Intune Devices**: Device inventory with compliance, encryption status
- **Device Software**: Application inventory across all managed devices
- **Azure NSG**: Network Security Groups with firewall rules
- **Security Alerts**: Azure Defender alerts and remediation steps
- **SQL Databases**: Database inventory with TDE encryption status
- **Storage Accounts**: Storage with encryption and HTTPS settings
- **Virtual Machines**: VM inventory with disk encryption status
- **Security Assessments**: Vulnerability scan results from Azure

### File Organization
Files are automatically organized by source:
```
/var/www/tracker/static/evidence/
├── m365/          - M365 and Intune evidence
├── azure/         - Azure Security evidence
├── isms/          - ISMS policy documents
└── manual/        - Manually uploaded evidence
```

## Usage

### Generate Evidence Files

1. **Navigate to StrikeGraph Evidence Page**
   - Go to SOC2 Dashboard → "StrikeGraph Evidence" button
   - Or directly: https://tracker.corp.cirque.com/soc2/strikegraph

2. **Generate All Files**
   - Click "Generate Evidence Files" button
   - System will create Excel files for all 22 automated evidence items
   - Progress notification shows success/error counts

3. **Download Individual Files**
   - Each evidence row now has a "Download" button
   - Click to download the specific evidence file
   - Files are named with evidence name and date

4. **Download All as ZIP**
   - Click "Download All (ZIP)" button
   - Creates a single ZIP archive with all evidence files
   - Useful for batch upload to StrikeGraph

### Evidence Mapping

The system automatically generates files for these evidence types:

#### M365/Intune Evidence (10 items)
- Administrator Access to Application
- Administrator Access to Database
- Administrator Access to Network/Cloud
- Administrator Access to Operating System
- Application User List
- Database User List
- Network User List
- Operating System User List
- User Access List
- Workstation Asset Inventory

#### Azure Security Evidence (12 items)
- Firewall Rules (NSG configurations)
- Intrusion Detection System Configuration (Security alerts)
- SQL Server Database Encryption Configuration
- Azure Storage Encryption Configuration
- Server Disk Encryption Configuration
- Workstation Disk Encryption Configuration
- Vulnerability Scan Results - External
- Vulnerability Scan Results - Internal
- Current Network Diagram (Network topology)
- Monitoring Tools Enabled (Azure Monitor alerts)
- Database Encryption (SQL TDE status)
- Server Encryption (VM disk encryption)

## File Formats

### Excel Files
All automated evidence is exported as `.xlsx` files with:
- **Professional Styling**: Corporate green header (#2D4639)
- **Clear Headers**: Column headers with proper formatting
- **Data Validation**: Compliant fields (Yes/No)
- **Timestamps**: Last sync dates for auditability
- **Multi-Sheet Support**: Detailed data in additional sheets (e.g., NSG rules)

### Example File Structure

**M365 Users Export:**
```
| Display Name | Email              | Job Title | Department | Office | Account Enabled | Is Admin |
|--------------|--------------------|-----------|------------|--------|-----------------|----------|
| John Smith   | john@cirque.com    | Manager   | IT         | HQ     | Yes             | No       |
```

**Network Security Groups Export:**
```
Sheet 1: NSG Summary
| NSG Name      | Resource Group | Location | Security Rules | Associated Subnets |
|---------------|----------------|----------|----------------|-------------------|
| production-nsg| RG-Production  | eastus   | 15 rules       | subnet-1, subnet-2|

Sheet 2: Security Rules Detail
| NSG Name | Rule Name | Priority | Direction | Access | Protocol | Source | Destination | Ports |
|----------|-----------|----------|-----------|--------|----------|--------|-------------|-------|
```

## API Endpoints

### Generate Evidence Files
```
POST /api/soc2/generate-evidence-files
```
Generates Excel files for all automated evidence items. Returns statistics on success/errors.

**Response:**
```json
{
  "success": true,
  "message": "Generated 22 evidence files",
  "stats": {
    "success": 22,
    "errors": 0,
    "total": 22
  },
  "results": [...]
}
```

### Download Single Evidence File
```
GET /api/soc2/download-evidence/<evidence_id>
```
Downloads the Excel file for a specific evidence item.

### Download All Evidence as ZIP
```
GET /api/soc2/download-all-evidence
```
Creates and downloads a ZIP archive containing all generated evidence files.

## Database Schema

### StrikeGraphEvidence Table
New `file_path` column tracks generated files:

```python
class StrikeGraphEvidence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    evidence_name = db.Column(db.String(255))
    file_path = db.Column(db.String(500))  # Relative path from /static/
    automation_source = db.Column(db.String(100))  # M365/Intune, Azure, ISMS, Manual
    # ... other fields
```

## Workflow

### Typical Audit Workflow

1. **Run Sync Operations**
   - M365 Sync: Collect users, devices, software
   - Azure Security Sync: Collect NSGs, alerts, databases, etc.

2. **Generate Evidence Files**
   - Click "Generate Evidence Files"
   - System creates Excel exports from database snapshots
   - Files stored in organized directory structure

3. **Review Evidence**
   - Browse StrikeGraph Evidence page
   - Download individual files for review
   - Verify data completeness and accuracy

4. **Submit to StrikeGraph**
   - Download all files as ZIP
   - Upload to StrikeGraph evidence repository
   - Map files to SOC2 controls in StrikeGraph

5. **Regular Updates**
   - Re-run syncs monthly/quarterly
   - Regenerate evidence files
   - Submit updated evidence to StrikeGraph

## Automation Status

Current automated controls (12 of 58):
- Administrator Access
- Antivirus
- Asset Inventory
- Automatic Patching
- Configuration Standards
- Disk Encryption
- Encryption at Rest
- Encryption in Transit
- Firewall Rules
- Intrusion Detection
- Monitoring Infrastructure
- Password Requirements

Total automated evidence: 22 of 96 items (22.9%)
- M365/Intune: 10 items
- Azure: 12 items
- ISMS: 9 items (policy documents)
- Manual: 65 items (require manual upload)

## Technical Details

### EvidenceFileService Class
Located in `/var/www/tracker/evidence_file_service.py`

**Key Methods:**
- `generate_m365_users_file()` - Export M365 user list
- `generate_admin_users_file()` - Export admin access list
- `generate_intune_devices_file()` - Export device inventory
- `generate_azure_nsg_file()` - Export network security groups
- `generate_azure_databases_file()` - Export SQL database configs
- `generate_all_automated_evidence_files()` - Batch generate all files

### File Naming Convention
```
{Evidence_Name}_{YYYYMMDD}.xlsx
```
Examples:
- `Application_User_List_20260109.xlsx`
- `Administrator_Access_to_Application_20260109.xlsx`
- `Firewall_Rules_20260109.xlsx`

### Storage Requirements
- Average file size: 5-15 KB per evidence file
- Total storage for all evidence: ~300 KB
- ZIP archive: ~150 KB (compressed)

## Troubleshooting

### No Files Generated
- Ensure M365 sync has run successfully
- Check that automation source is set (M365/Intune or Azure)
- Verify database has current records (is_current=True)

### Missing Download Button
- Click "Generate Evidence Files" first
- Check file permissions in `/var/www/tracker/static/evidence/`
- Review application logs for generation errors

### Empty Excel Files
- Verify sync collected data (check dashboard statistics)
- Ensure database records are marked as current
- Check that device/user records exist

### File Not Found Error
- Regenerate evidence files
- Check disk space in `/var/www/tracker/static/`
- Verify file permissions (should be owned by webuser)

## Future Enhancements

Potential additions:
- PDF report generation for formal audit submissions
- Scheduled automatic file generation after syncs
- Email notification when evidence is ready
- Direct integration with StrikeGraph API for automated uploads
- Evidence versioning and change tracking
- Custom evidence templates for manual items
- Evidence expiration reminders and auto-regeneration

## Related Documentation
- [SOC2 Dashboard](ADVANCED_FEATURES.md) - SOC2 compliance features
- [M365 Integration](SETUP_COMPLETE.md) - Microsoft 365 setup
- [Azure Security](azure_security_service.py) - Azure integration
- [StrikeGraph Evidence](load_strikegraph_evidence.py) - Evidence mapping
