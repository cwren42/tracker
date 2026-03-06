# SOC2 Data Retention and Deletion Policy

**Document ID:** SOC2-POL-DR-001  
**Owner:** IT/Security Lead  
**Effective Date:** 2026-03-02  
**Review Frequency:** Annual

## 1. Purpose
Define retention periods and secure disposal requirements for data and records used in operations and SOC 2 evidence.

## 2. Scope
Applies to production systems, endpoints, cloud storage, backup repositories, logs, and shared collaboration platforms.

## 3. Retention Standards
| Data Category | Minimum Retention | Disposal Method |
|---|---|---|
| Security logs | 12 months | Secure deletion from SIEM/storage |
| Access logs | 12 months | Secure deletion |
| Change records | 12 months | Secure deletion |
| Incident records | 24 months | Secure deletion after closure period |
| Vendor due diligence records | 24 months | Secure deletion/archival purge |
| Policy/procedure approvals | 24 months | Archived then secure deletion |
| Backup snapshots | 35-90 days (per system tier) | Cryptographic erase or provider-secure delete |

## 4. Deletion Requirements
- Deletion must be irreversible and logged.
- Restricted data requires confirmation of purge completion.
- Disposal events must include who, what, when, and method.

## 5. Legal Holds
If legal/regulatory hold applies, deletion is suspended until hold release is documented.

## 6. Roles
- **Data Owner:** Approves category and retention rule.
- **IT/Security:** Executes and validates secure deletion.
- **Legal/Compliance:** Issues and releases legal holds.

## 7. Evidence for SOC 2
- Retention schedule
- Deletion run logs
- Backup lifecycle settings
- Legal hold records
