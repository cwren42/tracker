**IS-CIRQ-PR-012-G: Equipment Security Procedure**

**Document: IS-CIRQ-PR-012-G**

**Standards Name: Equipment Security Procedure**

**Category: Physical and Environmental Security**

**Division: Procedure**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

The purpose of this procedure is to define the systematic process for
protecting Cirque\'s IT equipment from theft, damage, unauthorized
access, and environmental hazards throughout its lifecycle. This
procedure implements IS-CIRQ-P-010-G: Physical and Environmental
Security Policy and IS-CIRQ-P-007-G: Asset Management Policy, ensuring
the continued confidentiality, integrity, and availability of
information processed, stored, or transmitted by these assets.

**2. Scope**

This procedure applies to all Cirque-owned or leased IT equipment,
including but not limited to:

-   Servers, network devices, and data storage systems.

-   Workstations, laptops, and mobile devices.

-   Manufacturing equipment with IT components or data storage.

-   Peripherals (printers, scanners, external drives).

-   Cables, power supplies, and environmental control equipment (e.g.,
    UPS units).

This procedure covers equipment located in Cirque\'s offices (US and
Taipei), remote work locations, and equipment in transit.

**3. Responsibilities**

-   **IT Manager:** Overall owner of this procedure. Responsible for
    implementing and overseeing equipment security controls, maintaining
    the equipment inventory, and managing equipment disposal processes.

-   **All Employees/Contractors:** Responsible for safeguarding
    company-issued equipment entrusted to them and adhering to all
    equipment security policies and procedures.

-   **Facilities Management:** Responsible for physical environmental
    controls (HVAC, fire suppression) and general building security in
    coordination with IT.

**4. Procedure**

**4.1. Equipment Acquisition and Inventory** a. **Procurement:** All IT
equipment shall be procured through approved channels, ensuring
compatibility with Cirque\'s security standards. b. **Asset Tagging:**
Upon acquisition, all new IT equipment shall be immediately tagged with
a unique asset ID and recorded in the **Information Asset Inventory (RMM
and Intune)**, as per IS-CIRQ-PR-007-G: Asset Classification and
Handling Procedure. c. **Initial Configuration:** Equipment shall be
configured securely before deployment, including changing default
passwords, applying security baselines (via **Intune** for endpoints),
and installing necessary security software (**Windows Defender for
Business**).

**4.2. Equipment Placement and Protection** a. **Secure Location:** All
critical IT equipment (e.g., servers, network devices, sensitive
manufacturing equipment) shall be placed in designated secure areas with
restricted physical access (e.g., server rooms, locked cabinets) as
defined in IS-CIRQ-PR-011-G: Physical Access Control Procedure. b.
**Environmental Protection:** Equipment in secure areas shall be
protected from environmental threats: \* **Temperature/Humidity:**
Ensure HVAC systems maintain optimal operating conditions. \* **Fire:**
Position equipment away from flammable materials; ensure fire detection
and suppression systems are in place and regularly tested. \* **Water:**
Avoid placing equipment directly under water pipes or in areas prone to
leaks. c. **Power Protection:** All critical equipment shall be
connected to Uninterruptible Power Supplies (UPS) and surge protectors
to guard against power fluctuations and outages. UPS systems shall be
regularly tested. d. **Visual Protection:** Equipment displaying
sensitive information (e.g., monitors in public view) shall be
positioned or equipped with privacy screens to prevent unauthorized
viewing. e. **Physical Securing:** Servers and critical network
equipment shall be physically secured within racks. Laptops issued to
employees are equipped with full disk encryption (managed by Intune) to
mitigate data breach risk if stolen.

**4.3. Equipment Maintenance** a. **Authorized Personnel:** Only
authorized Cirque IT personnel or approved third-party vendors shall
perform maintenance on IT equipment. b. **Supervision:** Third-party
maintenance personnel shall be supervised by an Cirque employee when
accessing secure areas or sensitive equipment. c. **Security Controls:**
All existing security controls (e.g., access controls, encryption) shall
remain in place and operational during and after maintenance activities.
Any temporary disabling of controls must be documented and immediately
re-enabled. d. **Logging:** Maintenance activities on critical equipment
shall be logged, including date, time, personnel involved, and actions
taken.

**4.4. Off-Site Equipment and Remote Work** a. **Company-Issued
Equipment:** Employees working remotely shall use only company-issued
and managed equipment (e.g., laptops managed by **Intune** with FDE and
**Windows Defender for Business**). b. **Secure Connectivity:** Remote
access to Cirque\'s network and systems must only occur via secure VPN
connections. c. **Physical Security at Remote Locations:** Employees are
responsible for the physical security of company equipment at remote
locations, including: \* Keeping equipment in secure locations when not
in use. \* Not leaving equipment unattended in public places. \*
Reporting lost or stolen equipment immediately to the IT Manager.

**4.5. Equipment Removal and Transfer** a. **Authorization:** No IT
equipment shall be permanently removed from Cirque premises or
transferred between offices without explicit authorization from the IT
Manager. b. **Documentation:** All equipment movements shall be
documented in the asset inventory. c. **Security During Transit:**
Sensitive equipment in transit shall be protected appropriately (e.g.,
secure packaging, tracking, encryption of data if present).

**4.6. Secure Equipment Disposal and Re-use** a. **Data Sanitization:**
Before disposal, re-assignment, or return to a vendor, all storage media
(hard drives, SSDs, USBs, mobile devices) on the equipment must be
securely sanitized or destroyed. This includes: \*
**Confidential/Internal Use Data:** Wiping using industry-recognized
methods (e.g., NIST SP 800-88 guidelines for media sanitization) or
physical destruction (e.g., shredding, degaussing) to render data
irretrievable. \* **Endpoint Data:** For devices managed by **Intune**,
remote wipe capabilities can be used prior to physical destruction. b.
**Physical Destruction:** For equipment no longer functional or deemed
beyond secure data sanitization, physical destruction methods may be
employed (e.g., shredding, crushing). c. **Certification:** Cirque shall
engage certified IT asset disposition (ITAD) vendors for large-scale
equipment disposal where appropriate, ensuring chain of custody and data
destruction certificates. d. **Asset Inventory Update:** The disposal of
equipment shall be promptly recorded in the **Information Asset
Inventory**.

**5. Clear Desk and Clear Screen** a. All personnel shall adhere to a
\"Clear Desk Policy\" (as per IS-CIRQ-P-010-G) by securing sensitive
documents and removable media in locked drawers/cabinets when not in use
or when leaving their workspace. b. All personnel shall adhere to a
\"Clear Screen Policy\" (as per IS-CIRQ-P-010-G) by locking their
computer screens when leaving their workstation unattended.

**6. Review and Update**

This procedure will be reviewed at least annually, or sooner if there
are changes to Cirque\'s IT equipment, physical security infrastructure,
or legal/regulatory requirements.

**7. Related Documents**

-   IS-CIRQ-P-010-G: Physical and Environmental Security Policy

-   IS-CIRQ-PR-011-G: Physical Access Control Procedure

-   IS-CIRQ-P-007-G: Asset Management Policy

-   IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure

-   IS-CIRQ-P-008-G: Access Control Policy

-   IS-CIRQ-PR-008-G: Access Control Procedure
