# Quick Start: Evidence File Generation

## Generate All Evidence Files

1. **Go to StrikeGraph Evidence Page**
   ```
   https://tracker.corp.cirque.com/soc2/strikegraph
   ```

2. **Click "Generate Evidence Files" Button**
   - Green button at top of page
   - Generates ~22 automated evidence files
   - Takes 5-10 seconds

3. **Download Options**
   - **Individual Files**: Click "Download" button next to each evidence item
   - **All Files as ZIP**: Click "Download All (ZIP)" button

## Evidence Files Created

### M365/Intune Evidence (10 files)
- Administrator Access Lists (4 files)
- User Lists (5 files)
- Asset Inventory (1 file)

### Azure Security Evidence (12 files)
- Network Security Groups (NSG/Firewall rules)
- Security Alerts (Defender for Cloud)
- SQL Databases (encryption config)
- Storage Accounts (encryption settings)
- Virtual Machines (disk encryption)
- Security Assessments (vulnerability scans)
- Monitor Alerts (monitoring config)
- Network Topology (network diagram)

## File Locations

Generated files are stored in:
```
/var/www/tracker/static/evidence/
├── m365/          - M365 & Intune evidence
│   ├── Administrator_Access_to_Application_20260109.xlsx
│   ├── Application_User_List_20260109.xlsx
│   ├── Workstation_Asset_Inventory_20260109.xlsx
│   └── ... (7 more files)
├── azure/         - Azure Security evidence
│   ├── Firewall_Rules_20260109.xlsx
│   ├── SQL_Server_Database_Encryption_20260109.xlsx
│   └── ... (10 more files)
├── isms/          - Policy documents
└── manual/        - Manual uploads
```

## Uploading to StrikeGraph

1. Download all evidence as ZIP
2. Extract ZIP file
3. Log into StrikeGraph
4. Navigate to Evidence Repository
5. Upload each file to corresponding evidence item
6. Map evidence to SOC2 controls

## Regenerating Files

To update evidence with latest data:
1. Run M365 Sync (SOC2 Dashboard → "M365 Sync")
2. Run Azure Security Sync (SOC2 Dashboard → "Azure Security Sync")
3. Click "Generate Evidence Files" again
4. Files will be overwritten with current date

## File Format

All files are `.xlsx` (Excel) format with:
- Professional styling (green headers)
- Clear column names
- Timestamp data for audit trail
- Multiple sheets for detailed data

## Tips

- Generate files monthly or before audit reviews
- Download ZIP for easy batch upload
- Check "Last Evidence" dates on dashboard before generating
- Keep previous versions for change tracking
- Review files before submitting to auditors

## Support

For issues:
1. Check service is running: `sudo systemctl status tracker.service`
2. Check logs: `sudo journalctl -u tracker.service -f`
3. Verify evidence directory permissions
4. Review [EVIDENCE_FILES.md](EVIDENCE_FILES.md) for details
