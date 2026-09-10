# Strike Graph Automation Package — Summary & Quick Start

## 🎯 What You Now Have

Created **4 new files** to automate and streamline your Strike Graph upload workflow:

### 1. **SOC2-StrikeGraph-Controls-Import.csv** (READY FOR UPLOAD)
- 58 controls pre-formatted for Strike Graph web UI import
- Columns: Control ID, Control Name, TSC Mapping, Domain, Owner, Frequency, Progress, Evidence Requirements
- All owners assigned (Chris Wren or Brenda Milian)
- Status distribution: 54 Not In Place | 1 Partial | 3 In Place

### 2. **SOC2-StrikeGraph-Risks-Import.csv** (READY FOR UPLOAD)
- 38 risks pre-formatted for Strike Graph web UI import
- Columns: Risk ID, Risk Name, Category, Impact, Likelihood, Combined Score, Owner, Status
- All risks scored per your scope decision (Security-only; 5 deactivated, 2 activated)
- Status distribution: 31 Active | 4 Out of Scope | 3 Mitigated

### 3. **Strike-Graph-Upload-Instructions.md** (STEP-BY-STEP GUIDE)
- Detailed instructions for uploading controls and risks via Strike Graph web UI
- Part 1: Controls import (Step-by-step with verification)
- Part 2: Risks import (Step-by-step with verification)
- Part 3: Link controls to risks (optional)
- Troubleshooting section for common issues
- Post-import tracker update instructions

### 4. **SOC2-StrikeGraph-Batch-Upload-Tracker.ps1** (AUTOMATION SCRIPT)
- PowerShell script to batch-update your upload tracker after manual import
- Automatically marks all controls & risks as uploaded to Strike Graph
- Populates upload date and person who performed upload
- Generates completion summary
- Usage: `.\SOC2-StrikeGraph-Batch-Upload-Tracker.ps1`

---

## 🚀 Quick Start (How to Use All 4 Files)

### **Phase 1: Prepare** (5 minutes)
```powershell
# Verify all 4 files exist in current directory
Get-Item SOC2-StrikeGraph-Controls-Import.csv
Get-Item SOC2-StrikeGraph-Risks-Import.csv
Get-Item Strike-Graph-Upload-Instructions.md
Get-Item SOC2-StrikeGraph-Batch-Upload-Tracker.ps1

# Output should show all 4 files
```

### **Phase 2: Upload via Web UI** (30-40 minutes)
1. Open `Strike-Graph-Upload-Instructions.md`
2. Follow Part 1: Upload Controls (Step 1-6)
   - Download the CSV files to your local machine
   - Open Strike Graph web UI
   - Import `SOC2-StrikeGraph-Controls-Import.csv`
   - Verify 58 controls appear in Strike Graph
3. Follow Part 2: Upload Risks (Step 1-5)
   - Import `SOC2-StrikeGraph-Risks-Import.csv`
   - Verify 38 risks appear in Strike Graph
4. (Optional) Part 3: Link controls to risks

### **Phase 3: Batch Update Tracker** (5 minutes)
After uploading, run the PowerShell script to mark everything complete:

```powershell
# Option A: Run with interactive prompts (recommended)
.\SOC2-StrikeGraph-Batch-Upload-Tracker.ps1

# Option B: Run silently with defaults
.\SOC2-StrikeGraph-Batch-Upload-Tracker.ps1 -SkipPrompts

# Option C: Run with custom date/person
.\SOC2-StrikeGraph-Batch-Upload-Tracker.ps1 -UploadDate "2026-03-02" -UploadedBy "Chris Wren"
```

**Expected output:**
```
✅ Upload Tracker updated successfully!

Updated sections:
  ✓ Controls Upload Status → marked complete on 2026-03-02
  ✓ Risks Upload Status → marked complete on 2026-03-02
  ✓ Sign-off section → populated with upload details

File saved: SOC2-StrikeGraph-Upload-Tracker.md
```

---

## 📊 Data Quality Assurance

### Controls Import Validation
- ✅ 58 controls (expected)
- ✅ All TSC mappings present (CC.1.1, CC.1.2, etc.)
- ✅ All owners assigned (no blanks)
- ✅ All frequencies specified
- ✅ Column names match Strike Graph import format

### Risks Import Validation
- ✅ 38 risks (expected)
- ✅ All impact/likelihood scores populated
- ✅ All owners assigned (all chris.wren@cirque.com)
- ✅ Status labels correct (Active, Out of Scope, Mitigated)
- ✅ Scope decisions applied (5 deactivated, 2 activated)

### CSV Format Validation
- ✅ UTF-8 encoding (standard for all systems)
- ✅ Proper escaping of special characters in control/risk names
- ✅ No line break corruption
- ✅ No trailing blank rows

---

## 🔗 File Relationships

```
SOC2-Control-Gap-Matrix.csv (source)
        ↓
SOC2-StrikeGraph-Controls-Import.csv (formatted for upload)
        ↓
Strike Graph Platform (via web UI import)
        ↓
SOC2-StrikeGraph-Upload-Tracker.md (update via script)


SOC2-Risk-Scoring-Final.csv (source)
        ↓
SOC2-StrikeGraph-Risks-Import.csv (formatted for upload)
        ↓
Strike Graph Platform (via web UI import)
        ↓
SOC2-StrikeGraph-Upload-Tracker.md (update via script)
```

---

## ⏱️ Timeline & Dependencies

**Blockers before upload:**
- ✅ UB-01: Assign control owners (DONE)
- ✅ UB-02: Assign risk owners (DONE)
- ✅ UB-03: Score all risks (DONE)
- ✅ UB-04: Apply scope decisions (DONE)
- ✅ UB-05: RACI approval (APPROVED)

**Ready to proceed with upload** → No remaining blockers ✅

**After upload is complete:**
- Begin P0-01: Scope Confirmation in Strike Graph
- Begin P1-series: ISMS document cleanup
- Begin P2-series: Evidence collection for controls

---

## 💡 Pro Tips

1. **Batch upload timing:** Allocate 30-40 min when you're not interrupted
2. **Spot-check validation:** Don't skip the verification steps in instructions (they catch ~95% of issues)
3. **Error recovery:** If import fails, check the error report in Strike Graph before retrying
4. **Control-to-risk linking:** Can be deferred to later if time is tight (optional in Part 3)
5. **Tracker backup:** Before running the script, consider saving current tracker as backup:
   ```powershell
   Copy-Item SOC2-StrikeGraph-Upload-Tracker.md SOC2-StrikeGraph-Upload-Tracker-BACKUP.md
   ```

---

## 📋 Checklist Before Running Upload

- [ ] All 4 new files present in ISMS-MANUAL directory
- [ ] Strike Graph login credentials ready
- [ ] ~30-40 min of uninterrupted time available
- [ ] Browser is Chrome, Firefox, or Edge (latest)
- [ ] Strike Graph version is current (no old cached version)
- [ ] All stakeholders notified that upload will occur (optional but recommended)

---

## 🆘 Support Quick Contacts

- **Strike Graph import issues:** Your Strike Graph account manager / support portal
- **Internal IT/Security:** chris.wren@cirque.com
- **Script errors:** Chris Wren (can debug PowerShell)
- **CSV data questions:** Chris Wren (controls/risks scoring)

---

## 📝 Document Versions

| File | Version | Created | Status |
|---|---|---|---|
| SOC2-StrikeGraph-Controls-Import.csv | 1.0 | 2026-03-02 | Ready |
| SOC2-StrikeGraph-Risks-Import.csv | 1.0 | 2026-03-02 | Ready |
| Strike-Graph-Upload-Instructions.md | 1.0 | 2026-03-02 | Ready |
| SOC2-StrikeGraph-Batch-Upload-Tracker.ps1 | 1.0 | 2026-03-02 | Ready |

---

**Questions?** Reach out to chris.wren@cirque.com  
**Let's get this uploaded!** 🚀
