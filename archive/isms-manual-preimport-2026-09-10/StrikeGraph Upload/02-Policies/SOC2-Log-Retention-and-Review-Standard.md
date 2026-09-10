# SOC2 Log Retention and Review Standard

**Document ID:** SOC2-STD-LOG-001  
**Owner:** IT/Security Lead  
**Effective Date:** 2026-03-02  
**Review Frequency:** Annual

## 1. Purpose
Define log sources, retention periods, and review cadence required to support SOC 2 monitoring controls.

## 2. Required Log Sources
- Authentication and identity events
- Privileged access events
- Administrative configuration changes
- Endpoint protection and malware alerts
- Firewall and network security events
- Application security events (where available)

## 3. Retention and Protection
| Log Type | Retention | Integrity Protection |
|---|---|---|
| Security alerts | 12 months | Centralized immutable store where feasible |
| Admin activity logs | 12 months | Access-controlled repository |
| Access/auth logs | 12 months | Tamper-protected storage |
| Incident evidence logs | 24 months | Case-linked archival |

## 4. Review Cadence
- Daily: SEV-1/SEV-2 alert queue review
- Weekly: trend and anomaly review
- Monthly: control effectiveness and false positive review

## 5. SOC 2 Evidence
- Monthly review records
- Alert investigation tickets
- Retention setting screenshots/exports
- Access control records for logging platform
