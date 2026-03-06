# EC-056-A - Administrator Access to Application

**Evidence ID:** EC-056-A  
**Control Name:** Administrator Access to Application  
**Control ID:** SG-056  
**TSC Criteria:** SOC2:2022.CC.6.2  
**Frequency:** Daily  
**Governing Document:** IS-CIRQ-PR-009-G- Privileged Access Management Procedure  
**Primary Repository Location:** 07-Operational-Evidence/Access-Control-Logs  
**Status:** Pending Collection

## Required Evidence Artifacts

This control requires the following specific evidence for **Application Administrator Access**:

1. **Application Admin Account Inventory** - List of all application administrator accounts including:
   - Application name (Omnify, Cadence, GitLab, Asana, Microsoft 365, QuickBooks, etc.)
   - Admin account names/IDs
   - Individuals authorized to use each account
   - Access level/role (Super Admin, Application Admin, etc.)
   - Purpose/business justification
   - Last review date

2. **Quarterly Access Review (Application Admins)** - Most recent quarterly review showing:
   - Verification of continued need for application admin access
   - Confirmation of least privilege principle
   - Review of application admin activity logs
   - Reviewer signature (IT Manager) and date

3. **MFA Enforcement for Applications** - Proof that MFA is enabled for:
   - Microsoft 365 Admin Center access
   - GitLab admin accounts
   - QuickBooks admin access
   - Other critical application admin portals
   - Screenshots or configuration exports showing MFA status

4. **Access Approval Sample** - Sample application admin access request showing:
   - Formal request for privileged application access
   - Business justification
   - IT Manager and/or Department Manager approval
   - Time-limited access duration if applicable

5. **Application Admin Activity Logs** - Sample logs demonstrating:
   - User, date/time, actions performed in applications
   - Configuration changes, user management actions
   - Access to sensitive application data/settings

## Evidence Summary
Provide the finalized evidence artifact(s) for this control here, including capture date, source system, and reviewer.

## Collection Details
- **Collected By:**
- **Collection Date:**
- **System/Source:** Microsoft 365, GitLab, Omnify, Cadence, Asana, QuickBooks admin audit logs
- **Reviewer:**
- **Review Date:**
- **Audit Period Coverage:** [Specify date range]

## Evidence Checklist
- [ ] Application admin account inventory (current)
- [ ] Quarterly access review for application admins (signed)
- [ ] MFA configuration screenshots for application admin accounts
- [ ] Sample application admin access request with approval
- [ ] Application admin activity log exports (sample period)
- [ ] Review findings and remediation documentation
- [ ] Confirm timeframe is within audit period
- [ ] Confirm IT Manager approval is included

## Upload Instructions
1. Collect application admin access evidence from all critical business applications
2. Package files with clear naming: `EC-056-A_AppAdminInventory_YYYY-MM-DD`, `EC-056-A_QuarterlyReview_Q#_YYYY`, etc.
3. Upload to StrikeGraph under "Administrator Access to Application" evidence
4. Link to IS-CIRQ-PR-009-G governing procedure

## Notes
- Focus on critical business applications that handle sensitive data
- Procedure requires quarterly reviews - ensure latest review is within 90 days
- Include all SaaS application admin accounts (Microsoft 365, GitLab, etc.)
- Demonstrate MFA enforcement across all application admin portals
