# SOC2 Data Classification Policy

**Document ID:** SOC2-POL-DC-001  
**Owner:** IT/Security Lead  
**Effective Date:** 2026-03-02  
**Review Frequency:** Annual

## 1. Purpose
Define a consistent classification model so data is handled according to confidentiality and regulatory obligations.

## 2. Scope
Applies to all employees, contractors, systems, and third parties handling Cirque data.

## 3. Classification Levels
- **Public:** Approved for public release.
- **Internal:** Business-use only; limited internal distribution.
- **Confidential:** Sensitive business/customer/employee data; need-to-know access.
- **Restricted:** Highest sensitivity (regulated, credential, key, or security-critical data); strict access and monitoring.

## 4. Minimum Handling Requirements
| Requirement | Public | Internal | Confidential | Restricted |
|---|---|---|---|---|
| Access control | Open | Authenticated users | Need-to-know | Least privilege + approval |
| Encryption at rest | Optional | Recommended | Required | Required |
| Encryption in transit | Recommended | Required | Required | Required |
| Sharing external | Allowed if approved | Approved channels only | Contract + approval required | Prohibited unless executive + legal approval |
| Retention labeling | Optional | Required | Required | Required |

## 5. Roles and Responsibilities
- **Data Owner:** Assigns classification and approves changes.
- **System Owner:** Enforces controls in systems and applications.
- **IT/Security:** Monitors access, encryption, and policy compliance.
- **Users:** Handle and share data according to assigned classification.

## 6. Exceptions
Exceptions require documented risk acceptance, control compensations, and management approval.

## 7. Evidence for SOC 2
- Data inventory with classification field
- Access control lists by classification zone
- Encryption configuration evidence
- Exception approvals and review records
