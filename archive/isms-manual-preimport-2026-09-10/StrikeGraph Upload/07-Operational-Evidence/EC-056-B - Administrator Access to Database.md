# EC-056-B - Administrator Access to Database

**Evidence ID:** EC-056-B  
**Control Name:** Administrator Access to Database  
**Control ID:** SG-056  
**TSC Criteria:** SOC2:2022.CC.6.2  
**Frequency:** Daily  
**Governing Document:** IS-CIRQ-PR-009-G- Privileged Access Management Procedure  
**Primary Repository Location:** 07-Operational-Evidence/Access-Control-Logs  
**Status:** Pending Collection

## Required Evidence Artifacts

This control requires the following specific evidence for **Database Administrator Access**:

1. **Database Admin Account Inventory** - List of all database administrator accounts including:
   - Database platform (SQL Server, MySQL, PostgreSQL, Oracle, etc.)
   - Database admin account names (SA, DBA accounts, etc.)
   - Individuals authorized to use each account
   - Database systems/instances covered
   - Purpose/business justification
   - Last review date

2. **Quarterly Access Review (Database Admins)** - Most recent quarterly review showing:
   - Verification of continued need for database admin access
   - Confirmation of least privilege principle for DB roles
   - Review of database admin activity audit trails
   - Verification that standard users don't have DBA privileges
   - Reviewer signature (IT Manager) and date

3. **MFA Enforcement for Database Access** - Proof that MFA is enabled for:
   - Database management consoles
   - Remote database access tools
   - Cloud database admin portals (Azure SQL, AWS RDS, etc.)
   - Screenshots or configuration exports showing MFA requirements

4. **Access Approval Sample** - Sample database admin access request showing:
   - Formal request for DBA privileges
   - Business justification and specific databases needed
   - IT Manager approval
   - Time-limited access if applicable

5. **Database Admin Activity Logs** - Sample logs demonstrating:
   - User, date/time, SQL commands executed
   - Schema changes, privilege grants, data access
   - Database audit logs showing admin activities
   - Evidence of log review and monitoring

## Evidence Summary
Provide the finalized evidence artifact(s) for this control here, including capture date, source system, and reviewer.

## Collection Details
- **Collected By:**
- **Collection Date:**
- **System/Source:** SQL Server audit logs, database management tools, Azure SQL audit logs
- **Reviewer:**
- **Review Date:**
- **Audit Period Coverage:** [Specify date range]

## Evidence Checklist
- [ ] Database admin account inventory (current)
- [ ] Quarterly access review for database admins (signed)
- [ ] MFA configuration for database access
- [ ] Sample database admin access request with approval
- [ ] Database admin activity log exports (sample period)
- [ ] Evidence of separation between standard and admin DB accounts
- [ ] Confirm timeframe is within audit period
- [ ] Confirm IT Manager approval is included

## Upload Instructions
1. Collect database admin access evidence from all production databases
2. Package files with clear naming: `EC-056-B_DBAdminInventory_YYYY-MM-DD`, `EC-056-B_QuarterlyReview_Q#_YYYY`, etc.
3. Upload to StrikeGraph under "Administrator Access to Database" evidence
4. Link to IS-CIRQ-PR-009-G governing procedure

## Notes
- Focus on production databases containing sensitive/customer data
- Procedure requires quarterly reviews - ensure latest review is within 90 days
- Include both on-premises and cloud database platforms
- Demonstrate that database audit logging is enabled and monitored
- Verify SA or equivalent accounts are not used for routine tasks
