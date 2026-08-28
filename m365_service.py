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


# Microsoft licensing SKU partNumber -> human-friendly product name. Graph returns
# the cryptic skuPartNumber (e.g. "SPB"); this maps the ones present in the tenant
# (plus common extras) to readable names. Unmapped SKUs fall back to the raw code.
M365_SKU_NAMES = {
    "SPB": "Microsoft 365 Business Premium",
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Business Standard",
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Business Basic",
    "SPE_E3": "Microsoft 365 E3",
    "SPE_E5": "Microsoft 365 E5",
    "ENTERPRISEPACK": "Office 365 E3",
    "ENTERPRISEPREMIUM": "Office 365 E5",
    "STANDARDPACK": "Office 365 E1",
    "Microsoft_365_Copilot": "Microsoft 365 Copilot",
    "FLOW_FREE": "Power Automate (Free)",
    "POWER_BI_STANDARD": "Power BI (Free)",
    "POWERAPPS_DEV": "Power Apps for Developer",
    "Power_Pages_vTrial_for_Makers": "Power Pages vTrial (Makers)",
    "CCIBOTS_PRIVPREV_VIRAL": "Copilot Studio Viral Trial",
    "VISIOCLIENT": "Visio Plan 2",
    "PROJECTPROFESSIONAL": "Project Plan 3",
    "MCOEV": "Teams Phone Standard",
    "MCOPSTN1": "Teams Calling Plan (Domestic)",
    "MCOPSTN2": "Teams Calling Plan (Domestic & Intl)",
    "MCOPSTNC": "Teams Communications Credits",
    "Teams_Premium_(for_Departments)": "Teams Premium",
    "AAD_PREMIUM": "Microsoft Entra ID P1",
    "AAD_PREMIUM_P2": "Microsoft Entra ID P2",
    "EMS": "Enterprise Mobility + Security E3",
    "EMSPREMIUM": "Enterprise Mobility + Security E5",
    "RIGHTSMANAGEMENT_ADHOC": "Azure Rights Management (Ad-hoc)",
    "WIN_DEF_ATP": "Microsoft Defender for Endpoint",
    "CPC_E_2C_4GB_128GB": "Windows 365 Enterprise (2vCPU/4GB/128GB)",
}


def friendly_sku(sku):
    """Map a Graph skuPartNumber to a readable product name (raw code as fallback)."""
    if not sku:
        return sku
    return M365_SKU_NAMES.get(sku, M365_SKU_NAMES.get(str(sku).strip(), str(sku)))


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

    def find_user(self, user_ref):
        """Look up a user by UPN/email/id. Returns the Graph user dict (id,
        userPrincipalName, usageLocation, accountEnabled, assignedLicenses) or
        None if not found (e.g. not yet synced to Entra)."""
        token = self.get_access_token()
        url = (f"{self.base_url}/users/{quote(user_ref, safe='')}"
               "?$select=id,userPrincipalName,usageLocation,accountEnabled,assignedLicenses")
        try:
            r = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=25)
            return r.json() if r.status_code == 200 else None
        except requests.exceptions.RequestException:
            return None

    def ensure_usage_location(self, user_id, location='US'):
        """Set usageLocation (required before group-based licensing can apply).
        Returns True on success."""
        token = self.get_access_token()
        url = f"{self.base_url}/users/{quote(user_id, safe='')}"
        try:
            r = requests.patch(url, headers={'Authorization': f'Bearer {token}',
                               'Content-Type': 'application/json'},
                               json={'usageLocation': location}, timeout=25)
            return r.status_code in (200, 204)
        except requests.exceptions.RequestException:
            return False

    def add_group_member(self, group_id, user_id):
        """Add a directory user to a group (e.g. the license-bearing group).
        Idempotent — an 'already exists' 400 is treated as success. Returns
        {'success': bool, 'already': bool, 'error': str?}."""
        token = self.get_access_token()
        url = f"{self.base_url}/groups/{quote(group_id, safe='')}/members/$ref"
        body = {'@odata.id': f"{self.base_url}/directoryObjects/{user_id}"}
        try:
            r = requests.post(url, headers={'Authorization': f'Bearer {token}',
                              'Content-Type': 'application/json'}, json=body, timeout=25)
            if r.status_code in (200, 204):
                return {'success': True, 'already': False}
            if r.status_code == 400 and 'already exist' in (r.text or '').lower():
                return {'success': True, 'already': True}
            return {'success': False, 'error': f"HTTP {r.status_code}: {(r.text or '')[:200]}"}
        except requests.exceptions.RequestException as e:
            return {'success': False, 'error': str(e)}

    def send_mail(self, mailbox_email, recipients, subject, text_body=None, html_body=None, from_email=None):
        """Send mail through Microsoft Graph using the specified mailbox endpoint."""
        if not mailbox_email:
            raise ValueError("Mailbox email is required")

        to_recipients = [
            {'emailAddress': {'address': recipient}}
            for recipient in recipients
            if recipient
        ]
        if not to_recipients:
            raise ValueError("At least one recipient is required")

        token = self.get_access_token()
        encoded_mailbox = quote(mailbox_email, safe='')
        url = f"{self.base_url}/users/{encoded_mailbox}/sendMail"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        graph_from_email = from_email or mailbox_email
        payload = {
            'message': {
                'subject': subject,
                'from': {
                    'emailAddress': {
                        'address': graph_from_email
                    }
                },
                'body': {
                    'contentType': 'HTML' if html_body else 'Text',
                    'content': html_body or text_body or ''
                },
                'toRecipients': to_recipients,
            },
            'saveToSentItems': True,
        }

        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            logger.info(
                "Sent Graph email through %s as %s to %d recipient(s)",
                mailbox_email,
                graph_from_email,
                len(to_recipients),
            )
            return True
        except requests.exceptions.RequestException as e:
            body = getattr(e.response, 'text', '') if hasattr(e, 'response') else ''
            logger.error(
                "Graph sendMail failed through %s as %s: %s %s",
                mailbox_email,
                graph_from_email,
                e,
                body,
            )
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
        # signInActivity needs AuditLog.Read.All, which this app registration
        # holds. Without it last_signin_datetime stays null for every user and
        # no access review can evidence a dormant account.
        select = ('id,userPrincipalName,displayName,jobTitle,department,'
                  'officeLocation,accountEnabled,createdDateTime,signInActivity,userType')
        try:
            users = self._get_all_pages(f'users?$select={select}')
        except Exception as exc:
            # Fall back rather than lose the whole user sync if the permission
            # is ever revoked.
            logger.warning(f"signInActivity unavailable ({exc}); retrying without it")
            users = self._get_all_pages(
                'users?$select=id,userPrincipalName,displayName,jobTitle,'
                'department,officeLocation,accountEnabled,createdDateTime,userType')
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

    # ========== IDENTITY RECONCILE (cloud-first → on-prem AD hard-match) ==========
    # The Tracker links AD↔M365 only by email/UPN/object-id; these add the immutable-ID
    # (sourceAnchor) reads + the one write needed to hard-match a cloud-first user to a
    # freshly-created on-prem AD account. See workflow_engine onboard hard-match step.

    # Fields that describe an object's on-prem sync linkage. Selecting them is read-only.
    IDENTITY_SELECT = (
        "id,userPrincipalName,displayName,mail,proxyAddresses,accountEnabled,"
        "onPremisesImmutableId,onPremisesSyncEnabled,onPremisesSamAccountName,"
        "onPremisesDistinguishedName,createdDateTime,assignedLicenses"
    )

    def get_user_identity(self, user_ref):
        """Read the identity/sync-linkage fields for one cloud user (read-only).

        user_ref may be an object id or a UPN. Returns the user dict, or None if the
        object doesn't exist (404)."""
        if not user_ref:
            return None
        token = self.get_access_token()
        encoded = quote(str(user_ref), safe='')
        url = f"{self.base_url}/users/{encoded}?$select={self.IDENTITY_SELECT}"
        headers = {'Authorization': f'Bearer {token}'}
        try:
            r = requests.get(url, headers=headers)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            logger.error("get_user_identity failed for %s: %s", user_ref, e)
            raise

    def find_cloud_objects(self, query):
        """Find cloud user objects matching a free-text query across displayName,
        mail, userPrincipalName and proxyAddresses (read-only). Used to surface
        DUPLICATE cloud objects for the same person before a hard-match.

        Uses $search, which requires the ConsistencyLevel:eventual header. Returns a
        list of user dicts (identity fields)."""
        if not query:
            return []
        token = self.get_access_token()
        # $search terms must be quoted. Only $search-able properties are allowed —
        # proxyAddresses is NOT (Graph 400s on it, failing the whole OR'd query), so it's
        # excluded here (still returned in $select for display). Alias overlap is caught
        # at display time, not search.
        terms = " OR ".join(
            f'"{f}:{query}"' for f in
            ("displayName", "mail", "userPrincipalName", "givenName", "surname")
        )
        url = (f"{self.base_url}/users?$search={quote(terms, safe='')}"
               f"&$select={self.IDENTITY_SELECT}&$count=true&$top=50")
        headers = {'Authorization': f'Bearer {token}', 'ConsistencyLevel': 'eventual'}
        try:
            r = requests.get(url, headers=headers)
            r.raise_for_status()
            return r.json().get('value', [])
        except requests.exceptions.RequestException as e:
            logger.error("find_cloud_objects failed for %r: %s", query, e)
            raise

    def get_deleted_users(self):
        """List soft-deleted users in the Entra recycle bin (30-day retention).

        READ-ONLY — lists what is already soft-deleted; does NOT delete anything.
        Used to catch a recycle-bin twin that would collide on a new on-prem create."""
        token = self.get_access_token()
        # deletedItems with $count needs the advanced-query header.
        headers = {'Authorization': f'Bearer {token}', 'ConsistencyLevel': 'eventual'}
        url = (f"{self.base_url}/directory/deletedItems/microsoft.graph.user"
               f"?$select={self.IDENTITY_SELECT}&$count=true&$top=100")
        results = []
        try:
            while url:
                r = requests.get(url, headers=headers)
                r.raise_for_status()
                data = r.json()
                results.extend(data.get('value', []))
                url = data.get('@odata.nextLink')
            return results
        except requests.exceptions.RequestException as e:
            logger.error("get_deleted_users failed: %s", e)
            raise

    def set_onprem_immutable_id(self, user_id, immutable_id_b64):
        """WRITE: set onPremisesImmutableId (sourceAnchor) on a cloud-only user so a
        new on-prem AD object hard-matches it on the next AD-Connect sync cycle.

        Requires Graph application permission User.ReadWrite.All (admin-consented).
        Only valid on a cloud-only object (onPremisesSyncEnabled false/null); Graph
        rejects the write on an already-synced object. Returns True on success
        (HTTP 204). Raises with the Graph error body on failure so the caller can
        record exactly why (e.g. missing scope, already-synced)."""
        if not user_id:
            raise ValueError("user_id is required")
        if not immutable_id_b64:
            raise ValueError("immutable_id_b64 is required")
        token = self.get_access_token()
        encoded = quote(str(user_id), safe='')
        url = f"{self.base_url}/users/{encoded}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        try:
            r = requests.patch(url, headers=headers,
                               json={'onPremisesImmutableId': immutable_id_b64})
            r.raise_for_status()
            logger.info("Set onPremisesImmutableId on %s", user_id)
            return True
        except requests.exceptions.RequestException as e:
            body = getattr(e.response, 'text', '') if getattr(e, 'response', None) else ''
            logger.error("set_onprem_immutable_id failed for %s: %s %s", user_id, e, body)
            raise Exception(f"Graph PATCH onPremisesImmutableId failed: {e}; {body}")
    
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
        # Extended fields — some may not be available depending on Intune license tier.
        # NOTE: 'tpmVersion' is NOT a top-level property of the beta managedDevice
        # resource (it lives only under hardwareInformation, which is a non-default
        # complex type that requires a per-device GET). Including it in the list-call
        # $select returns 400 Bad Request, which previously forced a fallback to the
        # minimal field set and dropped ALL the rich hardware fields (storage/MAC/etc).
        # tpmVersion is still read opportunistically from hardwareInformation in the
        # consumer (assets_intune.py) when present.
        extended_fields = [
            'id', 'deviceName', 'serialNumber', 'userPrincipalName',
            'operatingSystem', 'osVersion', 'manufacturer', 'model',
            'complianceState', 'managementState', 'enrolledDateTime',
            'lastSyncDateTime', 'azureADDeviceId',
            'processorArchitecture', 'totalStorageSpaceInBytes',
            'freeStorageSpaceInBytes', 'wiFiMacAddress',
            'ethernetMacAddress',
        ]
        # Minimal fallback fields — always available.
        minimal_fields = [
            'id', 'deviceName', 'serialNumber', 'userPrincipalName',
            'operatingSystem', 'osVersion', 'manufacturer', 'model',
            'complianceState', 'managementState', 'enrolledDateTime',
            'lastSyncDateTime', 'azureADDeviceId',
        ]
        try:
            endpoint = 'deviceManagement/managedDevices?$select=' + ','.join(extended_fields)
            devices = self._get_all_pages(endpoint, use_beta=True)
            logger.info(f"Retrieved {len(devices)} devices with hardware details")
            return devices
        except Exception as e:
            if '400' in str(e) or 'Bad Request' in str(e):
                logger.warning("Extended hardware fields rejected (400); falling back to minimal fields")
                endpoint = 'deviceManagement/managedDevices?$select=' + ','.join(minimal_fields)
                devices = self._get_all_pages(endpoint, use_beta=True)
                logger.info(f"Retrieved {len(devices)} devices (minimal fields)")
                return devices
            raise
    
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
