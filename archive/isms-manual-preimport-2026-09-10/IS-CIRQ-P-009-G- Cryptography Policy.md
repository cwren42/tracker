**IS-CIRQ-P-009-G: Cryptography Policy**

**Document: IS-CIRQ-P-009-G**

**Standards Name: Cryptography Policy**

**Category: IT Security Related**

**Division: Policy**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** Executive Committee

**1. Purpose**

The purpose of this policy is to establish Cirque\'s requirements for
the effective and appropriate use of cryptographic controls to protect
the confidentiality, integrity, and authenticity of information. This
policy aligns with ISO/IEC 27001:2022 Annex A.8.24, ensuring that
cryptographic solutions are implemented to mitigate risks to information
assets throughout their lifecycle, including storage, processing, and
transmission.

**2. Scope**

This policy applies to all Cirque personnel (employees, contractors),
and to all information, information systems, applications, and
communications, where cryptographic controls are deemed necessary based
on risk assessments, legal, regulatory, or contractual requirements.
This includes information related to intellectual property (e.g., CAD
drawings, firmware, ASIC designs), customer data, and general business
operations, regardless of its location (on-premises, cloud, mobile
devices).

**3. Principles of Cryptography Use**

Cirque\'s use of cryptography is guided by the following principles:

-   **Risk-Based Approach:** Cryptographic controls shall be applied
    where justified by information security risk assessments and the
    classification of information assets (e.g., Confidential, Internal
    Use).

-   **Strength and Appropriateness:** Cryptographic algorithms, key
    lengths, and protocols shall be of appropriate strength for the
    sensitivity and longevity of the information they protect, and
    aligned with industry best practices and standards.

-   **Key Management:** Cryptographic keys shall be securely generated,
    stored, distributed, used, backed up, and recovered, and destroyed.

-   **Compliance:** The use of cryptography shall comply with all
    applicable legal, regulatory, and contractual obligations.

-   **Transparency and Auditability:** The implementation and use of
    cryptographic controls shall be documented and auditable.

**4. Requirements for Cryptographic Controls**

**4.1. Data at Rest Encryption:** a. **Sensitive Data Storage:**
Information classified as \"Confidential\" or \"Internal Use\" shall be
encrypted when stored on endpoints (laptops, workstations), file
servers, or portable media. \* **Endpoints:** Full Disk Encryption (FDE)
shall be mandated for all company-issued laptops and workstations,
managed via **Intune**. \* **File Servers:** Sensitive data stored on
network file shares shall be encrypted at rest. \* **Cloud Storage:**
Data stored in cloud services (e.g., Microsoft 365 SharePoint) shall
leverage the native encryption capabilities provided by the service
provider, with appropriate configuration. \* **Databases:** Databases
containing \"Confidential\" information (e.g., customer data, financial
records) shall utilize encryption at rest mechanisms (e.g., Transparent
Data Encryption - TDE). b. **Intellectual Property:** All intellectual
property, including CAD drawings, firmware, software code stored in
**GitLab**, and ASIC designs developed using **Cadence**, must be stored
encrypted, both at rest and in transit, within designated secure
repositories.

**4.2. Data in Transit Encryption:** a. **Remote Access:** All remote
access to Cirque\'s internal networks shall be secured using strong
cryptographic protocols (e.g., IPsec VPN, TLS 1.2 or higher). b.
**External Communications:** Communications with external parties
involving \"Confidential\" or \"Internal Use\" information (e.g., email,
file transfers) shall be encrypted. This includes: \* **Email:** Use of
secure email gateways enforcing TLS for transit or end-to-end encryption
for highly sensitive content. \* **Website/Web Applications:** All
Cirque websites and web-based applications that collect or transmit
sensitive information must use HTTPS with strong TLS protocols. \*
**File Transfers:** Secure file transfer protocols (e.g., SFTP, FTPS
over TLS, secure cloud-based file sharing services with encryption)
shall be used for transferring sensitive files externally.

**4.3. Cryptographic Key Management:** a. **Key Generation:**
Cryptographic keys shall be generated using approved, cryptographically
strong random number generators. b. **Key Storage:** Keys shall be
stored securely, segregated from the encrypted data, and protected
against unauthorized access, disclosure, or modification (e.g., hardware
security modules (HSMs), secure key vaults, encrypted containers). c.
**Key Distribution:** Key distribution shall be performed through
secure, authenticated channels. d. **Key Usage:** Keys shall be used
only for their intended purpose and by authorized entities. e. **Key
Backup and Recovery:** Procedures for secure backup and recovery of
cryptographic keys shall be established and tested. f. **Key
Destruction:** Keys shall be securely destroyed when they are no longer
required. g. **Key Rotation:** Critical cryptographic keys shall be
rotated periodically as defined by industry best practices and risk
assessments.

**4.4. Cryptographic Algorithm Selection:** a. Cirque shall use
cryptographic algorithms, key lengths, and protocols that are recognized
as strong and secure by current industry standards (e.g., NIST, FIPS
140-2 validated modules). b. Deprecated or weak cryptographic algorithms
(e.g., MD5, SHA-1 for digital signatures, DES, RC4, SSLv2/v3, TLS
1.0/1.1) shall not be used. The IT Manager is responsible for
identifying and mitigating their use.

**4.5. Cryptographic Controls for Authentication:** a. Cryptographic
techniques (e.g., hashing, digital signatures, strong authentication
protocols) shall be used to protect authentication information (e.g.,
passwords) and verify user identities. b. Multi-Factor Authentication
(MFA) will be used for privileged access and remote access.

**5. Responsibilities**

-   **IT Manager:** Responsible for the overall implementation,
    management, and oversight of cryptographic controls, including
    selection of algorithms, key management, and ensuring compliance
    with this policy.

-   **System Administrators / IT Personnel:** Responsible for
    configuring and maintaining cryptographic solutions as per this
    policy and related procedures.

-   **All Personnel:** Responsible for adhering to policies and
    procedures related to the use of encrypted systems and data (e.g.,
    ensuring full disk encryption is active, using VPN for remote
    access).

**6. Related Documents**

-   IS-CIRQ-P-001-G: Information Security Policy

-   IS-CIRQ-P-007-G: Asset Management Policy

-   IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure

-   IS-CIRQ-P-008-G: Access Control Policy

-   IS-CIRQ-PR-008-G: Access Control Procedure

-   IS-CIRQ-PR-009-G: Privileged Access Management Procedure

**7. Policy Review**

This policy will be reviewed at least annually, or sooner if significant
changes occur to Cirque\'s IT environment, the threat landscape, or new
cryptographic vulnerabilities are discovered.
