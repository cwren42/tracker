**IS-CIRQ-F-004-G: Asset Register**

**Document: IS-CIRQ-F-004-G**

**Standards Name: Asset Register**

**Category: Information Management Regulations**

**Division: Form**

**Standard Retention: Exist and No Corrections**

**Standard Type: Global**

**Version:** 1.0 **Effective Date:** 2025-07-01 **Review Date:**
2026-07-01 **Approved By:** IT Manager

**1. Purpose**

This **Asset Register** serves as a centralized record for identifying,
classifying, and tracking all information assets relevant to Cirque\'s
Information Security Management System (ISMS). It supports the
requirements of IS-CIRQ-P-007-G: Asset Management Policy and
IS-CIRQ-PR-007-G: Asset Classification and Handling Procedure, enabling
effective risk management and application of security controls.

**2. Scope**

This register applies to all information assets owned, leased, or under
the custody of Cirque, including hardware, software, data, services, and
physical locations within the ISMS scope (US, Japan, China).

**3. Instructions for Use**

-   This register is primarily maintained by the **IT Manager**,
    leveraging data from the **RMM system and Intune** as authoritative
    sources.

-   Manual entries should be made for assets not automatically detected
    (e.g., specific data sets, cloud services, physical documents,
    critical physical office spaces).

-   Ensure all fields are completed accurately for each asset.

-   This register will be reviewed and updated periodically as new
    assets are acquired/created, or existing assets undergo significant
    changes or are disposed of.

**4. Information Asset Register**

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  Asset ID     Asset Type   Description   Location     Manufacturer/Vendor   Model/Version   Serial           Asset Owner               Assigned Classification  Date               Last Review  Disposal Date Notes (e.g.,
                                                                                             Number/License   (Department/Individual)   (Confidential/Internal   Acquired/Created   Date         (if           critical system,
                                                                                             Key                                        Use/Public)                                              applicable)   specific software
                                                                                                                                                                                                               dependencies)
  ------------ ------------ ------------- ------------ --------------------- --------------- ---------------- ------------------------- ------------------------ ------------------ ------------ ------------- -----------------
  **Example                                                                                                                                                                                                    
  Entries:**                                                                                                                                                                                                   

  HW-SRV-001   Server       Production    US Office -  Dell                  PowerEdge R650  ABC123DEF456     IT Department             Confidential             2024-01-15         2025-06-15                 Hosts critical
               (Physical)   Web Server    Server Room                                                                                                                                                          customer-facing
                                                                                                                                                                                                               applications.

  SW-DB-001    Database     Customer      Azure Cloud  Microsoft             SQL Server 2019 N/A              Sales Department          Confidential             2023-09-01         2025-06-15                 Contains
                            Database                                                                                                                                                                           sensitive
                                                                                                                                                                                                               customer PII.

  IP-CAD-001   Data (IP)    ASIC Design   GitLab /     N/A                   V1.2            N/A              Engineering Department    Confidential             2024-03-20         2025-06-15                 Core intellectual
                            Files         Fileserver                                                                                                                                                           property.
                                                                                                                                                                                                               Integrated with
                                                                                                                                                                                                               Cadence.

  AP-OMN-001   Software     Omnify ERP    Cloud        Omnify                Latest          Subscription     Operations Department     Internal Use             2023-01-01         2025-06-15                 Critical for
               (SaaS)       System                                                           ID:XYZ                                                                                                            manufacturing
                                                                                                                                                                                                               process and
                                                                                                                                                                                                               inventory.

  PHY-US-001   Physical     US Office     South        N/A                   N/A             N/A              Operations/Facilities     Confidential             N/A                2025-06-15                 Main corporate
               Site         Building      Jordan, UT                                                                                                                                                           and engineering
                                                                                                                                                                                                               office.

  **New                                                                                                                                                                                                        
  Entries:**                                                                                                                                                                                                   

                                                                                                                                                                                                               

                                                                                                                                                                                                               
  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
