**IS-CIRQ-PR-016-G: Network Security Management Procedure**

**Document: IS-CIRQ-PR-016-G**

**Standards Name: Network Security Management Procedure**

**Category: IT Security Related**

**Division: Procedure**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

The purpose of this procedure is to define the systematic process for
managing the security of Cirque\'s networks, including wired, wireless,
and remote access connections. This procedure aims to protect network
services and information from unauthorized access, misuse, disclosure,
or disruption, in accordance with IS-CIRQ-P-011-G: Operations Security
Policy and ISO/IEC 27001:2022 Annex A.8.19.

**2. Scope**

This procedure applies to all Cirque network infrastructure, devices,
and services across all locations, including the US, Japan, and China
offices, and remote access points. This includes, but is not limited to:

-   Network devices (routers, switches, firewalls, wireless access
    points).

-   Network services (DNS, DHCP).

-   Network protocols.

-   Network cabling.

-   Virtual Private Network (VPN) solutions.

-   Cloud network configurations (e.g., Azure VNETs).

**3. Responsibilities**

-   **IT Manager:** Overall owner of this procedure. Responsible for
    network architecture, security design, approval of network changes,
    and oversight of network security monitoring.

-   **System Administrators / IT Personnel:** Responsible for
    implementing, configuring, maintaining, and monitoring network
    devices and services in accordance with this procedure.

-   **All Personnel:** Responsible for adhering to network usage
    policies, especially concerning remote access.

**4. Procedure**

**4.1. Network Segmentation and Architecture** a. Cirque networks shall
be logically segmented to isolate different functional areas, sensitive
data, and system classifications (e.g., corporate network, production
network, development network, guest wireless). b. Network segmentation
shall be enforced using firewalls and VLANs (Virtual Local Area
Networks). c. Network diagrams, illustrating segmentation and key
security zones, shall be maintained and regularly updated.

**4.2. Firewall Management** a. **Firewall Deployment:** Firewalls shall
be deployed at critical network boundaries, including the perimeter
between Cirque\'s internal network and external networks (e.g., the
Internet) and between network segments. b. **Rule Management:** \*
Firewall rules shall be explicitly defined, approved by the IT Manager,
and based on a least-privilege principle (deny-all by default,
permit-by-exception). \* Rules shall be documented, including
justification, source/destination IP, port, and protocol. \* Rules shall
be regularly reviewed (at least quarterly) and unnecessary rules
removed. c. **Secure Configuration:** Firewalls shall be securely
configured, with default passwords changed, unnecessary services
disabled, and management interfaces protected. d. **Logging:** Firewall
logs shall be enabled and forwarded to a centralized logging system as
per IS-CIRQ-PR-015-G: Logging and Monitoring Procedure.

**4.3. Secure Network Device Configuration** a. **Hardening:** All
network devices (routers, switches, wireless access points) shall be
hardened according to industry best practices and vendor
recommendations. This includes: \* Changing all default passwords. \*
Disabling unnecessary services and ports. \* Implementing strong
authentication and authorization for device management. \* Restricting
management access to designated administrative networks or hosts. b.
**Patch Management:** Network device firmware and operating systems
shall be regularly updated with security patches. c. **Configuration
Backup:** Device configurations shall be regularly backed up and stored
securely.

**4.4. Wireless Network Security** a. **Strong Encryption:** Wireless
networks used for corporate access shall utilize strong encryption
protocols (e.g., WPA2 Enterprise or WPA3) with EAP-TLS or PEAP for user
authentication. b. **Separate Guest Network:** A separate, isolated
guest wireless network shall be provided for visitors, ensuring it has
no access to Cirque\'s internal corporate or production networks. c.
**Authentication:** All wireless access points shall require strong
authentication.

**4.5. Remote Access Security (VPN)** a. **Mandatory VPN:** All remote
access to Cirque\'s internal networks and resources shall be conducted
exclusively through an approved Virtual Private Network (VPN) solution.
b. **Strong Encryption:** The VPN solution shall use strong
cryptographic protocols (e.g., IPsec, OpenVPN, or TLS VPN) for data in
transit encryption as per IS-CIRQ-P-009-G: Cryptography Policy. c.
**Multi-Factor Authentication (MFA):** MFA shall be mandatory for all
VPN connections. d. **Device Compliance:** Only company-managed devices
(e.g., laptops managed by **Intune**) with current security software
(**Windows Defender for Business**) and up-to-date patches shall be
permitted to connect via VPN.

**4.6. Network Monitoring** a. **Traffic Analysis:** Network traffic
shall be monitored for suspicious activity, intrusions, and anomalies
using network intrusion detection/prevention systems (IDS/IPS) or
similar tools. b. **Bandwidth Monitoring:** Network bandwidth usage
shall be monitored to ensure adequate capacity and detect unusual
traffic patterns. c. **Alerting:** Alerts shall be configured for
critical network security events and performance issues.

**4.7. Network Service Security** a. **DNS Security:** DNS services
shall be configured securely, and internal DNS servers protected from
external access. b. **DHCP Security:** DHCP services shall be managed to
prevent unauthorized IP address assignment. c. **Time Synchronization:**
Network devices shall be synchronized to a central, reliable time source
(NTP) to ensure accurate logging.

**4.8. Segregation of Duties for Network Administration** a. Where
feasible, duties related to network administration (e.g., firewall rule
changes, router configuration) shall be segregated to prevent a single
individual from being able to compromise network security without
detection.

**5. Review and Update**

This procedure will be reviewed at least annually, or sooner if there
are significant changes to Cirque\'s network architecture, security
technologies, or the threat landscape.

**6. Related Documents**

-   IS-CIRQ-P-011-G: Operations Security Policy

-   IS-CIRQ-P-008-G: Access Control Policy

-   IS-CIRQ-PR-008-G: Access Control Procedure

-   IS-CIRQ-P-009-G: Cryptography Policy

-   IS-CIRQ-PR-015-G: Logging and Monitoring Procedure

-   IS-CIRQ-PR-013-G: Change Management Procedure
