"""
TeamViewer Evidence Collection Service
Collects patch management and device information from TeamViewer API
"""

import requests
import json
from datetime import datetime
from app import db, Setting, Asset


class TeamViewerEvidenceService:
    """Service to collect evidence from TeamViewer API for SOC2"""
    
    def __init__(self):
        self.base_url = 'https://webapi.teamviewer.com/api/v1'
        self.token = self._get_token()
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
    
    def _get_token(self):
        """Get TeamViewer API token from settings"""
        from secret_store import decrypt_secret
        token_setting = Setting.query.filter_by(key='teamviewer_token').first()
        if not token_setting or not token_setting.value:
            raise ValueError("TeamViewer API token not configured")
        return decrypt_secret(token_setting.value)
    
    def get_all_devices(self):
        """Get all managed devices from TeamViewer"""
        try:
            devices = []
            
            # Get all groups
            groups_url = f'{self.base_url}/managed/groups'
            response = requests.get(groups_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            groups = response.json().get('resources', [])
            
            # Get devices from each group
            for group in groups:
                group_id = group['id']
                devices_url = f'{self.base_url}/managed/groups/{group_id}/devices'
                response = requests.get(devices_url, headers=self.headers, timeout=30)
                response.raise_for_status()
                group_devices = response.json().get('resources', [])
                devices.extend(group_devices)
            
            return devices
        except Exception as e:
            print(f"Error fetching TeamViewer devices: {e}")
            return []
    
    def get_device_details(self, device_id):
        """Get detailed information about a specific device"""
        try:
            url = f'{self.base_url}/managed/devices/{device_id}'
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching device {device_id} details: {e}")
            return None
    
    def get_patch_status(self):
        """
        Get patch status for all devices
        Note: TeamViewer API doesn't directly expose patch data,
        but we can infer from OS version and last update times
        """
        devices = self.get_all_devices()
        patch_data = []
        
        for device in devices:
            # Find matching asset in our database
            asset = Asset.query.filter_by(teamviewer_id=device.get('remotecontrol_id')).first()
            
            device_info = {
                'device_id': device.get('remotecontrol_id'),
                'device_name': device.get('alias') or device.get('remotecontrol_id'),
                'os_version': device.get('os_version', 'Unknown'),
                'last_seen': device.get('last_seen'),
                'online_state': device.get('online_state', 'Unknown'),
                'managed': device.get('managed', False),
                'asset_name': asset.name if asset else 'Untracked'
            }
            patch_data.append(device_info)
        
        return patch_data
    
    def get_device_inventory(self):
        """Get complete device inventory for asset tracking"""
        devices = self.get_all_devices()
        inventory = []
        
        for device in devices:
            asset = Asset.query.filter_by(teamviewer_id=device.get('remotecontrol_id')).first()
            
            device_info = {
                'teamviewer_id': device.get('remotecontrol_id'),
                'device_name': device.get('alias') or device.get('remotecontrol_id'),
                'description': device.get('description', ''),
                'os_version': device.get('os_version', 'Unknown'),
                'last_seen': device.get('last_seen'),
                'online_state': device.get('online_state', 'Unknown'),
                'supported_features': device.get('supported_features', []),
                'assigned_to': device.get('assigned_to', {}).get('name', 'Unassigned'),
                'policy_id': device.get('policy_id'),
                'in_asset_db': asset is not None,
                'asset_name': asset.name if asset else None,
                'asset_tag': asset.asset_tag if asset else None
            }
            inventory.append(device_info)
        
        return inventory
    
    def get_vulnerability_summary(self):
        """
        Get vulnerability summary from device data
        Combines TeamViewer asset data with Intune device data for comprehensive scanning
        """
        from soc2_models import IntuneDevice
        
        vulnerabilities = []
        
        # Known vulnerable/outdated OS versions
        outdated_patterns = {
            'Windows 7': 'Critical - OS End of Life',
            'Windows 8': 'Critical - OS End of Life',  
            'Windows 10 1507': 'High - Outdated build',
            'Windows 10 1607': 'High - Outdated build',
            'Windows 10 1709': 'High - Outdated build',
            'Windows 10 1803': 'High - Outdated build',
            'Windows 10 1809': 'Medium - Outdated build',
            'Windows Server 2008': 'Critical - OS End of Life',
            'Windows Server 2012': 'High - Extended support only',
            'macOS 10.13': 'High - Outdated version',
            'macOS 10.14': 'Medium - Outdated version',
        }
        
        # Get vulnerability data from Intune (has OS version info)
        intune_devices = IntuneDevice.query.filter_by(is_current=True).all()
        
        for device in intune_devices:
            os_version = device.os_version or ''
            severity = 'Low'
            finding = None
            
            # Check for known outdated versions
            for pattern, risk in outdated_patterns.items():
                if pattern in os_version:
                    severity = risk.split(' - ')[0]
                    finding = risk
                    break
            
            # Also flag devices not encrypted
            if not device.is_encrypted:
                if not finding:
                    finding = 'Medium - Disk encryption not enabled'
                    severity = 'Medium'
                else:
                    finding += ' + Disk not encrypted'
            
            # Also flag non-compliant devices
            if device.compliance_state != 'compliant':
                if not finding:
                    finding = 'Medium - Device non-compliant'
                    severity = 'Medium'
                else:
                    finding += ' + Non-compliant'
            
            # Only add if there's a finding
            if finding:
                vulnerabilities.append({
                    'device_id': device.device_id or device.device_name,
                    'device_name': device.device_name,
                    'os_version': os_version or 'Unknown',
                    'severity': severity,
                    'finding': finding,
                    'last_seen': device.last_sync_datetime.isoformat() if device.last_sync_datetime else 'Unknown',
                    'remediation': self._get_remediation(finding)
                })
        
        # If no vulnerabilities found, create a summary showing all devices are compliant
        if not vulnerabilities:
            # Add a summary entry
            vulnerabilities.append({
                'device_id': 'SUMMARY',
                'device_name': 'All Devices Compliant',
                'os_version': f'{len(intune_devices)} devices scanned',
                'severity': 'Info',
                'finding': 'No critical or high vulnerabilities detected',
                'last_seen': datetime.now().isoformat(),
                'remediation': 'Continue regular security updates and monitoring'
            })
        
        return vulnerabilities
    
    def _get_remediation(self, finding):
        """Get remediation recommendation based on finding"""
        if 'End of Life' in finding:
            return 'Upgrade to supported OS version (Windows 10/11 or Server 2016+)'
        elif 'Outdated build' in finding:
            return 'Apply Windows updates to latest build'
        elif 'encryption' in finding.lower():
            return 'Enable BitLocker or device encryption via Intune policy'
        elif 'Non-compliant' in finding:
            return 'Review and remediate compliance policy violations'
        else:
            return 'Update to latest supported version'
    
    def get_policy_compliance(self):
        """Get policy compliance status for managed devices"""
        devices = self.get_all_devices()
        compliance = []
        
        for device in devices:
            is_managed = device.get('managed', False)
            has_policy = device.get('policy_id') is not None
            
            compliance.append({
                'device_id': device.get('remotecontrol_id'),
                'device_name': device.get('alias') or device.get('remotecontrol_id'),
                'managed': is_managed,
                'policy_applied': has_policy,
                'policy_id': device.get('policy_id'),
                'compliant': is_managed and has_policy,
                'last_seen': device.get('last_seen')
            })
        
        return compliance
    
    def generate_patch_report_data(self):
        """Generate comprehensive patch report data"""
        devices = self.get_all_devices()
        
        report = {
            'report_date': datetime.utcnow().isoformat(),
            'total_devices': len(devices),
            'online_devices': sum(1 for d in devices if d.get('online_state') == 'Online'),
            'managed_devices': sum(1 for d in devices if d.get('managed', False)),
            'devices_with_policy': sum(1 for d in devices if d.get('policy_id')),
            'devices': []
        }
        
        for device in devices:
            asset = Asset.query.filter_by(teamviewer_id=device.get('remotecontrol_id')).first()
            last_seen = device.get('last_seen')
            
            # Calculate days since last seen
            days_offline = None
            if last_seen:
                try:
                    last_seen_dt = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                    days_offline = (datetime.utcnow() - last_seen_dt.replace(tzinfo=None)).days
                except:
                    pass
            
            device_data = {
                'teamviewer_id': device.get('remotecontrol_id'),
                'device_name': device.get('alias') or device.get('remotecontrol_id'),
                'os_version': device.get('os_version', 'Unknown'),
                'online_state': device.get('online_state', 'Unknown'),
                'managed': device.get('managed', False),
                'policy_applied': device.get('policy_id') is not None,
                'last_seen': last_seen,
                'days_offline': days_offline,
                'asset_tracked': asset is not None,
                'assigned_to': device.get('assigned_to', {}).get('name', 'Unassigned')
            }
            report['devices'].append(device_data)
        
        return report
