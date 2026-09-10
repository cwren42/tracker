# Strike Graph Controls & Risks Import Instructions

## Overview
This guide walks you through uploading 58 controls and 38 risks to Strike Graph via the web UI. All data is pre-formatted and validated for optimal import.

---

## Pre-Flight Checklist
- [ ] Strike Graph account access confirmed
- [ ] SOC2-StrikeGraph-Controls-Import.csv downloaded to local machine
- [ ] SOC2-StrikeGraph-Risks-Import.csv downloaded to local machine
- [ ] Browser: Chrome, Firefox, or Edge (latest version)
- [ ] Time allocated: ~30-40 minutes for controls + risks

---

## Part 1: Import Controls (58 total)

### Step 1: Open Strike Graph platform
1. Navigate to your Strike Graph instance URL
2. Log in with your organization account credentials
3. Verify you're on the **Security & Compliance** dashboard

### Step 2: Navigate to Controls section
1. Click **Controls** in the left navigation menu
2. Click **Import Controls** or **Upload CSV** button (exact label varies by version)
3. You should see an upload dialog

### Step 3: Upload Controls CSV
1. Click **Choose File** or **Select File**
2. Navigate to: `SOC2-StrikeGraph-Controls-Import.csv`
3. Click **Open** to select the file
4. **Verify preview:**
   - Expected columns: Control ID, Control Name, TSC Mapping, Domain, Owner, Frequency, Progress, Evidence Requirements
   - Expected rows: 58 rows total (header + 58 controls)
   - Sample controls visible: "CC-001 Acceptable Use Policy", "CC-058 Automatic Patching"

### Step 4: Configure import mapping
In the import dialog, verify these column mappings (should auto-detect):
- **Control ID** → Strike Graph Control Identifier
- **Control Name** → Control Name / Title
- **TSC Mapping** → Trust Service Criteria / TSC Reference
- **Domain** → Category / Domain (e.g., Governance, Operations, Access)
- **Owner** → Assigned Owner / Responsible Person
- **Frequency** → Execution Frequency (e.g., Annually, Continuous)
- **Progress** → Control Status (Not In Place / In Place / Partially In Place)
- **Evidence Requirements** → Description / Implementation Guidance

**If mappings don't auto-detect:**
1. Click **Manual Mapping** or **Map Columns**
2. Drag/drop or select corresponding Strike Graph fields for each column
3. Click **Save Mapping** (if option provided)

### Step 5: Import and validate
1. Click **Import** or **Upload** button
2. Wait for processing (should show progress bar)
3. When complete, you'll see a success message: "58 controls imported successfully"
4. Review any **import warnings or errors** (unlikely if CSV is well-formed):
   - If errors appear, click **Download Error Report** to diagnose
   - Common issues: Missing owner email, invalid TSC code (easily fixable)

### Step 6: Verification in Strike Graph
1. Click **Controls List** or **All Controls**
2. Verify you can see all 58 controls in the table
3. Spot-check a few controls:
   - Click "CC-001 Acceptable Use Policy" → Verify owner = brenda.milian@cirque.com, TSC = CC.1.1
   - Click "CC-020 Employee Performance" → Verify domain = Governance, frequency = Annually
   - Click "CC-058 Automatic Patching" → Verify status = In Place, owner = chris.wren@cirque.com

**If spot-checks pass, Controls import is complete!** ✅

---

## Part 2: Import Risks (38 total)

### Step 1: Navigate to Risks section
1. Click **Risks** in the left navigation menu
2. Click **Import Risks** or **Upload CSV** (exact label varies by version)
3. Click **Choose File**

### Step 2: Upload Risks CSV
1. Navigate to: `SOC2-StrikeGraph-Risks-Import.csv`
2. Click **Open** to select
3. **Verify preview:**
   - Expected columns: Risk ID, Risk Name, Category, Impact, Likelihood, Combined Score, Owner, Status
   - Expected rows: 38 risks total
   - Sample risks: "R-001 Acceptable Use of Company Assets", "R-038 Vulnerability Management"

### Step 3: Configure import mapping
Verify these column mappings:
- **Risk ID** → Risk Identifier
- **Risk Name** → Risk Title / Description
- **Category** → Risk Category (People, Technical, Legal, Physical, etc.)
- **Impact** → Impact Level (Low / Medium / High)
- **Likelihood** → Likelihood / Probability (Low / Medium / High)
- **Combined Score** → Risk Score / Rating (Low / Medium / High)
- **Owner** → Risk Owner / Responsible Party
- **Status** → Risk Status (Active / Out of Scope / Mitigated)

### Step 4: Import and validate
1. Click **Import** button
2. Wait for processing
3. Confirm success: "38 risks imported successfully"
4. Check for **import warnings** (usually none if CSV is well-formed)

### Step 5: Verification in Strike Graph
1. Click **Risks List** or **All Risks**
2. Spot-check several risks:
   - Click "R-001 Acceptable Use of Company Assets" → Impact = Medium, Likelihood = Medium, Score = Medium, Status = Active
   - Click "R-003 Availability" → Impact = Low, Score = Low, Status = Out of Scope (deactivated)
   - Click "R-011 Data Breach" → Impact = High, Score = High, owner = chris.wren@cirque.com, Status = Active
   - Click "R-037 Vendor Management" → Impact = High, Score = High, owner = chris.wren@cirque.com, Status = Active

**If spot-checks pass, Risks import is complete!** ✅

---

## Part 3: Link Controls to Risks (Optional but Recommended)

Some Strike Graph versions allow control-to-risk mapping. If available:

1. Open a **Risk** details page (e.g., "R-001 Acceptable Use of Company Assets")
2. Look for a **Related Controls** or **Mitigating Controls** section
3. Link relevant controls:
   - R-001 → CC-001 (Acceptable Use Policy), CC-041 (Security Training)
   - R-003 (Availability) → CC-004 (Business Continuity), CC-032 (Monitoring Infrastructure)
   - R-013 (Encryption) → CC-019 (Disk Encryption), CC-022 (Encryption at Rest), CC-023 (Encryption in Transit)

**This step is optional and can be deferred.** Proceed to sign-off if time is constrained.

---

## Troubleshooting

### Issue: "File upload failed" or "Invalid CSV format"
**Solution:** 
- Open the CSV in Notepad and verify no special characters or line breaks are corrupted
- Re-download the CSV file and try again
- Contact support if error persists

### Issue: "Owner email not recognized" during import
**Solution:**
- Verify email addresses match your organization directory (e.g., chris.wren@cirque.com)
- If needed, update emails in the CSV and re-upload

### Issue: "TSC Code not found" or "Invalid domain"
**Solution:**
- Strike Graph may use different naming for domains (e.g., "Risk Assessment" vs "Risk")
- Check Strike Graph documentation for your version's allowed values
- Edit CSV if needed and re-upload

### Issue: Partial import (only some rows imported)
**Solution:**
- Download the error report from Strike Graph
- Identify rows with errors (usually data format issues)
- Fix in CSV and re-upload only the error rows, or delete and re-import the full batch

---

## Post-Import: Update Strike Graph Upload Tracker

After both imports complete successfully, update the tracking file:

**File:** `SOC2-StrikeGraph-Upload-Tracker.md`

```markdown
## Controls Upload Status
✅ All 58 controls uploaded to Strike Graph on [DATE] by [NAME]

## Risks Upload Status
✅ All 38 risks uploaded to Strike Graph on [DATE] by [NAME]

## Sign-off
- Upload completed by: [Your Name]
- Date: [Today's Date]
- All 58 controls synced: Yes ✅
- All 38 risks synced: Yes ✅
- Notes: All spot-checks passed. Controls and risks are now in Strike Graph.
```

---

## Next Steps

1. **Confirm with stakeholders:** Send email to Chris Wren + Executive Committee with subject:
   > "✅ SOC 2 Controls & Risks uploaded to Strike Graph"
   > - 58 controls imported and verified
   > - 38 risks imported and verified
   > - All owners and TSC mappings confirmed
   > - RACI governance now active in platform

2. **Begin P0-01 tasks:** Formalize scope in Strike Graph governance section

3. **Schedule control evidence collection:** Start working through P1-series tasks to gather evidence per control

---

## Support Contact
If you encounter issues during import:
- Strike Graph Support: contact your account manager
- Internal: chris.wren@cirque.com (IT/Security lead)

---

**Document created:** 2026-03-02  
**Last updated:** 2026-03-02  
**Version:** 1.0
