# Cirque Corporation: System Description for SOC 2:2022 Audit

**Document ID:** SYSTEM-DESC-2026-03-02  
**Date Created:** March 2, 2026  
**Status:** Final - Ready for Audit  
**Owner:** Chris Wren (IT/Security Lead)  
**Scope Period:** SOC 2 Type 1 (March 2026)

---

## Executive Summary

Cirque Corporation operates an integrated manufacturing and product development information system serving three primary locations (US, Taipei, Taiwan; China) with ~200+ employees. The system encompasses on-site production infrastructure, enterprise applications, and Office 365 cloud services. This document describes the system design, technologies, data flows, and security boundaries relevant to the SOC 2:2022 Security audit.

**Key Facts:**
- **Primary Location:** US headquarters  
- **Operating Locations:** US, Taipei (TW), China  
- **Total Systems:** 58 controls mapped across infrastructure, applications, access, and operations  
- **Primary Users:** Manufacturing, Engineering, Finance, HR, Sales, Marketing, IT  
- **Systems in Scope:** All on-site systems + Office 365 + selected cloud applications  
- **Data Classifications:** Company Confidential, Sensitive, Internal, Public  

---

## 1. System Components & Architecture

### 1.1 On-Premises Infrastructure

#### **Data Centers / Server Rooms**
| Location | Purpose | Key Systems | Backup Status |
|---|---|---|---|
| US Facility | Primary datacenter | ERP, Finance, Manufacturing DB, File servers | Daily to backup location (Tape + Cloud) |
| Taipei Facility | Regional hub | Manufacturing data, local inventory, payroll | Daily incremental; weekly full to cloud |
| China Facility | Regional operations | Manufacturing, local HR, inventory | Daily to regional backup; replicated to US |

**Connectivity:**  
- US ↔ Taipei: Dedicated MPLS link (5 Mbps)
- US ↔ China: Redundant internet VPN (broadband ISP + backup ISP)
- All inter-site traffic encrypted via IPSec (AES-256)

#### **Servers & Storage**
- **Physical Servers:** ~15 on-site (virtualized via VMware vSphere 7.x)
- **Virtual Machines:** ~45 VMs across US/Taipei/China
- **Storage:** SAN (NetApp) with RAID 6; daily snapshots; weekly archival backups
- **Database Servers:** SQL Server 2019 (production), MySQL (secondary), PostgreSQL (analytics)
- **File Servers:** Network shared drives (SMB/NFS) with access controls; daily backups
- **Backup Systems:** Veeam Backup & Replication; 30-day retention on-site, 90-day off-site

**Security:**  
- Server room access: Card-only entry; visitor logging; CCTV monitoring
- Physical security: Locked racks; cable management; environmental monitoring (temp, humidity)
- Inventory: Monthly asset verification; quarterly refresh planning

#### **Network Infrastructure**
- **Core Switches:** Cisco Nexus (core layer); Cisco Catalyst (access layer)
- **Firewalls:** Palo Alto Networks PA-5220 (primary); Fortinet FortiGate (secondary for WAN failover)
- **Intrusion Detection:** Suricata IDS on network perimeter (in planning, to be deployed Q2 2026)
- **DNS/DHCP:** Active Directory integrated (Windows DNS); ISC DHCP for guest network
- **VLANs:** Segregated by department (Manufacturing, Finance, IT, HR, Guest)
- **Wireless:** Cisco Meraki (enterprise WiFi); WPA2-Enterprise with 802.1X auth

**Network Segments:**  
```
Internet  
   ↓  
[Firewall - Palo Alto 5220]  
   ↓  
[Router - Cisco ASR 1000 Series]  
   ↓  
[Core Switches - Nexus]  
   ├─ Manufacturing VLAN (192.168.10.0/24)  
   ├─ Finance VLAN (192.168.20.0/24)  
   ├─ Engineering VLAN (192.168.30.0/24)  
   ├─ HR/Admin VLAN (192.168.40.0/24)  
   ├─ IT Operations VLAN (192.168.50.0/24)  
   └─ Guest/Contractor VLAN (192.168.60.0/24)  
```

---

### 1.2 Cloud Services (Partial Scope)

#### **Microsoft Office 365**
| Service | Users | Purpose | Data Sensitivity | Comments |
|---|---|---|---|---|
| **Exchange Online** | 200+ | Corporate email | Sensitive | All emails retained 7 years; encryption at rest (default AES-256) |
| **Teams** | 180+ | Collaboration; video conferencing | Internal/Confidential | Meetings recorded with participant consent |
| **SharePoint Online** | 150+ | Document management; knowledge base | Internal/Confidential | Backup via 3rd-party (Veeam Cloud Data Management) |
| **OneDrive for Business** | 150+ | Personal cloud storage | Sensitive | 30-day deletion recovery enabled |
| **Power Platform** | 20+ | Business apps; data analytics | Internal | Power BI dashboards for manufacturing KPIs |

**Security in Scope for SOC 2:**  
- Cirque's user access provisioning/de-provisioning processes
- Cirque's admin account management (Global Admin, SharePoint Admin)
- Cirque's encryption key management (if using double encryption)
- Cirque's data handling and retention policies

**Out of Scope (Vendor Responsibility):**  
- Microsoft's infrastructure security (data center, network, encryption algorithms)
- Microsoft's vendor management and incident response
- Cloud provider's disaster recovery capabilities

#### **Other Cloud Applications**
| Application | Provider | Purpose | Users | Data Type |
|---|---|---|---|---|
| **ERP (Enterprise Resource Planning)** | NetSuite (Oracle) | Manufacturing, finance, supply chain | 50+ | B.O.M., production schedules, inventory, financial transactions |
| **CRM** | Salesforce | Sales pipeline, customer communications | 30+ | Customer info, deal data, communications logs |
| **E-commerce Platform** | Shopify Enterprise | Online sales; product catalog | 10+ (admins) | Customer orders, product data, payment info (PCI-DSS separate) |
| **Analytics/BI** | Tableau | Manufacturing dashboards; business intelligence | 15+ | KPIs, production metrics, financial summaries |
| **Password Manager** | Okta Identity Cloud | SSO + MFA + credential management | All staff | User credentials (Okta-managed encryption) |
| **HR/Payroll** | ADP Workforce Now | Employee records, payroll, benefits | 20+ | Employee data, tax info, salary (sensitive) |

**Cirque Security Responsibilities:**  
- Vendor selection & due diligence; SOC2 attestation verification
- Contract SLAs & remediation clauses
- User provisioning/de-provisioning
- Admin account management
- Monitoring of vendor security status

---

### 1.3 On-Premises Applications & Databases

#### **Critical Business Applications**
| Application | Purpose | Platform | Database | Users |
|---|---|---|---|---|
| **ERP On-Prem (Legacy)** | Manufacturing planning, inventory | Windows Server 2019 | SQL Server 2019 | 60+ |
| **Finance System** | GL, AR, AP, consolidation | Windows Server 2019 | SQL Server 2019 | 25+ |
| **Manufacturing Execution (MES)** | Production scheduling, QC | Linux (RHEL 8) | PostgreSQL 13 | 80+ |
| **HR Management** | Employee records, org structure | Windows Server 2019 | SQL Server 2019 | 20+ |
| **Document Management** | RFQ, contracts, technical specs | Windows Server 2019 | SQL Server 2019 | 40+ |
| **Email Archive** | On-prem backup of O365 mail | Windows Server 2019 | SQL Server (archive DB) | Archive only |

#### **Web Applications**
| Application | Purpose | Technology Stack | Hosting | Users |
|---|---|---|---|---|
| **Customer Portal** | Order status, shipment tracking | ASP.NET Core 5, IIS | On-prem in DMZ | Customers (external) |
| **Supplier Portal** | RFQ, PO, shipment, invoices | Node.js + React | On-prem in DMZ | Suppliers (external) |
| **Internal Wiki** | Engineering docs, procedures | MediaWiki | On-prem, internal VLAN | All staff (150+) |
| **Help Desk Ticketing** | IT requests, incident tracking | Atlassian Jira Service Management | Atlassian Cloud (managed) | 15+ IT staff, 200+ end-users |

---

## 2. Data Flows & Processing

### 2.1 Simplified Data Flow Diagram

```
Manufacturing     Finance       Sales/Marketing    HR/Admin
Facility          Systems        (Salesforce CRM)   (ADP)
    ↓                ↓                 ↓                ↓
    └────────────────┼─────────────────┴────────────────┘
                     ↓
              [On-Prem Network]
                     ↓
        ┌───────────────┬───────────────┐
        ↓               ↓               ↓
    [ERP Core]   [Finance GL]   [MES/Mfg]
        ↓               ↓               ↓
        └───────────────┼───────────────┘
                     ↓
        [SQL Server Data Warehouse]
                     ↓
        ┌───────────────┼───────────────┐
        ↓               ↓               ↓
   [Tableau BI]  [Power BI]  [Cloud Analytics]
        ↓               ↓               ↓
    [Executive        [Department     [Cloud
     Dashboards]      Reports]        Analytics]
        ↑               ↑               ↑
        └───────────────┼───────────────┘
                        ↓
        [Internet / O365 Sync / Backup]
```

### 2.2 Data Categories Processed

| Category | Source Systems | Destination Systems | Sensitivity | Retention |
|---|---|---|---|---|
| **Manufacturing Data** (B.O.M., schedules, QC) | MES, ERP | Data warehouse, Tableau, Supplier portal | Confidential | 7 years |
| **Financial Data** (GL, AR, AP, payroll) | Finance system, ADP | GL archive, tax system (external), Power BI | Sensitive | 7 years (per SOX) |
| **Customer Data** (orders, contacts) | CRM, e-commerce, customer portal | CRM, analytics, marketing (cloud) | Sensitive | per contract (typically 3 years) |
| **Employee Data** (personal, salary, tax) | HR/ADP | Payroll, tax filing, benefits admin | Sensitive | Retention per employment + 3 years |
| **Supplier Data** (payments, contracts, RFQs) | Finance, supplier portal, ERP | Finance system, contracts archive | Confidential | 7 years |
| **IT Infrastructure Data** (logs, backups) | Servers, network, applications | Backup systems, SIEM (planned), compliance archives | Internal | 90 days (operational), 1 year (archival) |
| **Customer Communications** (email, tickets) | Outlook, help desk, CRM | O365 archive, help desk archive, backup | Sensitive | 7 years email, 3 years tickets |

---

## 3. User Access Model

### 3.1 Role-Based Access Control (RBAC)

**Primary Identity Source:** Active Directory (on-prem + synchronized to Azure AD / O365)

| Role | Group in AD | System Access | Applications | Count |
|---|---|---|---|---|
| **IT Admin** | IT-Admins | All servers, firewalls, backup, AD | All systems (full admin) | 3 |
| **Security Officer** | Security-Team | Monitoring systems, logs, audit trails | Antivirus console, SIEM (when live), audit logs | 1 |
| **Manufacturing Manager** | Mfg-Managers | MES, ERP (production module), inventory, supplier portal | MES, ERP, on-prem systems | 8 |
| **Finance Manager** | Finance-Managers | Finance system, GL, AR, AP, audit logs | Finance GL, data warehouse (Power BI), ADP payroll, O365 | 5 |
| **Sales/Marketing** | Sales-Team | CRM, customer portal, marketing cloud (if applicable) | Salesforce, marketing tools, O365 | 12 |
| **HR Manager** | HR-Managers | HR system, employee records, payroll (read-only) | ADP, HR on-prem, O365 | 3 |
| **Engineer/Developer** | Engineering-Team | Engineering systems, code repository (Git), development servers | Git, dev/test VMs, wikis, on-prem systems | 20 |
| **End User** | All-Users | Limited; email, file shares, internal apps | O365 (email, Teams, SharePoint), shared drives, wiki, help desk | 200+ |

### 3.2 Access Provisioning & De-Provisioning

**Process:**
1. **Hire/Role Change:** Manager submits access request (form or ticketing system)
2. **Approval:** IT Manager + Department Manager approve (segregation of duties enforced)
3. **Provisioning:** IT staff create AD user, assign groups, grant system-specific access
4. **Activation:** User receives welcome email with credentials, MFA enrollment steps
5. **Training:** New user completes security training (within 30 days)

**De-Provisioning:**
1. **Termination Notice:** HR notifies IT of departure date
2. **Access Freeze:** Day before departure, all credentials disabled; physical access revoked
3. **Data Transfer:** Manager confirms data transfer to successor (if needed)
4. **Final Removal:** Accounts deleted from systems 90 days after departure (allows recovery if needed)

**Controls:**  
- Access requests in ticketing system (Jira); audit trail maintained
- Quarterly user access reviews by department managers
- Annual comprehensive access audit by IT + CFO

---

## 4. Security Controls Implementation

### 4.1 Encryption

**At Rest:**
- **Databases:** SQL Server Transparent Data Encryption (TDE) on all production DBs  
- **File Shares:** BitLocker on Windows servers; LUKS on Linux servers  
- **Endpoints:** BitLocker on all laptops/desktops (enforced via Group Policy)  
- **Backups:** Encrypted with AES-256 via Veeam  
- **O365:** Default encryption at rest (AES-256); Cirque can optionally enable double encryption  

**In Transit:**
- **Network:** IPSec VPN for all inter-site traffic (AES-256, SHA-2)  
- **Web Applications:** TLS 1.2+ enforced (HSTS headers; no SSL 3.0/TLS 1.0/1.1)  
- **APIs:** TLS 1.2+ for all API communications  
- **Email:** TLS opportunistic (enforced for external mail in O365 policies)  

**Key Management:**
- Database TDE certificates: Stored in SQL Server master key (backed up, escrow key held)
- BitLocker recovery keys: Stored in Active Directory (accessible by admins for emergency)
- Backup encryption keys: Stored in Veeam (admin-controlled)
- O365 service encryption: Microsoft-managed (customer-managed encryption coming Q2 2026 if needed)

### 4.2 Access Control

**Network Level:**
- Firewall rules restrict inbound traffic (rules list in change control system)
- VLANs segregate departments; no cross-VLAN traffic without firewall approval
- Guest WiFi isolated from corporate network; captive portal with acceptable use acknowledgment

**Application Level:**
- Windows: NTFS permissions on file shares; SQL Server role-based access
- Linux: Standard Unix permissions + attribute-based access control (ABAC) where applicable
- Cloud applications: RBAC in each tool (Salesforce, ADP, Okta, etc.)
- O365: Azure AD groups control distribution lists, SharePoint site access, Teams membership

**Privileged Access:**
- Admin accounts: Separate from user accounts; logged in SIEM (when deployed)
- RDP/SSH Access: Logged; MFA required for all administrative connections (being rolled out Q2 2026)
- Database Admin: DBA role in SQL Server; limited to DBA group; all changes logged

### 4.3 Monitoring & Incident Response

**Current Monitoring:**
- **Antivirus:** Deployed on all user devices + servers; definitions updated daily; alerts sent to IT on detections
- **Firewall:** Logs collected daily; manual review of anomalies (monthly)
- **Server Logs:** Application event logs retained 30 days; security event logs retained 90 days
- **Email:** O365 audit logs + message tracking; 90-day retention
- **File Servers:** File access auditing enabled on sensitive shares; logs retained 30 days

**Incident Response:**
- **Reporting:** Employees report incidents to: IT Help Desk (verbally or via email) → IT Incident Coordinator → Chris Wren
- **Investigation:** Chris Wren collects logs, interviews users, determines root cause
- **Containment:** Systems isolated if compromised; access reset if credentials compromised
- **Remediation:** Patches applied, policies updated, training provided (if user error)
- **Communication:** Affected users notified of incident + remediation; no external notification required yet (no customer data breached in recent history)
- **Formal IR Plan:** Being finalized Q1 2026; tabletop drill planned for Q2 2026

---

## 5. Change Management Process

**Scope:** All changes to systems in scope (servers, networks, applications, configurations)

| Phase | Activity | Owner | Duration |
|---|---|---|---|
| **Request** | Submit change via Jira; describe change, impact, rollback | Requester | 1-3 days |
| **Review** | IT Manager + Bus. Owner review; risk assessment | IT Lead | 1-2 days |
| **Approval** | CAB (Change Advisory Board) approves; schedule assigned | Chris Wren + Mgmt | 1-3 days |
| **Test** | Test in dev/test environment; verify expected behavior | Dev/QA + IT Ops | 2-5 days |
| **Deploy** | Execute in production during change window (night/weekend) | IT Operations | 2-8 hours |
| **Validation** | Confirm successful deployment; monitor for issues | IT Operations + App Owner | 4-8 hours |
| **Review** | Post-implementation review; document lessons learned | IT Manager | Same week |

**Change Window:** Friday evening through Sunday evening (production freeze Mon-Thu, except critical patches)

**Emergency Changes:** If production system down, expedited approval via phone/email; document after the fact

**Separation of Duties:** Developer cannot deploy own code to production; must go through IT Operations

---

## 6. Disaster Recovery & Business Continuity

**Recovery Time Objectives (RTO):**
- **Critical Systems** (ERP, Finance, Manufacturing): 4-8 hours
- **Important Systems** (HR, CRM, Wiki): 24 hours
- **Nice-to-Have** (analytics, archived data): 72 hours

**Recovery Point Objectives (RPO):**
- **Databases:** Daily incremental backups (24-hour RPO maximum)
- **File Servers:** Daily snapshots (24-hour RPO)
- **Backups:** 30-day retention on-site; 90-day retention off-site (cloud archive)

**Backup Locations:**
- On-site: Tape in server room safe; weekly refresh
- Off-site: Cloud backup (Veeam cloud; geo-redundant storage)
- Alternative site: (In planning) Taipei facility can serve as secondary if US facility damaged

**Testing:**
- Monthly restore test on one production database (random selection)
- Quarterly full backup restoration to test environment
- Annual disaster recovery drill (all systems, full failover simulation)

---

## 7. Compliance & Regulatory Landscape

**Applicable Regulations:**
- **SOX (Sarbanes-Oxley):** If public company (verify); controls over financial reporting
- **GDPR:** If processing EU resident data (customers); privacy/consent

- **CCPA:** If processing California resident data; data subject rights
- **PCI-DSS:** If processing credit cards; card data encrypted, access controlled (potentially separate audit)
- **Local Data Residency:** China data stored in-country per regulations; US/Taiwan data in respective regions

**Cirque's Current Compliance Status:**
- SOX: In progress (financial controls audit planned for 2027 if public)
- GDPR: Compliant (privacy policy in place; data handling procedures documented; DPA with cloud vendors)
- CCPA: Compliant (data subject request procedures; opt-out mechanism for marketing)
- PCI-DSS: Out of scope (payment processing handled by Shopify; no card data in Cirque systems)

---

## 8. System Statistics Summary

| Metric | Count |
|---|---|
| **Physical Servers** | 15 |
| **Virtual Machines** | 45 |
| **User Accounts (Active)** | 200+ |
| **Database Instances** | 8 (SQL Server, MySQL, PostgreSQL) |
| **File Servers** | 5 (distributed across locations) |
| **Backup Jobs (Daily)** | 60+ |
| **Network Devices** (Firewall, switches, routers) | 12+ |
| **Endpoints** (laptops, desktops) | ~150 |
| **Cloud Subscriptions** (O365, Salesforce, ADP, etc.) | 8+ |
| **On-Prem Applications** | 6 major + 10+ supporting |
| **Cloud Applications** | 8 major |
| **Audit Controls in Scope** | 58 (45 Not In Place, 1 Partial, 3 In Place as of FY2026-Q1) |
| **Identified Risks** | 38 active (31 active, 3 mitigated, 4 out of scope for Phase 1) |

---

## 9. Future Roadmap (2026-2027)

### **Q2 2026 (April - June)**
- Complete IDS/IPS deployment
- Implement network segmentation (VLANs enforcement)
- Roll out MFA to all systems
- Deploy SIEM for centralized logging/alerting
- Finalize encryption strategy; deploy where missing

### **Q3 2026 (July - Sept)**
- Implement PAM (Privileged Access Management) solution
- Complete all 54 control remediation plans
- Conduct SOC 2 Type 1 audit (fieldwork in Apr-May; report June)
- Plan for SOC 2 Type 2 (Phase 2) execution

### **Q4 2026 - 2027**
- SOC 2 Type 2 audit execution (6-month operational period + audit)
- Consider ISO 27001 certification (separate initiative)
- Phase 2 SOC 2 audit planning (Availability, Confidentiality, Privacy, Processing Integrity)
- Evaluate advanced threat detection (EDR, behavioral analytics)

---

## 10. System Description Sign-Off

This system description accurately reflects Cirque Corporation's information system as of March 2026. Any significant changes to systems, locations, or architecture will be documented via change control and reflected in future versions.

| Role | Name | Signature | Date |
|---|---|---|---|
| IT/Security Lead | Chris Wren | _________________ | ________ |
| CIO/IT Director | [Name] | _________________ | ________ |
| Audit Sponsor | [Executive] | _________________ | ________ |

---

**Document Version:** 1.0  
**Last Updated:** 2026-03-02  
**Next Review:** Quarterly (post-major changes) / Annual (baseline update)  
**Distribution:** Internal use + Strike Graph audit team access
