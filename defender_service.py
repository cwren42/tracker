"""
Microsoft Defender for Endpoint Service
Fetches vulnerability, software, and security recommendation data
"""
import requests
import msal
from datetime import datetime
from app import Setting, db
import logging

logger = logging.getLogger(__name__)

class DefenderService:
    """Service for interacting with Microsoft Defender for Endpoint API"""
    
    def __init__(self):
        self.base_url = 'https://api.securitycenter.microsoft.com/api'
        self.token = None
        self._authenticate()
    
    def _authenticate(self):
        """Authenticate with Microsoft Defender API"""
        try:
            tenant_id = Setting.query.filter_by(key='m365_tenant_id').first().value
            client_id = Setting.query.filter_by(key='m365_client_id').first().value
            client_secret = Setting.query.filter_by(key='m365_client_secret').first().value
            
            authority = f"https://login.microsoftonline.com/{tenant_id}"
            app = msal.ConfidentialClientApplication(
                client_id,
                authority=authority,
                client_credential=client_secret
            )
            
            scopes = ['https://api.securitycenter.microsoft.com/.default']
            result = app.acquire_token_for_client(scopes=scopes)
            
            if 'access_token' in result:
                self.token = result['access_token']
            else:
                error = result.get('error_description', 'Unknown error')
                logger.error(f'Defender authentication failed: {error}')
                raise Exception(f'Authentication failed: {error}')
                
        except Exception as e:
            logger.error(f'Error authenticating with Defender API: {str(e)}')
            raise
    
    def _get(self, endpoint, params=None):
        """Make GET request to Defender API"""
        if not self.token:
            self._authenticate()
        
        url = f"{self.base_url}/{endpoint}"
        headers = {'Authorization': f'Bearer {self.token}'}
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f'Error fetching {endpoint}: {str(e)}')
            raise
    
    def get_machines(self):
        """Get all machines/devices from Defender"""
        try:
            data = self._get('machines')
            machines = data.get('value', [])
            logger.info(f'Fetched {len(machines)} machines from Defender')
            return machines
        except Exception as e:
            logger.error(f'Error fetching machines: {str(e)}')
            return []
    
    def get_vulnerabilities(self):
        """Get all vulnerabilities across the organization"""
        try:
            data = self._get('vulnerabilities')
            vulnerabilities = data.get('value', [])
            logger.info(f'Fetched {len(vulnerabilities)} vulnerabilities from Defender')
            return vulnerabilities
        except Exception as e:
            logger.error(f'Error fetching vulnerabilities: {str(e)}')
            return []
    
    def get_machine_vulnerabilities(self, machine_id):
        """Get vulnerabilities for a specific machine"""
        try:
            data = self._get(f'machines/{machine_id}/vulnerabilities')
            return data.get('value', [])
        except Exception as e:
            logger.error(f'Error fetching vulnerabilities for machine {machine_id}: {str(e)}')
            return []
    
    def get_software(self):
        """Get all software inventory"""
        try:
            data = self._get('Software')
            software = data.get('value', [])
            logger.info(f'Fetched {len(software)} software items from Defender')
            return software
        except Exception as e:
            logger.error(f'Error fetching software: {str(e)}')
            return []
    
    def get_incidents(self):
        """Get all security incidents"""
        try:
            data = self._get('incidents')
            incidents = data.get('value', [])
            logger.info(f'Fetched {len(incidents)} incidents from Defender')
            return incidents
        except Exception as e:
            logger.error(f'Error fetching incidents: {str(e)}')
            return []
    
    def get_alerts(self):
        """Get all security alerts"""
        try:
            data = self._get('alerts')
            alerts = data.get('value', [])
            logger.info(f'Fetched {len(alerts)} alerts from Defender')
            return alerts
        except Exception as e:
            logger.error(f'Error fetching alerts: {str(e)}')
            return []
    
    def get_recommendations(self):
        """Get security recommendations"""
        try:
            data = self._get('recommendations')
            recommendations = data.get('value', [])
            logger.info(f'Fetched {len(recommendations)} recommendations from Defender')
            return recommendations
        except Exception as e:
            logger.error(f'Error fetching recommendations: {str(e)}')
            return []
    
    def get_software_by_machine(self):
        """Get software inventory organized by machine"""
        try:
            machines = self.get_machines()
            software_by_machine = []
            
            for machine in machines:
                machine_id = machine.get('id')
                machine_name = machine.get('computerDnsName', 'Unknown')
                
                # Get software installed on this machine
                data = self._get(f'machines/{machine_id}/software')
                installed_software = data.get('value', [])
                
                for software in installed_software:
                    software_by_machine.append({
                        'machineName': machine_name,
                        'machineId': machine_id,
                        'osPlatform': machine.get('osPlatform', 'Unknown'),
                        'softwareName': software.get('name', 'Unknown'),
                        'softwareVendor': software.get('vendor', 'Unknown'),
                        'softwareVersion': software.get('version', 'Unknown'),
                        'installedMachines': software.get('installedMachines', 0),
                    })
            
            logger.info(f'Fetched software for {len(machines)} machines')
            return software_by_machine
        except Exception as e:
            logger.error(f'Error fetching software by machine: {str(e)}')
            return []
    
    def get_missing_kbs(self):
        """Get missing Windows updates (KBs) and hotfixes"""
        try:
            machines = self.get_machines()
            missing_updates = []
            
            for machine in machines:
                machine_id = machine.get('id')
                machine_name = machine.get('computerDnsName', 'Unknown')
                
                # Get missing KBs for this machine
                data = self._get(f'machines/{machine_id}/recommendations')
                recommendations = data.get('value', [])
                
                # Filter for update-related recommendations
                for rec in recommendations:
                    if 'update' in rec.get('productName', '').lower() or rec.get('recommendationCategory') == 'Application':
                        missing_updates.append({
                            'machineName': machine_name,
                            'machineId': machine_id,
                            'osPlatform': machine.get('osPlatform', 'Unknown'),
                            'osVersion': machine.get('osVersion', 'Unknown'),
                            'recommendationName': rec.get('recommendationName', 'Unknown'),
                            'productName': rec.get('productName', 'Unknown'),
                            'severity': rec.get('severity', 'Unknown'),
                            'exposedMachines': rec.get('exposedMachinesCount', 0),
                            'relatedComponent': rec.get('relatedComponent', 'Unknown'),
                        })
            
            logger.info(f'Fetched missing updates for {len(machines)} machines')
            return missing_updates
        except Exception as e:
            logger.error(f'Error fetching missing KBs: {str(e)}')
            return []
    
    def get_vulnerability_summary(self):
        """Get vulnerability summary for evidence reporting"""
        try:
            vulnerabilities = self.get_vulnerabilities()
            machines = self.get_machines()
            
            # Group vulnerabilities by severity
            summary = {
                'total_vulnerabilities': len(vulnerabilities),
                'total_machines': len(machines),
                'by_severity': {
                    'Critical': 0,
                    'High': 0,
                    'Medium': 0,
                    'Low': 0
                },
                'top_vulnerabilities': []
            }
            
            # Count by severity
            for vuln in vulnerabilities:
                severity = vuln.get('severity', 'Low')
                if severity in summary['by_severity']:
                    summary['by_severity'][severity] += 1
            
            # Get top 10 critical vulnerabilities
            critical_vulns = [v for v in vulnerabilities if v.get('severity') == 'Critical']
            summary['top_vulnerabilities'] = critical_vulns[:10]
            
            return summary
            
        except Exception as e:
            logger.error(f'Error generating vulnerability summary: {str(e)}')
            return None
    
    def generate_vulnerability_report_data(self):
        """Generate detailed vulnerability report data for Excel export"""
        try:
            vulnerabilities = self.get_vulnerabilities()
            machines = self.get_machines()
            
            # Create machine lookup
            machine_dict = {m['id']: m for m in machines}
            
            report_data = []
            
            for vuln in vulnerabilities:
                vuln_id = vuln.get('id', 'Unknown')
                name = vuln.get('name', 'Unknown')
                severity = vuln.get('severity', 'Unknown')
                cvss_score = vuln.get('cvssV3', 0)
                description = vuln.get('description', '')
                
                # Get affected machines
                exposed_machines = vuln.get('exposedMachines', 0)
                
                report_data.append({
                    'vulnerability_id': vuln_id,
                    'name': name,
                    'severity': severity,
                    'cvss_score': cvss_score,
                    'description': description,
                    'exposed_machines': exposed_machines,
                    'published_date': vuln.get('publishedOn', ''),
                    'updated_date': vuln.get('updatedOn', '')
                })
            
            # Sort by severity and CVSS score
            severity_order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
            report_data.sort(key=lambda x: (severity_order.get(x['severity'], 4), -x['cvss_score']))
            
            return report_data
            
        except Exception as e:
            logger.error(f'Error generating vulnerability report: {str(e)}')
            return []
    
    def generate_patch_report_data(self):
        """Generate missing patch report data"""
        try:
            recommendations = self.get_recommendations()
            
            # Filter for patch-related recommendations
            patch_recommendations = [
                r for r in recommendations 
                if 'update' in r.get('recommendationName', '').lower() or 
                   'patch' in r.get('recommendationName', '').lower()
            ]
            
            report_data = []
            
            for rec in patch_recommendations:
                report_data.append({
                    'recommendation_id': rec.get('id', 'Unknown'),
                    'name': rec.get('recommendationName', 'Unknown'),
                    'product': rec.get('productName', 'Unknown'),
                    'severity': rec.get('severity', 'Unknown'),
                    'exposed_machines': rec.get('exposedMachinesCount', 0),
                    'status': rec.get('status', 'Active'),
                    'remediation_type': rec.get('remediationType', 'Update'),
                    'recommendation': rec.get('recommendedSecurityUpdate', '')
                })
            
            return report_data
            
        except Exception as e:
            logger.error(f'Error generating patch report: {str(e)}')
            return []
    
    def get_machine_summary(self):
        """Get summary of machine security posture"""
        try:
            machines = self.get_machines()
            
            summary = {
                'total_machines': len(machines),
                'by_risk_score': {
                    'High': 0,
                    'Medium': 0,
                    'Low': 0,
                    'None': 0
                },
                'by_health_status': {
                    'Active': 0,
                    'Inactive': 0,
                    'Unknown': 0
                }
            }
            
            for machine in machines:
                # Risk score categorization
                risk_score = machine.get('riskScore', 'None')
                if risk_score in summary['by_risk_score']:
                    summary['by_risk_score'][risk_score] += 1
                
                # Health status
                health = machine.get('healthStatus', 'Unknown')
                if health in summary['by_health_status']:
                    summary['by_health_status'][health] += 1
            
            return summary
            
        except Exception as e:
            logger.error(f'Error generating machine summary: {str(e)}')
            return None
    
    def generate_software_inventory_report_data(self):
        """Generate comprehensive software inventory report data"""
        try:
            software = self.get_software()
            vulnerabilities = self.get_vulnerabilities()
            
            # Create vulnerability lookup by software
            vuln_by_software = {}
            for vuln in vulnerabilities:
                # Software name is typically in the vulnerability name or description
                software_name = vuln.get('name', '').split()[0] if vuln.get('name') else ''
                if software_name not in vuln_by_software:
                    vuln_by_software[software_name] = []
                vuln_by_software[software_name].append(vuln)
            
            report_data = []
            
            for sw in software:
                vendor = sw.get('vendor', 'Unknown')
                name = sw.get('name', 'Unknown')
                version = sw.get('version', 'Unknown')
                installed_machines = sw.get('installedMachines', 0)
                
                # Check for known vulnerabilities
                sw_vulns = vuln_by_software.get(name, [])
                has_vulnerabilities = len(sw_vulns) > 0
                critical_vulns = sum(1 for v in sw_vulns if v.get('severity') == 'Critical')
                high_vulns = sum(1 for v in sw_vulns if v.get('severity') == 'High')
                
                report_data.append({
                    'vendor': vendor,
                    'product': name,
                    'version': version,
                    'installed_on': installed_machines,
                    'has_vulnerabilities': 'Yes' if has_vulnerabilities else 'No',
                    'critical_vulns': critical_vulns,
                    'high_vulns': high_vulns,
                    'total_vulns': len(sw_vulns),
                    'category': sw.get('category', 'Unknown'),
                    'end_of_life': sw.get('endOfSupportStatus', 'Supported')
                })
            
            # Sort by number of vulnerabilities (highest first), then by installed machines
            report_data.sort(key=lambda x: (-x['total_vulns'], -x['installed_on']))
            
            return report_data
            
        except Exception as e:
            logger.error(f'Error generating software inventory report: {str(e)}')
            return []
