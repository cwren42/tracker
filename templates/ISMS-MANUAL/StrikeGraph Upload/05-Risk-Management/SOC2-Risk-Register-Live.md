# SOC 2:2022 Risk Register (Live)

**Document ID:** RISK-REGISTER-2026-03-02  
**Date Created:** March 2, 2026  
**Last Updated:** 2026-03-02  
**Status:** Final - Ready for Audit  
**Owner:** Chris Wren (IT/Security Lead)  
**Review Frequency:** Quarterly or upon incident

---

## Risk Register Overview

This document lists all 38 active risks to Cirque Corporation's information security posture, along with their assessed impact/likelihood, current controls, and mitigation strategies. All risks have been scored per SOC 2:2022 scope (Security criteria only).

| Metric | Count |
|---|---|
| Total Risks | 38 |
| Active Risks | 31 |
| Mitigated Risks | 3 |
| Out of Scope (Phase 2) | 4 |
| Risk Score Distribution | 12 High, 16 Medium, 10 Low |

---

## Risk Register Details

### Access Control Risks (6 Total - All High)

| Risk ID | Risk Name | Category | Impact | Likelihood | Score | Owner | Current Controls | Mitigation Strategy | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-013 | Encryption | Access | **HIGH** | Medium | **HIGH** | Chris Wren | CC-022 (Encryption at Rest), CC-023 (Encryption in Transit), CC-019 (Disk Encryption) | Deploy end-to-end encryption; enforce TLS 1.2+ for all traffic; full-disk encryption on all devices. Complete by Q2 2026. | Active - In Progress |
| R-022 | Network Security | Access | **HIGH** | Medium | **HIGH** | Chris Wren | CC-024 (Firewall Rules), CC-029 (Intrusion Detection), CC-032 (Monitoring Infrastructure) | Implement IDS/IPS; segment networks by trust levels; restrict lateral movement; deploy next-gen firewall. Q2 2026. | Active - In Progress |
| R-023 | Password Management | Access | **HIGH** | Medium | **HIGH** | Chris Wren | CC-036 (Password Requirements), CC-049 (User Authentication), CC-031 (Logical Access Policy) | Enforce zero-trust password policy (minimum 12 chars, complexity, 90-day rotation); enable MFA on all systems. Q2 2026. | Active - In Progress |
| R-028 | Privileged Access Management | Access | **HIGH** | Medium | **HIGH** | Chris Wren | CC-056 (Administrator Access), CC-046 (Termination of Access), CC-048 (User Access Review) | Implement PAM solution (CyberArk or similar); time-limited elevation; all PA activity logged; quarterly reviews. Q2-Q3 2026. | Active - In Progress |
| R-032 | Source Code Control | Access | **HIGH** | Medium | **HIGH** | Chris Wren | CC-042 (Separation of Duties: Developers), CC-043 (Separation of Duties: IT Ops), CC-010 (Change Mgmt: Ticketing) | Git-based source control; code review mandatory; signed commits; no direct prod access for devs. Q2 2026. | Active - In Progress |
| R-035 | User Access Controls | Access | **HIGH** | Medium | **HIGH** | Chris Wren | CC-037 (Provisioning), CC-046 (Termination of Access), CC-048 (User Access Review) | RBAC implementation; automated provisioning/de-provisioning; monthly access reviews; quarterly comprehensive audit. Q2 2026. | Active - In Progress |

### Legal/Compliance Risks (3 Total - All High)

| Risk ID | Risk Name | Category | Impact | Likelihood | Score | Owner | Current Controls | Mitigation Strategy | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-006 | Compliance with Laws and Regulations | Legal | **HIGH** | Medium | **HIGH** | Chris Wren | CC-040 (Risk Assessment Policy), CC-003 (Board Oversight), CC-039 (Risk Assessment Methodology) | Legal review of data handling practices; map to GDPR/CCPA/SOX compliance; compliance calendar; quarterly reviews. Q2 2026. | Active - In Progress |
| R-008 | Contracts | Legal | **HIGH** | Medium | **HIGH** | Chris Wren | CC-014 (Contracts), CC-051 (Vendor Management Policy), CC-047 (Third Party SOC2) | Standardized contract templates with security/SLA clauses; legal review all new contracts; vendor SLA dashboard. Q2 2026. | Active - In Progress |
| R-011 | Data Breach | Privacy | **HIGH** | Medium | **HIGH** | Chris Wren | CC-022, CC-023, CC-019 (Encryption), CC-026 (Incident Response), CC-025 (Employee Responsibility) | End-to-end encryption; incident response drills; breach notification procedure; cyber insurance (consider Q2). | Active - In Progress |

### Vendor/Supply Chain Risks (1 Total - High)

| Risk ID | Risk Name | Category | Impact | Likelihood | Score | Owner | Current Controls | Mitigation Strategy | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-037 | Vendor Management | Vendor | **HIGH** | Medium | **HIGH** | Chris Wren | CC-050 (Due Diligence), CC-051 (Policy), CC-052 (Review), CC-053 (Risk Register), CC-047 (SOC2 attestations) | Vendor risk matrix; annual SOC2 attestation requirement for critical vendors; SLA monitoring dashboard; quarterly reviews. Q2 2026. | Active - In Progress |

### Policy/Governance Risks (6 Total - All Medium)

| Risk ID | Risk Name | Category | Impact | Likelihood | Score | Owner | Current Controls | Mitigation Strategy | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-004 | Business Continuity | Policy | Medium | Medium | Medium | Chris Wren | CC-004 (BC Plan), CC-032 (Monitoring), CC-003 (Board Oversight) | Formalize BC/DR plan; 30-day RTO; 4-hour RPO; annual testing; board-level oversight. Q2 2026. | Active - In Progress |
| R-012 | Data Management | Policy | Medium | Medium | Medium | Chris Wren | CC-017 (Data Policy), CC-018 (Retention/Deletion), CC-015 (Classification) | Finalize data mgmt policy; enforce classification; implement DLP tools; quarterly audits. Q2 2026. | Active - In Progress |
| R-019 | Incident Management | Policy | Medium | Medium | Medium | Chris Wren | CC-026 (Process), CC-027 (Testing), CC-025 (Responsibility), CC-028 (External Reporting) | Document full IR procedures; assign IR team; conduct annual tabletop exercise. Q2 2026. | Active - In Progress |
| R-021 | IT Security Governance | Policy | Medium | Medium | Medium | Chris Wren | CC-003 (Board Oversight), CC-040 (Risk Assessment Policy), CC-039 (Methodology), CC-038 (Action Plans) | Establish Security Committee; quarterly risk/control reviews with board; budget for Q2 projects. Q1-Q2 2026. | Active - In Progress |
| R-005 | Change Management | Technical | Medium | Medium | Medium | Chris Wren | CC-005 (Policy), CC-006 (Software), CC-007 (Emergency), CC-008 (Infrastructure), CC-009/010 (SoD, Ticketing) | Enforce change control via Jira; CAB approval required; test before prod; post-implementation review. Q1 2026 (done April). | Active - In Progress |
| R-024 | Patching | Technical | Medium | Medium | Medium | Chris Wren | CC-058 (Automatic Patching), CC-013 (Config Standards), CC-008 (Change Mgmt for patches) | Monthly patch management cadence; critical patches within 30 days; monthly test cycle. Q1 2026. | Active - In Progress |

### Technical Risks (8 Total - Mix of Medium & High)

| Risk ID | Risk Name | Category | Impact | Likelihood | Score | Owner | Current Controls | Mitigation Strategy | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-002 | Antivirus | Technical | Medium | Low | **LOW** | Chris Wren | CC-057 (Deployed, updated daily), CC-029 (Intrusion Detection), CC-032 (Monitoring) | Maintain current AV coverage; daily signature updates; quarterly review of detection/response metrics. Ongoing. | Mitigated |
| R-014 | End User Device Protections | Technical | Medium | Medium | Medium | Chris Wren | CC-057 (Antivirus), CC-019 (Disk Encryption), CC-036 (Password Policy), CC-049 (Auth) | MDM solution for all laptops/mobiles; auto-lock after 10 min; full-disk encryption; OS updates required. Q2 2026. | Active - In Progress |
| R-020 | Intrusion Detection | Technical | Medium | Medium | Medium | Chris Wren | CC-029 (IDS/IPS), CC-024 (Firewall), CC-032 (Monitoring) | Deploy IDS/IPS on network perimeter; 24/7 monitoring; alert response SLA <1 hour. Q2 2026. | Active - In Progress |
| R-033 | System Monitoring | Technical | Medium | Medium | Medium | Chris Wren | CC-032 (Monitoring Infrastructure), CC-004 (BC/DR), CC-054 (Vulnerability Scan) | SIEM deployment; alert rules for suspicious activity; daily log review; escalation procedures. Q2 2026. | Active - In Progress |
| R-034 | Systems Configuration | Technical | Medium | Medium | Medium | Chris Wren | CC-013 (Config Standards), CC-016 (Data Flow Diagram), CC-033 (Network Diagram) | Baseline configuration for all systems; version control for configs; quarterly CIS benchmark scan. Q2 2026. | Active - In Progress |
| R-017 | Fraud | Fraud | Medium | Low | **LOW** | Chris Wren | CC-042/043 (SoD), CC-048 (Access Review), CC-026 (IR Process) | Quarterly access reviews; segregation of duties enforced in finance/payroll; audit logs; exception reports. Ongoing. | Mitigated |
| R-038 | Vulnerability Management | Technical | Medium | Medium | Medium | Chris Wren | CC-054 (Quarterly Scans), CC-008 (Change Mgmt), CC-013 (Config Standards) | Quarterly vuln scans; remediation SLA per severity (critical <30d, high <60d); retesting; dashboard tracking. Q2 2026. | Active - In Progress |

### Physical Risks (2 Total - Low)

| Risk ID | Risk Name | Category | Impact | Likelihood | Score | Owner | Current Controls | Mitigation Strategy | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-015 | Environment | Physical | Medium | Low | **LOW** | Chris Wren | Card access systems active, visitor logging in place, server room restricted | Maintain card access; quarterly access reviews; annual assessment of physical controls. Ongoing (in scope). | Active - Mitigated |
| R-025 | Physical Access Controls | Physical | Medium | Low | **LOW** | Chris Wren | Card access, visitor procedures, server room locks, CCTV (monitoring) | Annual physical security audit; maintain current systems; update visitor procedures if facilities change. Ongoing (in scope). | Active - Mitigated |

### People/HR Risks (8 Total - All Medium)

| Risk ID | Risk Name | Category | Impact | Likelihood | Score | Owner | Current Controls | Mitigation Strategy | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-001 | Acceptable Use of Company Assets | People | Medium | Medium | Medium | Chris Wren | CC-001 (Policy), CC-041 (Training), CC-011 (Code of Conduct) | Finalize AUP; annual training; acknowledgements; quarterly spot checks on usage. Q1 2026. | Active - In Progress |
| R-009 | Control Ownership | People | Medium | Medium | Medium | Chris Wren | CC-030 (Job Descriptions), CC-003 (Board Oversight), RACI (UB-05) | Assign explicit control owners in RACI; quarterly responsibility reviews; succession planning. Q1 2026. | Active - In Progress |
| R-010 | Corporate Monitoring | People | Medium | Medium | Medium | Chris Wren | Policies under development, CC-002 (Background Checks), CC-020 (Perf Reviews) | Balanced monitoring of IT systems (logs, emails); privacy-respecting; documented policy. Q1 2026. | Active - In Progress |
| R-016 | External Communications | People | Medium | Medium | Medium | Chris Wren | CC-011 (Code of Conduct), CC-041 (Training), CC-002 (Background Checks) | Training on public relations/media; incident communication scripts; approval process for public statements. Q1 2026. | Active - In Progress |
| R-018 | HR Practices | People | Medium | Medium | Medium | Brenda Milian | CC-020 (Perf Reviews), CC-002 (Background Checks), CC-030 (Job Descriptions) | Regular training for HR staff; background checks for all new hires; annual capability assessments. Ongoing. | Active - In Progress |
| R-026 | Planning/Internal Communications | People | Medium | Medium | Medium | Chris Wren | UB-05 RACI approved, ongoing governance | Strategic planning with board; quarterly comms on security initiatives/risks. Q1-Q2 2026. | Active - In Progress |
| R-030 | Reporting Lines | People | Medium | Medium | Medium | Chris Wren | CC-035 (Org Chart), CC-030 (Job Descriptions) | Maintain current org chart; clear escalation paths; CYS annual review. Ongoing. | Active - In Progress |
| R-031 | Security Awareness | People | Medium | Medium | Medium | Chris Wren | CC-041 (Training), CC-025 (Responsibility), CC-001 (Policy) | Annual security training; monthly tips; phishing simulation campaign (starting Q2); sign-off tracking. Q1-Q2 2026. | Active - In Progress |

### Out of Scope (Phase 2) - 4 Risks

| Risk ID | Risk Name | Category | Impact | Likelihood | Score | Reason | Phase 2 Timeline |
|---|---|---|---|---|---|---|---|
| R-003 | Availability | Technical | **LOW** | Low | **LOW** | Out of Scope: SOC 2 Availability criteria deferred | Q3-Q4 2026 |
| R-007 | Confidentiality | Legal | **LOW** | Low | **LOW** | Out of Scope: SOC 2 Confidentiality criteria deferred | Q3-Q4 2026 |
| R-027 | Privacy of User Data | Privacy | **LOW** | Low | **LOW** | Out of Scope: SOC 2 Privacy criteria deferred | Q3-Q4 2026 |
| R-029 | Processing Integrity | Software | **LOW** | Low | **LOW** | Out of Scope: SOC 2 Processing Integrity criteria deferred | Q3-Q4 2026 |
| R-036 | User Data Collection | Privacy | **LOW** | Low | **LOW** | Out of Scope: SOC 2 Privacy criteria deferred | Q3-Q4 2026 |

---

## Risk Scoring Legend

### Impact Levels
- **HIGH:** Could result in material financial loss, regulatory fines, customer loss, reputational damage, or system unavailability >24 hours
- **MEDIUM:** Partial system disruption (< 24 hrs), customer complaint, elevated operational cost, data exposure (non-sensitive), compliance calendar miss
- **LOW:** Temporary/minor disruption (<4 hrs), isolated incident, no financial impact, data exposure limited to internal-only info

### Likelihood Levels
- **HIGH:** Expected to occur 1-2x per year; previously occurred in past 3 years
- **MEDIUM:** Possible; may occur once per 2-3 years; related incidents occur in industry
- **LOW:** Unlikely; <0.5x per year; rare in industry context

### Combined Risk Score
| Impact | Likelihood → | Low | Medium | High |
|---|---|---|---|---|
| **High** | | Medium | High | **HIGH** |
| **Medium** | | Low | **Medium** | High |
| **Low** | | Low | Low | **MEDIUM** |

---

## Mitigation Timeline

### Q1 2026 (Now - March)
- Finalize policies: Security, Data Mgmt, IR, Change Mgmt, AUP, Vendor Mgmt
- Launch security awareness training
- Establish control ownership (RACI finalized)
- Org chart update
- Begin evidence collection

### Q2 2026 (April - June)
- Deploy encryption (at-rest, in-transit, disk)
- Implement IDS/IPS
- Network segmentation
- MFA on all systems
- Source code control (Git)
- Vulnerability scanning (quarterly)
- BC/DR testing
- Access control reviews

### Q3 2026 (July - Sept)
- PAM solution implementation
- DLP tools
- SIEM deployment
- SOC 2 Type 2 planning begins
- Quarterly IR drill
- Vendor risk assessments complete

### Q4 2026 - 2027
- Type 2 audit execution
- Phase 2 audit planning (Confidentiality, Availability, Privacy)

---

## Risk Owner Responsibilities

**Chris Wren (IT/Security Lead):**
- Reviews risks monthly
- Updates mitigation status
- Escalates blockers to Exec Committee
- Leads Technical/Access/Legal/Vendor/Governance/Physical risk mitigation
- Coordinates with department heads on People risks

**Brenda Milian (HR):**
- HR Practices risk owner
- Coordinates background checks, training, job descriptions
- Supports People risk mitigation

**Department Managers:**
- Support control testing as required
- Report incidents/anomalies to IC
- Participate in quarterly reviews

**Exec Committee:**
- Approves risk tolerance/strategy quarterly
- Authorizes budget for mitigation (Q2 2026 projects ~$150-200K estimate)
- Reviews high-risk exceptions

---

## Approval & Sign-Off

| Role | Name | Signature | Date |
|---|---|---|---|
| Risk Owner | Chris Wren | _________________ | ________ |
| Chief Risk Officer | [Executive] | _________________ | ________ |
| Board Oversight | [Board Member] | _________________ | ________ |

---

**Document Version:** 1.0  
**Last Updated:** 2026-03-02  
**Next Review:** 2026-04-01 (Monthly during audit)  
**Quarterly Reviews:** Q2 (April), Q3 (July), Q4 (Oct) 2026
