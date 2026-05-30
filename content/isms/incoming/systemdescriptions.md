IMPORTANT:  

This document comprises Section III of your organization's SOC 2 report; both the report and this document are Confidential. If a customer/client wishes to review your SOC 2 report, it is highly advised to get a signed NDA. Ultimately, it is your call if you’d like to share your SOC 2 report with or without a signed NDA; the decision should be made based on the sensitivity of the information included within this document. 

More guidance/information here.  

Document Revision Checklist: 

Carefully revise this template to tailor it to your organization. 

Update [COMPANY] with your company name. 

Update [SYSTEM NAME] with what you are calling your product/solution/service. 

Replace [VP of Engineering] with the appropriate job title of the owner of the IT Security function at your organization. 

Do not change the formatting, font, or color of any of the headers or black text on this document. The System Description will be copied directly into your final SOC 2 report, so it is important that the formatting of the document remains the same as this template. 

Note: The template includes formatting that may not translate correctly to Microsoft Word. 

Red text denotes instructions. 

Purple text denotes examples that should be carefully reviewed/revised then changed to black. 

Black text may be edited, but not removed. 

All content in the System Description is fair game to be audited, so ensure that it is an accurate description of your product and practices. It is OK to mention processes that we don’t have a control for in the Strike Graph Control Library, but if an auditor asks about anything described in this document, you must be able to provide evidence that the process/control exists.  

For a Type 1 report, please omit the section: System Changes During the Period. 

Write in the third person (use of ‘we’ is discouraged). 

Remove the Table of Contents prior to delivering this to the External Auditor. It is included for reference and organizational purposes only. 

Attach an editable or .docx copy of the final version of this document to the System Description evidence item within Strike Graph via a file upload or through automatic collection with the Google Drive integration. Please do not upload a PDF.  

 

​​Overview of Operations 

​Company Background 

​Overview of the System 

​Key Features of the [System Name] System 

​Principle Service Commitments and System Requirements 

​System Components 

​People 

​Data 

​Third Party Access 

​Infrastructure 

​Software 

​Procedures 

​Boundaries of the System 

​Complementary Subservice Organization Controls 

​Relevant Aspects of the Control Environment, Risk Assessment, Information and Communications, and Monitoring 

​Control Environment 

​Integrity and Ethical Values 

​Board/Owner/Management Oversight 

​Organizational Structure 

​Assignment of Authority and Responsibility 

​Commitment to Competence 

​Accountability 

​Controls 

​Security Management 

​Logical and Physical Access 

​Change Management 

​Data Backup and Disaster Recovery 

​Incident Response 

​Vendor Management 

​System Monitoring 

​Other TSCs 

​Information and Communications 

​Monitoring 

​Risk Assessment 

​Incidents in the Last 12 Months 

​System Changes During the Period 

​Complementary User Entity Controls​ 

 

 

[Optional]  

This System Description is intended to meet the common informational needs of: 

Select all applicable: 

Customers interested in using our products 

Existing users of our products for all or a portion of the reporting period 

Internal personnel 

Our service auditor  

The service auditors of the entities that interact with our products 

Other business partners that interact with our products 

Regulatory authorities, if needed. 

 

Overview of Operations 

Company Background 

Cirque Corporation was established in 1991 and is the original developer of capacitive sensing.  Our mission is to make innovative human machine interfaces in order to enable machines to see and feel the world.  We are headquartered in Salt Lake City, Utah with locations in Taipei TW and remote offices in Shanghai, Kunshan, Heifei, China and Austin, Texas.  We sell our capacitive sensing products in touchpads, touch screens and various other touch sensing devices integrated all over the world.   

Overview of the System  

Cirque Corporation provides capacitive touch products for various applications and environments.  Typically for things like touchpads and touchscreens, our capacitive touch products can include either just an IC that is integrated into the rest of a customer product or it can also include a full touchpad peripheral module that is a stand alone device.   

Key Features of the Capacitive Sensing System 

Cirque’s Touch IC platform (most notably Gen 6) contains the following key features: 

Ultra high SNR resulting in precise measurements and high noise immunity 

Full support for PTP compliant touchpads 

Ability to do proximity sensing 

Fully configurable RX/TX to allow for any size or aspect ratio within the given set of electrodes (up to 82 for the largest IC).   

Full solution support including creation of production test fixturing and equipment. 

Principle Service Commitments and System Requirements 

Cirque’s core product is a hardware component (touch IC or touch module).  Sometimes in addition to just the hardware component, Cirque can also provide design services to help facilitate the design in of the Cirque HW into a customer’s product.  Cirque has designed its processes and procedures related to the Capactive Sensing System (or the “System”) to meet its objectives as a touch solution sometimes including design services (“Services”). Those objectives are based on the service commitments that Cirque makes to its user entities and the operational and compliance requirements that it has established for the services. These commitments also take into consideration the security laws and regulations in the jurisdictions in which Cirque’s services are offered. 

Cirque establishes operational requirements that support the achievement of service commitments, relevant laws and regulations, and other system requirements. Such requirements are communicated in Cirque's system policies and procedures, system design documents, and contracts with customers. Information security policies define an organization-wide approach to how systems and data are protected. These include policies around how the service is designed and developed, how the system is operated, how the internal business systems and networks are managed and how employees are hired and trained. In addition to these policies, standard operating procedures have been documented on how to carry out specific manual and automated processes required in the operation and development of the System. 

This report is limited in scope to the Security  add others that are relevant: Availability, Confidentiality, Processing Integrity, and Privacy) Trust Services Criteria based on guidance from the AICPA. The controls that management has identified to meet each criteria are described in detail within the ‘Control Environment’ section of this System Description as well as in Section 4 of this report. They are not included here to eliminate the redundancy that would result from listing them in this section. Although the applicable Trust Services Criteria and related control activities are included in Section 4, they are, nevertheless, an integral part of Cirque’s description of the system. Any applicable Trust Services Criteria that are not addressed by control activities at Cirque are described within the ‘Complementary Subservice Organization Controls’ section below. 

Cirque’s principle service commitments and system requirements are: 

Trust Services Criteria 

Service Commitments 

System Requirements 

Confidentiality 

The Company will protect confidential information against any unauthorized use or disclosure to the same extent that the Company protects its own confidential information, but in no event will the Company use less than a reasonable standard of care to protect such confidential information. 

The Company will use confidential information solely for the purpose for which it was disclosed. 

Encryption of data at rest 

Data classification 

Data management 

Privacy 

The Company will protect personal information against any unauthorized use or disclosure to the same extent that the Company protects personal information under its control, but in no event will the Company use less than a reasonable standard of care to protect such personal information. 

The Company will use personal information solely for the purpose for which it was disclosed. 

The Company will comply with relevant privacy laws and regulations of the jurisdictions for which its services are offered. 

Data subject rights as described in our Privacy Policy 

Internal Privacy Policy 

Encryption of data at rest 

Data management 

 

System Components 

The Capative Sensing System platform is designed, implemented, and operated to achieve specific business objectives in accordance with management-specified requirements. The purpose of this system description is to delineate the boundaries of the system, which includes the services outlined above and the following components, described below: people, data, infrastructure, software, and procedures.  

People 

Cirque is organized into functional areas. Within these functional areas, organizational and reporting hierarchies have been defined, and responsibilities have been assigned. Responsibilities for specific roles are clearly defined with job descriptions. The organizational structure provides the framework within which its activities for achieving entity-wide objectives are planned, executed, controlled and monitored. 

Leadership – responsible for setting the company’s strategic goals and managing company-wide activities.  

Engineering – responsible for developing features and supporting the platform. Responsible for incident management. 

Project Management-responsible for ensuring that a customer project is completed on time, to spec, within budget.  

Quality-responsible for ensuring that all designs are able to meet the required customer criteria before release.  Also responsible for ensuring that all production limits are set to ensure that the all products produced will also be in spec.   

 

 

Data 

The sensitive data that comes into Cirque is always related to a customer’s product design.  When a customer wishes to consider Cirque for a product design they will give Cirque an overview or requirements describing what they need and how it will integrate with Cirque’s Capacitive Sensing System.  Any customer data is immediately stored only on a customer’s project site and access to that data is only given to sales, project managers, or engineers who have a need to access it.   

After a project is completed it will be archived so the data cannot be changed.  Three years after a project is archived it is deleted.   

Third Party Access 

No third-party providers have access to our data. 

Infrastructure 

The primary infrastructure supporting the Cirque Capacitive Sensing system comprises a Microsoft 365 One Drive location that is private and secured for only Cirque access.   

 

 

 

Procedures 

 

Both automated and manual processes have been established by the organization to support the operation of the System. These include procedures through which services activities are initiated, authorized, performed, and delivered. Management has developed policies that establish the organization's overall approach to internal controls related to security and operational processes. These policies comply with overall business objectives and are aimed to minimize risk through preventive measures, timely identification of irregularities, limitation of losses, and timely restoration.  

The organization has established control activities, based on policies that are carried out through various procedures. These procedures include, but are not limited to:  

Oversight, selection, documentation, implementation and monitoring of security controls. 

Authorization, changes to, and termination of information system access. 

Maintenance and support of the security system and necessary backup and offline storage. 

Governance and processes for change management. 

Incident response guidelines and processes. 

Vendor oversight and processes to mitigate vendor risk. 

IT and operational risk management. 

Boundaries of the System 

The people, data, infrastructure, software, and procedures described above establish the system boundaries for our SOC 2 examination. 

 

  

 

Complementary Subservice Organization Controls 

 

No subservice organization controls are relevant to Cirque’s Capacitive Sensing System.  

Relevant Aspects of the Control Environment, Risk Assessment, Information and Communications, and Monitoring 

 

Control Environment 

Cirque's control environment sets the tone of the organization and influences the control consciousness of its personnel. Some of the components of internal control include controls that have more of an effect at the entity level, while other components include controls that are primarily related to specific processes or applications. The control environment includes controls that may have a pervasive effect on the organization, an effect on specific processes, as well as security controls intended to effectively protect client data and provide a stable environment for the security of Cirque’s client-facing services. The components of the control environment factors include the integrity and ethical values, management’s commitment to competence; its organizational structure; the assignment of authority and responsibility; and the oversight and direction provided by executive management and operations management. 

Integrity and Ethical Values 

Integrity and ethical values are essential elements of Cirque's control environment, affecting the design, administration, and monitoring of other components. Integrity and ethical behavior are the product of Cirque's ethical and behavioral standards, how they are communicated, and how they are reinforced in practice. They include management’s actions to remove or reduce incentives and temptations that might prompt personnel to engage in dishonest, illegal, or unethical acts. Specific control activities that Cirque has implemented in this area are: 

An employee handbook that outlines our policies 

Regular company wide security training 

NDAs for all employees 

Background checks of all employees 

Board/Owner/Management Oversight 

Cirque’s control consciousness is influenced significantly by the participation of its executive team. The executive team meets on a periodic basis to oversee operations management activities and to discuss and monitor related issues. Executive management meets and interacts with team members as a component of day-to-day operations to discuss business objectives and operational issues. 

Organizational Structure 

Cirque’s organizational structure provides the framework within which its activities for achieving entity‐wide objectives are planned, executed, controlled, and monitored. Cirque management believes that establishing a relevant organizational structure includes considering key areas of authority and responsibility and lines of reporting. Cirque is organized along functional areas. Within functional areas, organizational and reporting hierarchies have been defined and responsibilities have been assigned. 

Assignment of Authority and Responsibility 

Cirque’s assignment of authority and responsibility include factors such as how authority and responsibility for operating activities are assigned and how reporting relationships and authorization hierarchies are established. It also includes policies relating to business practices, knowledge and experience of key personnel, and resources provided for carrying out duties. In addition, it includes policies and communications directed at ensuring that personnel understand the entity’s objectives, know how their individual actions interrelate and contribute to those objectives, and recognize how and for what they will be held accountable. 

Commitment to Competence 

Cirque is committed to providing the highest quality professional and technological resources. This includes management’s consideration of the knowledge and skills necessary to accomplish tasks that define each employee’s roles and responsibilities. 

Accountability 

Cirque management philosophy and operating style encompass a broad range of characteristics. Such characteristics include management’s approach to taking and monitoring business risks, management’s attitudes and actions toward financial reporting, and management’s attitudes toward information processing, accounting functions and personnel. Management meetings are held frequently to address issues as they are brought to management’s attention. Cirque human resources policies and practices relate to employee hiring, orientation, training, evaluation, promotion, compensation, and disciplinary activities. Specific control activities that Cirque has implemented in this area include: 

Controls 

Security Management 

Management has developed information security policies and related procedures to govern the security program at Cirque. The Information Security Policy is maintained, reviewed and annually updated by the CEO. The development of an information security program, processes and procedures are the responsibility of the IT Manager. The Information Security Policies are reviewed and approved annually or as business needs change. Procedure documents related to access control and change management are updated as business needs change. 

These policies and procedures cover the following key security life cycle areas: 

Data classification. 

Assessment of the business impact resulting from proposed security approaches. 

Selection, documentation, and implementation of security controls. 

Authorization, changes to, and termination of information system access. 

Monitoring security controls. 

Management of access and roles. 

Maintenance and support of the security system and necessary backup and offline storage. 

Incident response. 

 

Logical and Physical Access 

Cirque maintains an office in Sandy, Utah and Taipei, Taiwan.  Access to either offices is secured by key card access and all key card access is logged by the Cirque IT department.   

Visitors must be accompanied by an employee or key card holder. Visitors can check-in via a kiosk in the office lobby. Employees are notified via email when a visitor has arrived. The building leased in Taiwan is monitored by 24/7 security guards and external doors are locked from 7pm - 8am.  The building leased in Utah has external doors always locked and both internal and external doors can only be accessed by key card 24/7. 

Change Management 

Cirque has a Change Management Policy which governs deliberate changes to the IT environment, including infrastructure, data, and software development. The Change Management policy governs the request, documentation, testing and approval of changes. All technology acquisition, development and maintenance processes are governed by change management procedures. The Change Management Policy is communicated to relevant personnel and updated annually, or as business needs require. The Quality Director is the owner of the Change Management Policy and is responsible for ensuring that changes to IT services are made in a manner appropriate to their impact on Company Operations. 

Cirque's product team utilizes Asana to manage specific changes throughout the change control processes. For any system change a change request form must be filled out by the party requesting the change and then an Asana project or Asana tasks will be created to ensure that the change is completed and verified.   

When tasks have been created, the project management team will match them with resources and ensure that they are completed on time and on budget.  Upon completion QA is required to test and approve all changes before release.   

While not all changes are SW or FW (some are hardware), executed SW or FW changes are developed on a separate branch of a project's git repository. When an engineer or resource has completed a ticket in their separate branch they create a "merge request" in the git repository. A merge request must be reviewed and approved by a separate engineer or resource. Once the request has been approved the merge request can be completed and the code is included in a development branch within the git repository. 

Data Backup and Disaster Recovery 

All design files and design data is stored on onsite servers at Cirque’s Sandy location.  This data is backed up to local backups nightly, and is also backed up regularly to the Taipei servers.  In addition to the backups in Taipei, there are also AWS backups that are done every two weeks to ensure that there are multiple off site locations for backup and recovery.   

Incident Response 

Cirque maintains a formal Security Incident Response Plan (SIRP), version 2.0, effective March 2, 2026, approved by the CTO and owned by the IT Manager. The SIRP is reviewed annually and tested through semi-annual tabletop exercises. Incident response guidelines are published and available to all employees; incident reporting procedures are included in the mandatory annual security awareness training program (supplemented by quarterly phishing simulations). 

Cirque uses its Tracker platform to centrally maintain, manage, and monitor assets, vulnerabilities, and patch management activity arising from incidents. A dedicated Incident portal houses the IR team's guided playbooks, incident register, and post-incident tracking. 

The SIRP follows the NIST SP 800-61 framework and includes: 

Definition of an incident — any event compromising the confidentiality, integrity, or availability of information systems, data, or physical assets, classified as SEV-1 (Critical) through SEV-4 (Low) 

Incident categories — 11 defined types including Malware, Phishing, Unauthorized Access, Data Breach, DoS/DDoS, Lost/Stolen Device, Physical Security, Insider Threat, Vendor Incident, Web Attack, and Account Compromise 

Employee responsibilities — all personnel are required to immediately report suspected incidents via security@cirque.com, the IT ticketing system, or directly to the IT Manager 

Notification and escalation procedures — SEV-1 requires CTO/CEO/Legal notification within 1 hour; SEV-2 requires immediate CTO/IT Manager notification; escalation matrices define on-call, backup responder, and regulatory notification timelines (GDPR 72 hrs, CCPA, state laws, PCI DSS 24 hrs) 

Containment — isolation of affected systems, account disabling, firewall blocking, and quarantine actions; incident-type-specific containment steps (Ransomware, Phishing, Data Breach, Lost/Stolen Device, DoS) 

Eradication — removal of malware, patching of exploited vulnerabilities, removal of unauthorized accounts, and verified clean scans with 72-hour minimum monitoring 

Recovery and restoration of services — restoring from clean Azure Backup, re-applying security configurations, phased network reconnection, and intensive 7–14 day post-recovery monitoring; return to production requires IT Manager + CEO approval 

Root cause analysis and post-incident review — formal Post-Incident Review (PIR) conducted within 7 days; final incident report with RCA, impact assessment, lessons learned, and corrective action items completed within 14 days and distributed to IT manager, CEO (SEV-1/2), and SOC 2 auditors as applicable 

The Incident portal provides four detailed guided playbooks actively used by the IR Team: Ransomware (14 steps), Phishing (10 steps), Data Breach (12 steps), and Lost/Stolen Device (8 steps). Incident tickets (format: INC-YYYY-NNNNN) are centrally tracked with status, timeline, affected assets, root cause, and corrective actions, feeding directly into SOC 2 evidence collection. 

 

Vendor Management 

The organization clearly defines vendor management roles, contract expectations and vendor risks in adherence to their Vendor Management Policy. Vendor management is overseen by the CEO. Formal contracts are utilized for vendor and business partner relationships; scope, responsibilities, compliance requirements and service levels (if required) are included in the contracts. 

Cirque performs due diligence activities over new vendors prior to contract execution and on an annual basis thereafter. Due diligence activities include an assessment of information security practices based on the assessed level of vendor risk. Third party SOC 2 reports are reviewed for impact to the company environment. 

System Monitoring 

Cirque's infrastructure and applications are monitored at multiple layers through a combination of automated tooling, centralized logging, and procedural controls governed by IS-CIRQ-PR-015-G (Logging and Monitoring Procedure) and the SOC 2 Log Retention and Review Standard. 

Endpoint & Antivirus Protection 

Microsoft Defender for Business is deployed on all company-issued workstations and servers, with virus definitions updated daily. Detections generate automatic alerts to the IT security team. Windows Defender alerts are collected and centrally monitored via Microsoft Intune, integrated with Microsoft 365 security features. 

Firewall & Network Monitoring 

Azure Firewall with IDS/IPS capabilities protects cloud-hosted systems. Network Security Groups (NSGs) enforce default-deny inbound rules with flow logs forwarded to Azure Log Analytics. DDoS Protection Standard is enabled on all public-facing Azure resources. VLANs segment departments; cross-VLAN traffic requires explicit firewall approval. 

Email Security 

Microsoft Defender for Office 365 scans all inbound and outbound email for malware, phishing, and spam prior to and upon delivery. O365 audit logs and message tracking are retained for 90 days. 

Centralized Logging & SIEM 

Azure Sentinel serves as the SIEM, aggregating logs from Azure Activity Logs, the Microsoft 365 Unified Audit Log, Active Directory, endpoint security events, physical access (Unifi Access system), and application audit trails. Logs are retained according to type: security event logs (90 days / 12 months for incidents), admin activity (12 months), and compliance data (immutable storage with legal hold capability). All systems synchronize time via NTP to ensure accurate cross-source log correlation. 

Log Review Cadence 

Daily: Critical system logs, firewall logs, privileged access logs, and SEV-1/SEV-2 alert queues 

Weekly: General system, application, and physical access logs; anomaly and trend review 

Monthly: Firewall anomaly review, vulnerability report to IT Manager and CEO, control effectiveness review 

Ad-hoc: In response to active alerts or incident investigations 

File Integrity & Configuration Monitoring 

File Integrity Monitoring via Azure Security Center covers Windows Servers (registry keys, system files, critical configs), with email and Microsoft Teams notifications on unauthorized changes. Linux servers run AIDE (Advanced Intrusion Detection Environment) nightly, alerting to security@cirque.com on any changes to etc, bin, sbin, bin, and boot. Intune compliance policies continuously monitor workstation configuration drift with deviations flagged for remediation within 48 hours. 

 

Vulnerability Scanning 

Weekly automated vulnerability scans are executed via Microsoft Defender for Cloud. Remediation SLAs are enforced: Critical (7 days), High (30 days), Medium (90 days), Low (next maintenance window). Results feed directly into Cirque's Tracker platform, which centrally manages vulnerabilities, patch status, and associated assets. 

Tracker Platform 

Cirque's internal Tracker platform provides centralized monitoring of assets, vulnerabilities, patch management, and SOC 2 control status. Automated monitoring profiles execute checks against enrolled assets and generate alerts routed to the Alert Center. Alerts are triaged by the IT security team and escalated via email notification based on severity. All actions are logged in an immutable audit trail used as SOC 2 evidence. 

Access & Privileged Activity Monitoring 

All successful and failed login attempts, privileged account usage, configuration changes, and unauthorized resource access attempts are logged across AD, Azure, and application layers. Automated alerts trigger on multiple failed logins, privilege escalation, and Active Directory changes, escalating immediately to the IT Manager for investigation. Privileged administrative connections require MFA and are individually logged. 

Other TSCs 

Information and Communications 

Information and communication are integral components of the Cirque internal control system. It is the process of identifying, capturing, and exchanging information in the time frame necessary to conduct, manage and control the entity’s operations. At Cirque, information is identified, captured, processed, and reported by various information systems, as well as through conversations with clients, service providers, and employees. 

Daily standups are held to discuss the status of the current sprint activities which may include security tasks. Departmental meetings are utilized to align team objectives with company objectives.  Additionally, email and Slack messages are used to communicate time-sensitive information. 

Because Cirque does not process any customer data other than the customer’s original design requests, the communication to the customer is owned by Sales and Project Management.  In the event that the customer needs communication regarding their product a member of the sales or project management team will communicate to the customer, typically through email, to give the customer the needed update.   

Monitoring 

Monitoring is a critical aspect of internal control in evaluating whether controls are operating as intended and whether they are modified as appropriate for changing conditions. Management has implemented monitoring controls to address timely and appropriate responses to issues that may impact information security. Automated systems (ex: IDS, firewall, vulnerability scans, patch alerts) are monitored for security events impacting Company systems and remediations are actioned as needed.  

In addition, Management monitors the quality of internal control performance as a normal part of their activities. They are heavily involved in day-to-day activities and regularly review various aspects of internal and customer-facing operations to: 

Determine if objectives are achieved. 

Identify any new risks that develop. 

Implement appropriate measures to address those risks.  

The monitoring process is achieved through several ongoing management oversight activities that include:    

Risk Assessment  

Management is responsible for identifying risks that threaten achievement of the service commitments and system requirements (described above). Management has implemented a process for identifying relevant risks that could affect the organization’s ability to meet its objectives.  

The risk assessment is carried out by management and occurs annually, or as business needs change. It includes risks that could act against the company's objectives and service commitments, as well as specific risks related to a compromise to the security of data. The level of each identified risk is determined by considering the impact of the risk itself and the likelihood of the risk materializing and high scoring risks are actioned upon. Risks are analyzed to determine whether the risk meets company risk acceptance criteria to be accepted or whether a mitigation plan will be applied. Mitigation plans include both the individual or department responsible for the plan and may include budget considerations.  

Management considers the following in its risk assessment: 

Risks that could impact the security of the organization’s IT environment. 

Risk of fraud. 

Vendor or supply chain risks. 

Risks to customer or employee data. 

Cross department risks that may impact security objectives. 

Identification and assessment of changes, such as environmental, regulatory, and technological changes that could significantly affect the system of internal control for security. 

 

Incidents in the Last 12 Months 

There have been no significant incidents related to a control failure or that impacted service commitments or system requirements, were required to be disclosed or had a material impact requiring disclosure. 

System Changes During the Period 

There were no changes that are likely to affect report users’ understanding of how the Capacitive Sensing System is used to provide the service during the period from [Start date] to [End date]. 

Complementary User Entity Controls 

No specific controls are required by the end customer to use Cirque’s capacitive sensing system as it is a HW based solution that does not require controls from the customer.   