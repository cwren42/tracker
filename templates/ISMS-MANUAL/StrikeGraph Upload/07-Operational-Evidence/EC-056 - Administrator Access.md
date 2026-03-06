# EC-056 - Administrator Access

**Evidence ID:** EC-056  
**Control Name:** Administrator Access  
**Control ID:** SG-056  
**TSC Criteria:** SOC2:2022.CC.6.2  
**Frequency:** Daily  
**Governing Document:** IS-CIRQ-PR-009-G- Privileged Access Management Procedure  
**Primary Repository Location:** 07-Operational-Evidence/Access-Control-Logs  
**Source Catalog:** SOC2-Evidence-Catalog.md  
**Status:** Pending Collection

## Required Evidence Artifacts

This control requires the following specific evidence:

1. **Privileged Account Inventory** - Comprehensive list of all privileged accounts including:
   - Account name and type (Domain Admin, Database Admin, Application Admin, etc.)
   - Associated systems and applications
   - Individuals authorized to use each account
   - Purpose/business justification
   - Last review date

2. **Quarterly Access Review Documentation** - Most recent quarterly review showing:
   - Verification of continued need for each privileged account
   - Confirmation of least privilege principle alignment
   - Identification and disposition of dormant/unnecessary accounts
   - Review of privileged activity audit trails
   - Reviewer signature and date

3. **MFA Enforcement Evidence** - Proof that MFA is enabled for:
   - Active Directory domain administrator accounts
   - Critical cloud platforms (Azure, Microsoft 365 Admin Centers)
   - Network device administration
   - Screenshots or configuration exports showing MFA status

4. **Access Approval Documentation** - Sample access request showing:
   - Formal request for privileged access
   - Business justification
   - IT Manager approval
   - Time-limited (JIT) access duration if applicable

5. **Privileged Activity Logs** - Sample logs demonstrating:
   - User, date/time, action performed, system accessed
   - Centralized log management (if available)
   - Alert configurations for suspicious activity

## Evidence Summary
Provide the finalized evidence artifact(s) for this control here, including capture date, source system, and reviewer.

## Collection Details
- **Collected By:**
- **Collection Date:**
- **System/Source:** Active Directory, Azure AD, Network Device Logs, Ticketing System
- **Reviewer:**
- **Review Date:**
- **Audit Period Coverage:** [Specify date range]

## Evidence Checklist
- [ ] Privileged account inventory (current)
- [ ] Most recent quarterly access review (signed)
- [ ] MFA configuration screenshots for privileged accounts
- [ ] Sample access request with approval
- [ ] Privileged activity log exports (sample period)
- [ ] Review findings and remediation documentation
- [ ] Confirm timeframe is within audit period
- [ ] Confirm IT Manager approval is included

## Upload Instructions
1. Ensure all five evidence artifacts listed above are collected
2. Package files with clear naming: `EC-056_PrivilegedAccountInventory_YYYY-MM-DD`, `EC-056_QuarterlyReview_Q#_YYYY`, etc.
3. Upload to StrikeGraph under SG-056 control
4. Link to IS-CIRQ-PR-009-G governing procedure

## Notes
- Procedure requires quarterly reviews - ensure latest review is within 90 days
- Daily frequency indicates ongoing monitoring - provide representative log sample, not all daily logs
- Focus on demonstrating comprehensive privileged account management lifecycle
- If using shared/generic accounts (discouraged), ensure additional controls documented
