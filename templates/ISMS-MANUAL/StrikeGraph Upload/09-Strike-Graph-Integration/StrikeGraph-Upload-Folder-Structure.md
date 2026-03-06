# StrikeGraph Upload - Folder Structure & Organization

**Purpose:** Central repository for all SOC 2 evidence files destined for Strike Graph upload via integration manager.

**Location:** OneDrive → ISMS-MANUAL → StrikeGraph Upload folder  
**Path:** `C:\Users\cjwren\OneDrive - Cirque Corporation\Documents\ISMS-MANUAL\StrikeGraph Upload`

---

## Recommended Folder Structure

```
ISMS-MANUAL (OneDrive Folder Root)
│
├── StrikeGraph Upload/
│   │
│   ├── 01-Governance/
│   │   ├── P0-01-Scope-Confirmation.md
│   │   ├── P0-02-Audit-Timeline.md
│   │   ├── P0-03-RACI-Matrix.md (when ready)
│   │   ├── P0-05-Evidence-Repository-Standard.md (when ready)
│   │   ├── SOC2-UB05-RACI-Draft.md
│   │   └── Board-Approval-Minutes.docx (when available)
│   │
│   ├── 02-Policies/
│   │   ├── Information-Security-Policy.docx
│   │   ├── Data-Classification-Policy.docx
│   │   ├── Data-Retention-Deletion-Policy.docx
│   │   ├── Change-Management-Policy.docx
│   │   ├── Incident-Response-Plan.docx
│   │   ├── Business-Continuity-Plan.docx
│   │   ├── Vendor-Management-Policy.docx
│   │   ├── User-Access-Policy.docx
│   │   ├── Privileged-Access-Management-Procedures.docx
│   │   ├── Code-of-Conduct.docx
│   │   ├── Acceptable-Use-Policy.docx
│   │   └── [Additional policies as finalized]
│   │
│   ├── 03-System-Documentation/
│   │   ├── SOC2-System-Description.md
│   │   ├── Network-Diagram.png
│   │   ├── Data-Flow-Diagram.png
│   │   ├── System-Architecture-Diagram.visio
│   │   ├── Asset-Inventory.xlsx
│   │   └── Configuration-Standards.docx
│   │
│   ├── 04-Control-Procedures/
│   │   ├── CC-001-Acceptable-Use-Procedure.docx
│   │   ├── CC-002-Background-Check-Procedure.docx
│   │   ├── CC-003-Board-Oversight-Procedure.docx
│   │   ├── [... all 58 control procedures ...]
│   │   └── CC-058-Automatic-Patching-Procedure.docx
│   │
│   ├── 05-Risk-Management/
│   │   ├── SOC2-Risk-Register-Live.md
│   │   ├── Risk-Assessment-Methodology.docx
│   │   ├── Risk-Assessment-Policy.docx
│   │   └── Risk-Action-Plans.xlsx
│   │
│   ├── 06-HR-Organizational/
│   │   ├── Organizational-Chart.png
│   │   ├── Job-Descriptions-Master.docx
│   │   ├── Summary-of-Applicability.csv
│   │   ├── Training-Records-2025-2026.xlsx
│   │   └── Background-Check-Procedures.docx
│   │
│   ├── 07-Operational-Evidence/
│   │   ├── Access-Control-Logs/
│   │   │   ├── User-Provisioning-Requests-Mar2026.xlsx
│   │   │   ├── User-Termination-Records-2025-2026.xlsx
│   │   │   ├── Access-Review-Evidence-Q1-2026.xlsx
│   │   │   └── VPN-Access-Logs-Sample.txt
│   │   │
│   │   ├── Change-Management-Evidence/
│   │   │   ├── Change-Requests-Log-Q1-2026.xlsx
│   │   │   ├── Change-Approval-Records-Sample.pdf
│   │   │   ├── Emergency-Change-Log.xlsx
│   │   │   └── Post-Implementation-Reviews.xlsx
│   │   │
│   │   ├── Incident-Response-Evidence/
│   │   │   ├── Incident-Log-2025-2026.xlsx
│   │   │   ├── Incident-Investigation-Reports-Sample.pdf
│   │   │   ├── Incident-Response-Testing-Results-2025.docx
│   │   │   └── Logs-Firewall-Sample-Mar2026.txt
│   │   │
│   │   ├── Security-Operations-Evidence/
│   │   │   ├── Antivirus-Incident-Log-Q1-2026.xlsx
│   │   │   ├── Vulnerability-Scan-Results-Mar2026.pdf
│   │   │   ├── Patch-Deployment-Log-Q1-2026.xlsx
│   │   │   └── Intrusion-Detection-Alerts-Sample.txt
│   │   │
│   │   ├── Monitoring-Logs/
│   │   │   ├── Firewall-Rules-Current.txt
│   │   │   ├── Network-Access-Logs-Sample-Mar2026.txt
│   │   │   ├── Server-Event-Logs-Sample.txt
│   │   │   └── Database-Audit-Logs-Sample.txt
│   │   │
│   │   └── Vendor-Management-Evidence/
│   │       ├── Vendor-Master-List.xlsx
│   │       ├── Vendor-Contracts-Sample.pdf
│   │       ├── Third-Party-SOC2-Attestations.pdf
│   │       └── Vendor-Risk-Assessment-Records.xlsx
│   │
│   ├── 08-Control-Mappings/
│   │   ├── SOC2-Statement-of-Applicability.csv
│   │   ├── Control-to-Policy-Mapping.xlsx
│   │   ├── Control-Gap-Matrix.csv
│   │   └── Control-Evidence-Matrix.xlsx
│   │
│   ├── 09-Strike-Graph-Integration/
│   │   ├── SOC2-StrikeGraph-Controls-Import.csv
│   │   ├── SOC2-StrikeGraph-Risks-Import.csv
│   │   ├── StrikeGraph-Upload-Manifest.xlsx (THIS FILE - tracks what's uploaded)
│   │   └── Integration-Manager-Setup-Notes.md
│   │
│   └── 10-Archive/
│       ├── Previous-Versions/
│       ├── Draft-Documents/
│       └── superseded-Controls/
```

---

## File Naming Conventions

**Standard Format:** `[DocumentType]-[Description]-[DateOrVersion].ext`

### Examples:

**Policies:**
- `Information-Security-Policy-v1.0-2026-03-02.docx`
- `Data-Classification-Policy-FINAL-2026-03-05.docx`

**Procedures:**
- `CC-001-Acceptable-Use-Procedure-v1.0.docx`
- `CC-022-Encryption-At-Rest-Implementation-Guide-v2.1.docx`

**Evidence:**
- `Access-Review-Evidence-Q1-2026.xlsx`
- `Incident-Log-2025-2026-FINAL.xlsx`
- `Firewall-Rules-2026-03-02.txt`

**Control Mappings:**
- `SOC2-Statement-of-Applicability-v1.0-2026-03-02.csv`
- `Control-Gap-Matrix-2026-03-02.csv`

**Import Files (for Strike Graph integration):**
- `SOC2-StrikeGraph-Controls-Import-2026-03-02.csv`
- `SOC2-StrikeGraph-Risks-Import-2026-03-02.csv`

---

## OneDrive Sharing & Access

### Recommended Access Model

| Role | Sharing Level | Access Method | View Folders | Edit/Upload | Delete |
|---|---|---|---|---|---|
| **Chris Wren (IT/Security)** | Owner | Direct access (folder owner) | All | Yes | Yes |
| **Brenda Milian (HR)** | Editor | Shared link or direct share | 01-Governance, 06-HR-Org | Yes (HR folders) | Own files |
| **Strike Graph Integration Manager** | Viewer | Shared read-only link | All | No (download only) | No |
| **Executive Committee** | Viewer | Shared read-only link | All | No | No |
| **Department Managers** | Editor (optional) | Shared editable link | 02-Policies, 04-Procedures | Yes (upload) | No |
| **All Employees** | Viewer | Shared link to policies | 01-Governance, 02-Policies only | No | No |

---

## OneDrive Folder Setup Steps

1. **Create Folder Structure**
   - In OneDrive ISMS-MANUAL folder, create `/StrikeGraph Upload/` root folder
   - Create 10 numbered subfolders (01-Governance through 10-Archive) as shown above
   - Copy this document into `/09-Strike-Graph-Integration/` for reference

2. **Set Initial Sharing**
   - Chris Wren: already has access (owns the folder)
   - Brenda Milian: Share `/01-Governance/` and `/06-HR-Organizational/` with Editor permission
   - Strike Graph Integration Manager: Share `/StrikeGraph Upload/` root with Viewer (read-only) permission
   - Keep permanent shared links for recurring access

3. **Enable Version History**
   - OneDrive automatically keeps 93 days of version history
   - Document current version of each policy/procedure before updates
   - For major updates, move old version to `/10-Archive/Previous-Versions/`

4. **Configure Monitor/Alerts (Optional)**
   - Set up email notification for when files in `/StrikeGraph Upload/` are modified
   - Notify Chris Wren daily during batch upload windows (Mar 5-21)
   - Integration manager bookmarks the OneDrive link for easy daily access

---

## Upload Checklist & Sign-Off

**Before uploading each batch of files to Strike Graph, complete this checklist:**

### Batch 1: Governance Foundation (Due: March 5, 2026)
- [ ] P0-01 Scope Confirmation (SIGNED by Exec Committee)
- [ ] P0-02 Audit Timeline (SIGNED)
- [ ] UB-05 RACI Matrix (APPROVED)
- [ ] Organizational Chart (current)
- **Upload by:** March 6, 2026
- **Uploaded by:** _________________ **Date:** _________

### Batch 2: Core Policies (Due: March 12, 2026)
- [ ] Information Security Policy (FINAL)
- [ ] Data Classification Policy (FINAL)
- [ ] Change Management Policy (FINAL)
- [ ] Incident Response Plan (FINAL)
- [ ] Data Retention/Deletion Policy (FINAL)
- [ ] Business Continuity Plan (FINAL)
- **Upload by:** March 13, 2026
- **Uploaded by:** _________________ **Date:** _________

### Batch 3: System & Control Documentation (Due: March 15, 2026)
- [ ] System Description Document (FINAL)
- [ ] Network Diagram (current)
- [ ] Data Flow Diagram (current)
- [ ] Asset Inventory (current)
- [ ] SOC2 Statement of Applicability SoA (CSV)
- [ ] Control Gap Matrix (CSV)
- [ ] All 58 control procedures (or 10+ priority controls + rest by March 20)
- **Upload by:** March 16, 2026
- **Uploaded by:** _________________ **Date:** _________

### Batch 4: Risk & HR Documentation (Due: March 15, 2026)
- [ ] Risk Register (LIVE, all 38 risks)
- [ ] Risk Assessment Methodology (FINAL)
- [ ] Vendor Master List (current)
- [ ] Job Descriptions (all key roles)
- [ ] Training Records (2025-2026)
- **Upload by:** March 16, 2026
- **Uploaded by:** _________________ **Date:** _________

### Batch 5: Operational Evidence (Due: March 20, 2026)
- [ ] User Provisioning Logs (Q1 2026)
- [ ] Termination Records (2025-2026)
- [ ] Access Review Evidence (Q1 2026)
- [ ] Change Request Logs (sample, Q1 2026)
- [ ] Incident Response Testing Results (2025)
- [ ] Vulnerability Scan Results (Q1 2026)
- [ ] Antivirus/Security Logs (sample, Mar 2026)
- [ ] Vendor SOC 2 Attestations (if available)
- **Upload by:** March 21, 2026
- **Uploaded by:** _________________ **Date:** _________

---

## Strike Graph Integration Manager Setup

**Once folder is populated, coordinate with Strike Graph integration manager:**

1. **Share OneDrive read-only link** to `StrikeGraph Upload` folder
   - Chris Wren sends permanent shared link with Viewer (read-only) permission
   - Integration manager bookmarks for daily monitoring access

2. **Integration Manager configures daily monitoring** of OneDrive folder
   - Check folder daily at 10 AM for new/updated files
   - Download files from subfolders
   - Track uploads in local spreadsheet (see StrikeGraph-Upload-Manifest)

3. **Map folder structure** to Strike Graph sections:
   - `/01-Governance/` → Strike Graph "Governance" section
   - `/02-Policies/` → Strike Graph "Policies" section
   - `/03-System-Documentation/` → Strike Graph "System Design" section
   - `/04-Control-Procedures/` → Strike Graph "Control Details" section
   - `/05-Risk-Management/` → Strike Graph "Risks" section
   - `/06-HR-Organizational/` → Strike Graph "HR/Org" section
   - `/07-Operational-Evidence/` → Strike Graph "Evidence" section
   - `/08-Control-Mappings/` → Strike Graph "Mappings" section

4. **Daily OneDrive monitoring** (manual or script-based)
   - Check for new files each morning
   - No duplicate files in Strike Graph (check timestamp before uploading newer version)
   - Log each upload in tracking spreadsheet

5. **Weekly confirmation report** to Chris Wren
   - Files found in OneDrive this week
   - Files uploaded to Strike Graph
   - Any access issues or errors
   - Strike Graph sync status ("On Track" / "Issues Found")

---

## Version Control & Archiving

**Active Documents (02-Policies, 04-Procedures, 07-Evidence):**
- Keep current version in main folder
- Move superseded versions to `/10-Archive/Previous-Versions/` with date suffix
- Naming: `[DocName]-v[OldVersion]-SUPERSEDED-2026-03-02.docx`

**Static/Reference Documents (01-Governance, 03-System-Documentation):**
- Update in place; version in filename
- Keep one prior version in archive for reference

**Evidence Logs (07-Operational-Evidence):**
- Accumulate monthly; don't overwrite
- Naming: `[LogType]-[MonthYear].xlsx` (e.g., Access-Review-Evidence-Mar2026.xlsx)
- Annual rollup: `[LogType]-ANNUAL-2026.xlsx`

---

## Quick Reference: What Goes Where

| Document Type | Target Folder | Upload Batch | Status Check |
|---|---|---|---|
| Governance approvals, scope, timeline | 01-Governance | Batch 1 | Priority |
| All policy documents | 02-Policies | Batch 2-3 | Priority |
| Architecture, diagrams, system info | 03-System-Documentation | Batch 3 | Priority |
| Control procedures (54 procedures) | 04-Control-Procedures | Batch 3-4 | Priority (10+ by Batch 3) |
| Risks, risk assessment docs | 05-Risk-Management | Batch 4 | Priority |
| HR records, org structure | 06-HR-Organizational | Batch 2-4 | Priority |
| Logs, incidents, access records | 07-Operational-Evidence | Batch 5 | Ongoing during fieldwork |
| CSV mappings, control matrices | 08-Control-Mappings | Batch 3 | Priority |
| Import CSVs, integration notes | 09-Strike-Graph-Integration | Batch 1 | Priority |

---

## Contact & Questions

- **SharePoint Site Owner:** [Name/Email]
- **StrikeGraph Integration Manager:** [Contact info from Strike Graph]
- **Chris Wren (Document Owner):** chris.wren@cirque.com
- **Upload Support:** Contact strike.graph-support@strikegraph.com

---

**Document Version:** 1.0  
**Created:** 2026-03-02  
**Last Updated:** 2026-03-02
