# EC-056-C - Administrator Access to Operating System

**Evidence ID:** EC-056-C  
**Control Name:** Administrator Access to Operating System  
**Control ID:** SG-056  
**TSC Criteria:** SOC2:2022.CC.6.2  
**Frequency:** Daily  
**Governing Document:** IS-CIRQ-PR-009-G- Privileged Access Management Procedure  
**Primary Repository Location:** 07-Operational-Evidence/Access-Control-Logs  
**Status:** Pending Collection

## Required Evidence Artifacts

This control requires the following specific evidence for **Operating System Administrator Access**:

1. **OS Admin Account Inventory** - List of all operating system administrator accounts including:
   - Domain Administrator accounts (Active Directory)
   - Local Administrator accounts on servers and workstations
   - Linux/Unix root or sudo access accounts
   - Cloud VM administrator access (Azure, AWS)
   - Account names and associated systems
   - Individuals authorized to use each account
   - Purpose/business justification
   - Last review date

2. **Quarterly Access Review (OS Admins)** - Most recent quarterly review showing:
   - Verification of continued need for OS admin access
   - Confirmation that admins use separate accounts (standard vs. privileged)
   - Review of Domain Admin group membership
   - Verification of local admin account usage
   - Identification of dormant admin accounts
   - Reviewer signature (IT Manager) and date

3. **MFA Enforcement for OS Admin Access** - Proof that MFA is enabled for:
   - Active Directory domain administrator accounts
   - Azure AD administrative roles
   - Server remote access (RDP with MFA)
   - VPN access for administrative tasks
   - Screenshots showing MFA requirements and enforcement

4. **Access Approval Sample** - Sample OS admin access request showing:
   - Formal request for OS-level administrative privileges
   - Business justification and systems requiring access
   - IT Manager approval
   - Time-limited access if applicable

5. **OS Admin Activity Logs** - Sample logs demonstrating:
   - Windows Event Logs (Security, System) showing admin logons
   - Active Directory audit logs showing privilege changes
   - Linux/Unix sudo logs or auth logs
   - Azure Activity Logs for VM admin actions
   - Evidence of log review and monitoring

## Evidence Summary
Provide the finalized evidence artifact(s) for this control here, including capture date, source system, and reviewer.

## Collection Details
- **Collected By:**
- **Collection Date:**
- **System/Source:** Active Directory, Windows Event Logs, Azure AD Audit Logs, Linux syslog
- **Reviewer:**
- **Review Date:**
- **Audit Period Coverage:** [Specify date range]

## Evidence Checklist
- [ ] OS admin account inventory (current)
- [ ] Domain Admin and local admin group membership lists
- [ ] Quarterly access review for OS admins (signed)
- [ ] MFA configuration for AD admin accounts and remote access
- [ ] Sample OS admin access request with approval
- [ ] OS admin activity log exports (sample period)
- [ ] Evidence of dedicated admin accounts (separate from standard accounts)
- [ ] Confirm timeframe is within audit period
- [ ] Confirm IT Manager approval is included

## Upload Instructions
1. Collect OS admin access evidence from Active Directory, servers, and cloud platforms
2. Package files with clear naming: `EC-056-C_OSAdminInventory_YYYY-MM-DD`, `EC-056-C_QuarterlyReview_Q#_YYYY`, etc.
3. Upload to StrikeGraph under "Administrator Access to Operating System" evidence
4. Link to IS-CIRQ-PR-009-G governing procedure

## Notes
- **Critical**: Demonstrate separation of duties - admins must have separate privileged accounts
- Procedure requires quarterly reviews - ensure latest review is within 90 days
- Include Domain Admin group membership as key evidence artifact
- Show MFA is enforced for all AD domain admin accounts (not optional)
- Verify local admin accounts are minimized and monitored
- Include both Windows and Linux/Unix OS admin evidence if applicable
