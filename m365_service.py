"""
Microsoft 365 and Intune Integration Service
Handles authentication and data collection for SOC2 compliance
"""
import requests
import json
from datetime import datetime, timedelta
import logging
from urllib.parse import quote

logger = logging.getLogger(__name__)


class M365Service:
    """Service for Microsoft Graph API integration"""
    
    def __init__(self, tenant_id, client_id, client_secret):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = "https://graph.microsoft.com/v1.0"
        self.beta_url = "https://graph.microsoft.com/beta"
        self.access_token = None
        self.token_expiry = None
    
    def get_access_token(self):
        """Get OAuth2 access token from Microsoft"""
        if self.access_token and self.token_expiry and datetime.utcnow() < self.token_expiry:
            return self.access_token
        
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'scope': 'https://graph.microsoft.com/.default',
            'grant_type': 'client_credentials'
        }
        
        try:
            response = requests.post(url, data=data)
            response.raise_for_status()
            token_data = response.json()
            
            self.access_token = token_data['access_token']
            # Set expiry 5 minutes before actual expiry for safety
            expires_in = token_data.get('expires_in', 3600) - 300
            self.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
            
            logger.info("Successfully obtained M365 access token")
            return self.access_token
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get access token: {e}")
            raise Exception(f"Authentication failed: {str(e)}")
    
    def _make_request(self, endpoint, use_beta=False):
        """Make authenticated request to Microsoft Graph"""
        token = self.get_access_token()
        base = self.beta_url if use_beta else self.base_url
        url = f"{base}/{endpoint}"
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {endpoint}: {e}")
            raise

    def get_user_photo_bytes(self, user_principal_name: str):
        """Fetch a user's profile photo as raw bytes.

        Returns None if no photo is set or photo can't be retrieved.
        """
        if not user_principal_name:
            return None

        token = self.get_access_token()
        encoded = quote(user_principal_name, safe='')
        url = f"{self.base_url}/users/{encoded}/photo/$value"
        headers = {
            'Authorization': f'Bearer {token}',
        }

        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200 and response.content:
                return response.content
            if response.status_code not in (404,):
                logger.warning(
                    "Photo fetch failed for %s (status=%s)",
                    user_principal_name,
                    response.status_code,
                )
            # 404 is common if user has no photo
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to fetch photo for {user_principal_name}: {e}")
            return None
    
    def _get_all_pages(self, endpoint, use_beta=False):
        """Get all pages from paginated API response"""
        all_results = []
        next_link = endpoint
        
        while next_link:
            if next_link.startswith('http'):
                # It's a full URL from @odata.nextLink
                token = self.get_access_token()
                headers = {'Authorization': f'Bearer {token}'}
                response = requests.get(next_link, headers=headers)
                response.raise_for_status()
                data = response.json()
            else:
                data = self._make_request(next_link, use_beta)
            
            all_results.extend(data.get('value', []))
            next_link = data.get('@odata.nextLink')
        
        return all_results
    
    def get_users_mfa_status(self):
        """Get MFA status for all users"""
        try:
            users_data = self._get_all_pages('users?$select=id,displayName,userPrincipalName,mail')
            mfa_data = []
            
            for user in users_data:
                user_id = user.get('id')
                try:
                    # Get authentication methods for user
                    methods_data = self._make_request(f'users/{user_id}/authentication/methods')
                    methods = methods_data.get('value', [])
                    
                    mfa_enabled = len(methods) > 1  # More than just password
                    mfa_methods = []
                    
                    for method in methods:
                        method_type = method.get('@odata.type', '')
                        if 'phoneAuthenticationMethod' in method_type:
                            mfa_methods.append('Phone')
                        elif 'emailAuthenticationMethod' in method_type:
                            mfa_methods.append('Email')
                        elif 'microsoftAuthenticatorAuthenticationMethod' in method_type:
                            mfa_methods.append('Authenticator App')
                        elif 'fido2AuthenticationMethod' in method_type:
                            mfa_methods.append('FIDO2 Security Key')
                    
                    mfa_data.append({
                        'displayName': user.get('displayName'),
                        'userPrincipalName': user.get('userPrincipalName'),
                        'mail': user.get('mail'),
                        'mfaEnabled': mfa_enabled,
                        'mfaMethods': ', '.join(mfa_methods) if mfa_methods else 'None',
                        'methodCount': len(methods)
                    })
                except Exception as e:
                    logger.warning(f"Could not get MFA status for user {user.get('userPrincipalName')}: {str(e)}")
                    mfa_data.append({
                        'displayName': user.get('displayName'),
                        'userPrincipalName': user.get('userPrincipalName'),
                        'mail': user.get('mail'),
                        'mfaEnabled': False,
                        'mfaMethods': 'Unable to verify',
                        'methodCount': 0
                    })
            
            logger.info(f'Fetched MFA status for {len(mfa_data)} users')
            return mfa_data
        except Exception as e:
            logger.error(f'Error fetching MFA status: {str(e)}')
            return []
    
    def get_conditional_access_policies(self):
        """Get conditional access policies"""
        try:
            data = self._make_request('identity/conditionalAccess/policies')
            policies = data.get('value', [])
            logger.info(f'Fetched {len(policies)} conditional access policies')
            return policies
        except Exception as e:
            logger.error(f'Error fetching conditional access policies: {str(e)}')
            return []
    
    # ========== USER MANAGEMENT ==========
    
    def get_all_users(self):
        """Get all M365 users"""
        logger.info("Fetching all M365 users")
        # Note: signInActivity requires AuditLog.Read.All permission (optional)
        users = self._get_all_pages('users?$select=id,userPrincipalName,displayName,jobTitle,department,officeLocation,accountEnabled,createdDateTime')
        logger.info(f"Retrieved {len(users)} users")
        return users
    
    def get_user_licenses(self, user_id):
        """Get license assignments for a user"""
        try:
            data = self._make_request(f'users/{user_id}/licenseDetails')
            return data.get('value', [])
        except Exception as e:
            logger.error(f"Failed to get licenses for user {user_id}: {e}")
            return []
    
    def get_admin_roles(self):
        """Get all directory roles (admin roles)"""
        logger.info("Fetching admin roles")
        roles = self._get_all_pages('directoryRoles')
        
        admin_assignments = []
        for role in roles:
            role_id = role['id']
            role_name = role['displayName']
            
            # Get members of this role
            try:
                members = self._get_all_pages(f'directoryRoles/{role_id}/members')
                for member in members:
                    admin_assignments.append({
                        'role_id': role_id,
                        'role_name': role_name,
                        'user_id': member.get('id'),
                        'user_principal_name': member.get('userPrincipalName'),
                        'display_name': member.get('displayName')
                    })
            except Exception as e:
                logger.error(f"Failed to get members for role {role_name}: {e}")
        
        logger.info(f"Retrieved {len(admin_assignments)} admin role assignments")
        return admin_assignments
    
    # ========== INTUNE DEVICE MANAGEMENT ==========
    
    def get_managed_devices(self):
        """Get all Intune managed devices"""
        logger.info("Fetching Intune managed devices")
        devices = self._get_all_pages('deviceManagement/managedDevices', use_beta=True)
        logger.info(f"Retrieved {len(devices)} managed devices")
        return devices
    
    def get_device_hardware_info(self, device_id):
        """Get detailed hardware information for a specific device"""
        logger.info(f"Fetching hardware info for device {device_id}")
        try:
            # Beta endpoint provides more detailed hardware info
            device = self._make_request(f'deviceManagement/managedDevices/{device_id}', use_beta=True)
            return device
        except Exception as e:
            logger.error(f"Failed to get hardware info for device {device_id}: {e}")
            return None
    
    def get_all_devices_with_hardware(self):
        """Get all managed devices with detailed hardware information"""
        logger.info("Fetching all devices with hardware details")
        # Use $select to reduce payload; the beta managedDevices entity can be very large.
        select_fields = [
            'id',
            'deviceName',
            'serialNumber',
            'userPrincipalName',
            'operatingSystem',
            'osVersion',
            'manufacturer',
            'model',
            'complianceState',
            'managementState',
            'enrolledDateTime',
            'lastSyncDateTime',
            'azureADDeviceId',
            'processorArchitecture',
            'physicalMemoryInBytes',
            'totalStorageSpaceInBytes',
            'freeStorageSpaceInBytes',
            'wiFiMacAddress',
            'ethernetMacAddress',
            'tpmVersion',
            'hardwareInformation'
        ]
        endpoint = 'deviceManagement/managedDevices?$select=' + ','.join(select_fields)
        devices = self._get_all_pages(endpoint, use_beta=True)
        logger.info(f"Retrieved {len(devices)} devices with hardware details")
        return devices
    
    def get_device_compliance(self):
        """Get device compliance status"""
        logger.info("Fetching device compliance data")
        try:
            compliance = self._get_all_pages('deviceManagement/managedDevices?$select=id,deviceName,complianceState,lastSyncDateTime', use_beta=True)
            return compliance
        except Exception as e:
            logger.error(f"Failed to get device compliance: {e}")
            return []
    
    def get_device_installed_apps(self, device_id):
        """Get installed apps for a specific device"""
        try:
            apps = self._get_all_pages(f'deviceManagement/managedDevices/{device_id}/detectedApps', use_beta=True)
            return apps
        except Exception as e:
            logger.error(f"Failed to get apps for device {device_id}: {e}")
            return []
    
    def get_all_detected_apps(self):
        """Get all detected apps across all devices"""
        logger.info("Fetching detected apps from all devices")
        try:
            apps = self._get_all_pages('deviceManagement/detectedApps?$expand=managedDevices', use_beta=True)
            logger.info(f"Retrieved {len(apps)} detected apps")
            return apps
        except Exception as e:
            logger.error(f"Failed to get detected apps: {e}")
            return []
    
    # ========== AUDIT LOGS ==========
    
    def get_audit_logs(self, start_date=None, end_date=None):
        """Get Azure AD audit logs"""
        logger.info("Fetching audit logs")
        
        filter_parts = []
        if start_date:
            filter_parts.append(f"activityDateTime ge {start_date.isoformat()}Z")
        if end_date:
            filter_parts.append(f"activityDateTime le {end_date.isoformat()}Z")
        
        filter_query = '&$filter=' + ' and '.join(filter_parts) if filter_parts else ''
        
        try:
            logs = self._get_all_pages(f'auditLogs/directoryAudits?$top=100{filter_query}')
            logger.info(f"Retrieved {len(logs)} audit log entries")
            return logs
        except Exception as e:
            logger.error(f"Failed to get audit logs: {e}")
            return []
    
    def get_sign_in_logs(self, start_date=None, end_date=None):
        """Get sign-in logs"""
        logger.info("Fetching sign-in logs")
        
        filter_parts = []
        if start_date:
            filter_parts.append(f"createdDateTime ge {start_date.isoformat()}Z")
        if end_date:
            filter_parts.append(f"createdDateTime le {end_date.isoformat()}Z")
        
        filter_query = '&$filter=' + ' and '.join(filter_parts) if filter_parts else ''
        
        try:
            logs = self._get_all_pages(f'auditLogs/signIns?$top=100{filter_query}')
            logger.info(f"Retrieved {len(logs)} sign-in log entries")
            return logs
        except Exception as e:
            logger.error(f"Failed to get sign-in logs: {e}")
            return []
    
    # ========== GROUPS ==========
    
    def get_all_groups(self):
        """Get all Azure AD groups"""
        logger.info("Fetching all groups")
        groups = self._get_all_pages('groups')
        logger.info(f"Retrieved {len(groups)} groups")
        return groups
    
    def get_group_members(self, group_id):
        """Get members of a specific group"""
        try:
            members = self._get_all_pages(f'groups/{group_id}/members')
            return members
        except Exception as e:
            logger.error(f"Failed to get group members for {group_id}: {e}")
            return []
    
    # ========== TEST CONNECTION ==========
    
    def test_connection(self):
        """Test the connection to Microsoft Graph API"""
        try:
            token = self.get_access_token()
            data = self._make_request('organization')
            org_name = data['value'][0]['displayName'] if data.get('value') else 'Unknown'
            
            return {
                'success': True,
                'organization': org_name,
                'message': 'Successfully connected to Microsoft Graph API'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': 'Failed to connect to Microsoft Graph API'
            }
