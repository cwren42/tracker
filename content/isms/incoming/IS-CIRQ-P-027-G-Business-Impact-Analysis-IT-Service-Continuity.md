---
title: Business Impact Analysis (IT Service Continuity)
slug: is-cirq-p-027-g
category: ISMS Manual
document_type: policy
version: "0.1"
status: draft
---
# Business Impact Analysis (IT Service Continuity)

| | |
|---|---|
| **Organization** | Cirque Corporation |
| **Document** | IS-CIRQ-P-027-G |
| **Internal reference** | ITSC-BIA-01 |
| **Version** | 0.1 |
| **Status** | DRAFT — pending executive acceptance |
| **Effective date** | — (on acceptance) |
| **Prepared** | 2026-08-31 · IT Operations |
| **Classification** | Internal — Confidential |
| **Review cycle** | Annual (next: Aug 2027) |

## 1. Purpose & Scope

This Business Impact Analysis (BIA) identifies the IT-supported business functions whose loss would
materially affect Cirque Corporation's operations, revenue, or customer commitments, and assigns each a
**Recovery Point Objective (RPO)**, **Recovery Time Objective (RTO)**, **Maximum Tolerable Period of
Disruption (MTPD)**, and **Recovery Level Objective (RLO)**.

Values are grounded in the organization's operating recovery capability — an off-site Proxmox Backup
Server (PBS) holding nightly backups of all virtual systems (03:00 daily, retained 7 daily / 4 weekly /
6 monthly), complemented by daily storage-level (ZFS) snapshots — and in the assessed business impact of
sustained disruption.

> **Draft note:** The RPO figure below is a *measured* capability. The RTO, MTPD, RLO, and financial-loss
> thresholds are *proposed* targets that become authoritative only upon the leadership sign-off in §6.

## 2. Recovery Objectives — Definitions & Standard

| Objective | Definition | Current standard |
|---|---|---|
| **RPO** — Recovery Point Objective | Maximum acceptable data loss (point back to which data is recovered). | **≤ 24 hours (measured)** |
| **RTO** — Recovery Time Objective | Target elapsed time to restore a function to its recovery level. | Per system (proposed) |
| **MTPD** — Maximum Tolerable Period of Disruption | Longest a function can be down before damage becomes unacceptable. | Per system (proposed) |
| **RLO** — Recovery Level Objective | Functional level restored within the RTO (full vs. minimum-viable). | Per system (proposed) |

## 3. Critical Functions, Objectives & Impact

| Business function / system | Tier | RPO | RTO | MTPD | RLO | Impact of sustained disruption |
|---|---|---|---|---|---|---|
| **Identity & Directory** (Active Directory / Entra) | 1 | ≤ 24 h | 4 h | 1 day | Full authentication | Organization-wide work stoppage — no logins, file, app, email, or VPN access. |
| **Network & Internet** (gateway, core switching, VPN) | 1 | N/A (config) | 4 h | 1 day | Connectivity + remote access | Site-wide loss of connectivity; no cloud services, email, or remote work. |
| **Email & Collaboration** (Microsoft 365) | 1 | ≈ 0 (cloud) | Provider SLA | 1 day | Mail, Teams, SharePoint | Loss of customer communication and order intake; internal coordination halts. |
| **ERP — Order & Finance** | 1 | ≤ 24 h | 24 h | 2 days | Order entry + invoicing | Cannot process/fulfill orders, invoice, or run finance — direct revenue impact. |
| **File Services** (engineering & business shares) | 2 | ≤ 24 h | 24 h | 3 days | Read/write to shared data | Engineering and operations productivity loss; project and delivery delays. |
| **Engineering / Dev Systems** (source, build, license, EDA) | 2 | ≤ 24 h | 24–48 h | 3–5 days | Development + build capability | R&D and firmware/hardware development halted; product timelines slip. |
| **IT Management & Monitoring** (asset/RMM tooling) | 3 | ≤ 24 h | 3 days | 5+ days | IT visibility & control | Reduced IT oversight and automation; not directly customer-facing. |

## 4. Financial-Loss Thresholds

Accepted tolerance thresholds — the points at which a sustained IT disruption is judged to begin causing
material financial loss.

- **Internal (significant business impact) — ≈ 1 business day.** Sustained loss of Tier-1 systems
  (identity, network, email, ERP) begins to cause significant internal financial impact through halted
  operations and lost productivity at roughly one business day of disruption.
- **Customer (client financial loss) — ≈ 2–3 business days.** Customer-facing financial impact — missed
  order fulfillment, delivery and support-commitment failures, and potential contractual/SLA exposure —
  is estimated to materialize within two to three business days of sustained disruption.

## 5. Operational Dependency on IT

Cirque's operations are **substantially (High) dependent** on IT. Core value-delivery processes — order
intake/fulfillment (ERP), product engineering and firmware/hardware development, file and data services,
shipping/logistics, finance, and all internal and customer communication (M365) — run on or through IT.
Discrete physical/manual steps may continue briefly, but the surrounding order, engineering, test, and
shipping processes cannot operate without IT.

## 6. Acceptance (required to make this authoritative)

| Role | Name / Title | Signature | Date |
|---|---|---|---|
| Prepared by | IT Operations | | 2026-08-31 |
| Reviewed by | | | |
| Accepted by (Executive) | | | |

---
*Security-questionnaire mapping — this document supports: Q47 (BIA executed defining MTPD/RTO/RLO/RPO —
answer Yes on acceptance); Q47a RPO = 24 hours; Q47b internal loss ≈ 1 day; Q47c customer loss ≈ 2–3 days.*
