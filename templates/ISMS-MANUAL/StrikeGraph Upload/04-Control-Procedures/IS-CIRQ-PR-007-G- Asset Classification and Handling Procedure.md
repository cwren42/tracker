**IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure**

**Document: IS-CIRQ-PR-007-G**

**Standards Name: Asset Classification and Handling Procedure**

**Category: Information Management Regulations**

**Division: Procedure**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

The purpose of this procedure is to describe the systematic process for
classifying, handling, and protecting information assets at Cirque, in
accordance with IS-CIRQ-P-007-G: Asset Management Policy. This ensures
that appropriate security controls are applied throughout the asset\'s
lifecycle, from creation/acquisition to disposal.

**2. Scope**

This procedure applies to all Cirque personnel (employees, contractors)
and to all information assets within the defined ISMS scope, regardless
of type, format, or location.

**3. Responsibilities**

-   **IT Manager:** Manages the authoritative asset inventory
    (RMM/Intune), oversees the classification process, and ensures
    technical controls are implemented for assets.

-   **Asset Owners (e.g., Department Managers):** Responsible for
    initially classifying their owned assets, ensuring adherence to
    handling requirements, and reviewing classifications periodically.

-   **All Personnel:** Responsible for understanding and adhering to the
    asset classification and handling requirements relevant to their
    roles.

**4. Procedure**

**4.1. Asset Identification and Inventory Management**

a\. **Initial Identification:** When a new information asset is acquired
or created (e.g., new server, software license, significant new data
set), it shall be identified and recorded.

b\. **Automated Tagging (for endpoints/servers):** For IT-managed
hardware and software, the **RMM system and Intune agents will
automatically tag and inventory assets**. The IT Manager will ensure
these systems are configured for comprehensive asset discovery.

c\. **Manual Addition:** For assets not automatically detected (e.g.,
specific data sets, cloud services, physical documents, critical
physical office spaces), the Asset Owner or IT Manager shall manually
add them to the central asset inventory.

d\. **Asset Inventory Maintenance:** The IT Manager shall ensure the
asset inventory, as managed by RMM/Intune, is regularly updated and
accurate.

e\. **Minimum Inventory Data:** Each asset record shall include, at
minimum: Unique Asset ID, Asset Type, Description, Location, Asset
Owner, Date Acquired/Created, and Initial Classification.

**4.2. Asset Ownership Assignment**

a\. For each identified asset, a clear **Asset Owner** shall be assigned
by the IT Manager in consultation with relevant Department Managers.

b\. The Asset Owner is responsible for the overall protection and
classification of the asset, while the IT Manager acts as the
operational custodian for IT-managed assets.

**4.3. Asset Classification**

a\. **Initial Classification:** The Asset Owner, with guidance from the
IT Manager, is responsible for assigning an initial classification level
(Confidential, Internal Use, Public) to each owned asset based on its
sensitivity, value, and criticality.

b\. **Classification Criteria:** The classification shall be determined
by assessing the potential impact of a compromise to the asset\'s
confidentiality, integrity, and availability, as defined in
IS-CIRQ-P-007-G: Asset Management Policy.

c\. **Documentation:** The assigned classification level shall be
recorded in the asset inventory.

d\. **Review:** Asset classifications shall be reviewed annually by the
Asset Owner or when significant changes to the asset\'s use, value, or
sensitivity occur.

**4.4. Asset Handling Requirements (Based on Classification)**

All personnel handling information assets must adhere to the following
controls based on the asset\'s classification:

**4.4.1. Confidential Information (e.g., CAD designs, firmware, ASIC
designs, customer sales data):**

a\. **Storage:** Must be stored in secured locations with restricted
access (e.g., encrypted network drives, secure cloud storage like
SharePoint with strict permissions, dedicated secured folders in GitLab
for code). Physical confidential documents must be in locked cabinets or
secure rooms.

b\. **Access:** Strict Need-to-Know basis. Access granted only after
formal approval by Asset Owner. Logical access controls (user
authentication, strong passwords, MFA) are mandatory.

c\. **Transmission:** Must be transmitted using secure, encrypted
channels (e.g., encrypted email, secure file transfer protocols, VPN for
internal access). Avoid public file-sharing services.

d\. **Processing:** Processed only on secured devices (e.g.,
company-managed workstations with Windows Defender for Business and
Intune policies). Do not process on personal devices unless specifically
authorized and secured.

e\. **Physical Handling:** Keep out of sight when not in use. Do not
leave unattended on desks. Shred or cross-shred physical documents when
no longer needed.

f\. **Remote Access:** Only via secure VPN and on company-approved
devices.

**4.4.2. Internal Use Information (e.g., internal procedures,
non-sensitive project plans):**

a\. **Storage:** Stored on company network drives, SharePoint, or
approved cloud services. Physical documents in general office areas but
not publicly accessible.

b\. **Access:** Access is restricted to Cirque employees and authorized
contractors on a need-to-know basis for internal business purposes.

c\. **Transmission:** Typically via internal email or company messaging
platforms (Teams). For external sharing, consider basic encryption if
content is not public.

d\. **Processing:** Processed on company devices. Exercise caution on
personal devices.

e\. **Physical Handling:** Keep in secure office areas. Shred physical
documents before discarding.

**4.4.3. Public Information (e.g., marketing materials, public website
content):**

a\. **Storage:** May be stored in publicly accessible folders or on the
company website.

b\. **Access:** No restrictions on access.

c\. **Transmission:** May be shared via any unencrypted method.

d\. **Processing:** Can be processed on any device.

e\. **Physical Handling:** No specific handling requirements beyond
general office waste.

**4.5. Physical Asset Handling and Protection (US and Taipei Offices)**

a\. **Entry Controls:** All visitors must sign in and be escorted by an
employee. Access badges for employees are mandatory.

b\. **Securing Areas:** Critical areas (e.g., server rooms,
manufacturing floors, design labs where CAD/ASIC work occurs) will have
stricter access controls (e.g., keycard access, biometric scanners if
applicable).

c\. **Clean Desk Policy:** All personnel must maintain a clean desk,
especially in areas where sensitive information is handled, ensuring no
confidential documents are left unattended.

d\. **Equipment Security:** Workstations and equipment will be
physically secured to prevent theft where practical.

e\. **Environmental Controls:** Monitoring for temperature, humidity,
and fire suppression in server rooms and critical manufacturing areas.

**4.6. Secure Disposal of Assets**

a\. **Data Sanitization:** \* **Electronic Media (Hard Drives, SSDs,
USBs):** Data on media classified as Confidential or Internal Use must
be securely wiped using industry-recognized methods (e.g., NIST SP
800-88 guidelines for sanitization) or physically destroyed (e.g.,
shredding, degaussing) prior to disposal or re-use. \* **Cloud Data:**
Ensure data is properly deleted from cloud services following service
provider\'s secure deletion processes.

b\. **Physical Documents:** Confidential and Internal Use hard copy
documents must be cross-shredded or incinerated when no longer required.

c\. **Hardware Disposal:** All hardware (laptops, servers, mobile
devices) must have their data-bearing components sanitized or destroyed
before being recycled, sold, or discarded. The IT Manager will oversee
this process.

**5. Review and Update**

This procedure will be reviewed at least annually, or sooner if there
are changes to IS-CIRQ-P-007-G: Asset Management Policy, changes in
asset types, new threats, or updates to legal/regulatory requirements.

**6. Related Documents**

-   IS-CIRQ-P-007-G: Asset Management Policy

-   Information Asset Inventory (RMM/Intune)

-   IS-CIRQ-PR-002-G: Information Security Risk Assessment Procedure

-   IS-CIRQ-PR-001-G: Access Control Procedure)

-   IS-CIRQ-P-008-G: Acceptable Use Policy
