# EC-056-D - Administrator Access to Network/Cloud

**Evidence ID:** EC-056-D  
**Control Name:** Administrator Access to Network/Cloud  
**Control ID:** SG-056  
**TSC Criteria:** SOC2:2022.CC.6.2  
**Frequency:** Daily  
**Governing Document:** IS-CIRQ-PR-009-G- Privileged Access Management Procedure  
**Primary Repository Location:** 07-Operational-Evidence/Access-Control-Logs  
**Status:** Pending Collection

## Required Evidence Artifacts

This control requires the following specific evidence for **Network and Cloud Administrator Access**:

1. **Network/Cloud Admin Account Inventory** - List of all network and cloud administrator accounts including:
   - Network device admin accounts (firewalls, switches, routers, wireless controllers)
   - Cloud service provider admin accounts (Azure, AWS, etc.)
   - Physical access control system admins (Unifi Access)
   - Account names and associated systems/platforms
   - Individuals authorized to use each account
   - Access level (Global Admin, Subscription Owner, etc.)
   - Purpose/business justification
   - Last review date

2. **Quarterly Access Review (Network/Cloud Admins)** - Most recent quarterly review showing:
   - Verification of continued need for network/cloud admin access
   - Review of Azure/cloud administrative role assignments
   - Review of network device admin access lists
   - Confirmation of least privilege for cloud subscriptions/resources
   - Identification of unused admin accounts
   - Reviewer signature (IT Manager) and date

3. **MFA Enforcement for Network/Cloud Access** - Proof that MFA is enabled for:
   - Azure Global Administrator and privileged roles
   - AWS root account and IAM admin users
   - Network device management interfaces
   - Firewall administrative access
   - Cloud management consoles
   - Screenshots showing MFA requirements and conditional access policies

4. **Access Approval Sample** - Sample network/cloud admin access request showing:
   - Formal request for network or cloud administrative privileges
   - Business justification and specific systems/platforms
   - IT Manager approval
   - Time-limited access if applicable (especially for cloud)

5. **Network/Cloud Admin Activity Logs** - Sample logs demonstrating:
   - Azure Activity Logs showing administrative actions
   - AWS CloudTrail logs (if applicable)
   - Network device configuration change logs
   - Firewall rule modification logs
   - Evidence of centralized logging and monitoring
   - Evidence of regular log review

## Evidence Summary
Provide the finalized evidence artifact(s) for this control here, including capture date, source system, and reviewer.

## Collection Details
- **Collected By:**
- **Collection Date:**
- **System/Source:** Azure Activity Logs, Azure AD, network device logs, firewall logs
- **Reviewer:**
- **Review Date:**
- **Audit Period Coverage:** [Specify date range]

## Evidence Checklist
- [ ] Network/cloud admin account inventory (current)
- [ ] Azure administrative role assignments report
- [ ] Network device admin access lists
- [ ] Quarterly access review for network/cloud admins (signed)
- [ ] MFA configuration for Azure admin roles and network devices
- [ ] Azure Conditional Access policies for admin accounts
- [ ] Sample network/cloud admin access request with approval
- [ ] Network/cloud admin activity log exports (sample period)
- [ ] Evidence of log monitoring and alerting
- [ ] Confirm timeframe is within audit period
- [ ] Confirm IT Manager approval is included

## Upload Instructions
1. Collect network/cloud admin access evidence from Azure, network devices, and cloud platforms
2. Package files with clear naming: `EC-056-D_NetworkCloudAdminInventory_YYYY-MM-DD`, `EC-056-D_QuarterlyReview_Q#_YYYY`, etc.
3. Upload to StrikeGraph under "Administrator Access to Network/Cloud" evidence
4. Link to IS-CIRQ-PR-009-G governing procedure

## Notes
- **Critical for Cloud**: Azure Global Admin and Owner roles require special scrutiny
- Procedure requires quarterly reviews - ensure latest review is within 90 days
- Include Azure administrative role assignments as key evidence
- Network device logs should show configuration changes and admin access
- Demonstrate MFA + Conditional Access for all Azure admin accounts
- Show separation between standard user accounts and cloud admin accounts
- Include evidence of Just-in-Time (JIT) access if implemented for cloud resources
