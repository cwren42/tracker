"""
Azure Security Center & Networking Service
Collects security evidence from Azure Resource Manager, Defender for Cloud, and Network Security
"""
import requests
import msal
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class AzureSecurityService:
    """Service for Azure Security Center and Network data collection"""
    
    def __init__(self):
        """Initialize Azure Security service with credentials from database"""
        # Import here to avoid circular dependency
        from app import db
        from app import Setting
        
        self.tenant_id = self._get_setting('m365_tenant_id')
        self.client_id = self._get_setting('m365_client_id')
        self.client_secret = self._get_setting('m365_client_secret')
        self.subscription_id = self._get_setting('azure_subscription_id')
        
        if not all([self.tenant_id, self.client_id, self.client_secret]):
            raise ValueError("Azure credentials not configured")
        
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope = ["https://management.azure.com/.default"]
        self.token = None
        self.token_expiry = None
        
    def _get_setting(self, key):
        """Get setting from database"""
        from app import Setting
        setting = Setting.query.filter_by(key=key).first()
        return setting.value if setting else None
    
    def _get_token(self):
        """Get Azure Resource Manager access token"""
        if self.token and self.token_expiry and datetime.utcnow() < self.token_expiry - timedelta(seconds=300):
            return self.token
        
        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=self.client_secret
        )
        
        result = app.acquire_token_for_client(scopes=self.scope)
        
        if "access_token" in result:
            self.token = result["access_token"]
            self.token_expiry = datetime.utcnow() + timedelta(seconds=result.get("expires_in", 3600))
            return self.token
        else:
            error = result.get("error_description", result.get("error"))
            raise Exception(f"Failed to acquire Azure token: {error}")
    
    def _make_request(self, url, params=None):
        """Make authenticated request to Azure API"""
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    
    def _get_all_pages(self, url, params=None):
        """Handle pagination for Azure API responses"""
        results = []
        
        while url:
            data = self._make_request(url, params)
            if 'value' in data:
                results.extend(data['value'])
            else:
                results.append(data)
            
            url = data.get('nextLink')
            params = None  # nextLink includes all params
        
        return results
    
    def get_network_security_groups(self):
        """Get all Network Security Groups and their rules"""
        if not self.subscription_id:
            logger.warning("Azure subscription ID not configured")
            return []
        
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Network/networkSecurityGroups?api-version=2023-05-01"
        
        try:
            nsgs = self._get_all_pages(url)
            
            result = []
            for nsg in nsgs:
                nsg_data = {
                    'name': nsg.get('name'),
                    'location': nsg.get('location'),
                    'resource_group': nsg.get('id', '').split('/resourceGroups/')[1].split('/')[0] if '/resourceGroups/' in nsg.get('id', '') else None,
                    'security_rules': []
                }
                
                # Get security rules
                properties = nsg.get('properties', {})
                for rule in properties.get('securityRules', []):
                    nsg_data['security_rules'].append({
                        'name': rule.get('name'),
                        'priority': rule.get('properties', {}).get('priority'),
                        'direction': rule.get('properties', {}).get('direction'),
                        'access': rule.get('properties', {}).get('access'),
                        'protocol': rule.get('properties', {}).get('protocol'),
                        'source_address': rule.get('properties', {}).get('sourceAddressPrefix'),
                        'destination_address': rule.get('properties', {}).get('destinationAddressPrefix'),
                        'source_port': rule.get('properties', {}).get('sourcePortRange'),
                        'destination_port': rule.get('properties', {}).get('destinationPortRange')
                    })
                
                result.append(nsg_data)
            
            return result
        except Exception as e:
            logger.error(f"Error fetching NSGs: {str(e)}")
            return []
    
    def get_security_alerts(self):
        """Get Defender for Cloud security alerts"""
        if not self.subscription_id:
            return []
        
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Security/alerts?api-version=2022-01-01"
        
        try:
            alerts = self._get_all_pages(url)
            
            result = []
            for alert in alerts:
                props = alert.get('properties', {})
                result.append({
                    'name': props.get('alertDisplayName'),
                    'severity': props.get('severity'),
                    'status': props.get('status'),
                    'description': props.get('description'),
                    'detected_time': props.get('timeGeneratedUtc'),
                    'resource_id': props.get('compromisedEntity'),
                    'remediation': props.get('remediationSteps')
                })
            
            return result
        except Exception as e:
            logger.error(f"Error fetching security alerts: {str(e)}")
            return []
    
    def get_sql_databases(self):
        """Get SQL databases and encryption settings"""
        if not self.subscription_id:
            return []
        
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Sql/servers?api-version=2021-11-01"
        
        try:
            servers = self._get_all_pages(url)
            
            result = []
            for server in servers:
                server_name = server.get('name')
                resource_group = server.get('id', '').split('/resourceGroups/')[1].split('/')[0] if '/resourceGroups/' in server.get('id', '') else None
                
                # Get databases for this server
                db_url = f"https://management.azure.com/subscriptions/{self.subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Sql/servers/{server_name}/databases?api-version=2021-11-01"
                databases = self._get_all_pages(db_url)
                
                for db in databases:
                    db_name = db.get('name')
                    if db_name == 'master':  # Skip master database
                        continue
                    
                    # Get TDE status
                    tde_url = f"https://management.azure.com/subscriptions/{self.subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Sql/servers/{server_name}/databases/{db_name}/transparentDataEncryption/current?api-version=2021-11-01"
                    try:
                        tde = self._make_request(tde_url)
                        tde_status = tde.get('properties', {}).get('state', 'Unknown')
                    except:
                        tde_status = 'Unknown'
                    
                    result.append({
                        'server_name': server_name,
                        'database_name': db_name,
                        'location': server.get('location'),
                        'resource_group': resource_group,
                        'tde_enabled': tde_status == 'Enabled',
                        'tde_status': tde_status
                    })
            
            return result
        except Exception as e:
            logger.error(f"Error fetching SQL databases: {str(e)}")
            return []
    
    def get_storage_accounts(self):
        """Get storage accounts and encryption settings"""
        if not self.subscription_id:
            return []
        
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Storage/storageAccounts?api-version=2023-01-01"
        
        try:
            accounts = self._get_all_pages(url)
            
            result = []
            for account in accounts:
                props = account.get('properties', {})
                encryption = props.get('encryption', {})
                
                result.append({
                    'name': account.get('name'),
                    'location': account.get('location'),
                    'resource_group': account.get('id', '').split('/resourceGroups/')[1].split('/')[0] if '/resourceGroups/' in account.get('id', '') else None,
                    'encryption_enabled': encryption.get('services', {}).get('blob', {}).get('enabled', False),
                    'https_only': props.get('supportsHttpsTrafficOnly', False),
                    'tls_version': props.get('minimumTlsVersion', 'Unknown')
                })
            
            return result
        except Exception as e:
            logger.error(f"Error fetching storage accounts: {str(e)}")
            return []
    
    def get_virtual_machines(self):
        """Get virtual machines and their security settings"""
        if not self.subscription_id:
            return []
        
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Compute/virtualMachines?api-version=2023-03-01"
        
        try:
            vms = self._get_all_pages(url)
            
            result = []
            for vm in vms:
                props = vm.get('properties', {})
                storage_profile = props.get('storageProfile', {})
                os_disk = storage_profile.get('osDisk', {})
                
                result.append({
                    'name': vm.get('name'),
                    'location': vm.get('location'),
                    'resource_group': vm.get('id', '').split('/resourceGroups/')[1].split('/')[0] if '/resourceGroups/' in vm.get('id', '') else None,
                    'os_type': os_disk.get('osType'),
                    'disk_encryption': os_disk.get('encryptionSettings', {}).get('enabled', False),
                    'vm_size': props.get('hardwareProfile', {}).get('vmSize')
                })
            
            return result
        except Exception as e:
            logger.error(f"Error fetching VMs: {str(e)}")
            return []
    
    def get_security_assessments(self):
        """Get Defender for Cloud security assessments (vulnerability scan)"""
        if not self.subscription_id:
            return []
        
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Security/assessments?api-version=2020-01-01"
        
        try:
            assessments = self._get_all_pages(url)
            
            result = []
            for assessment in assessments:
                props = assessment.get('properties', {})
                status = props.get('status', {})
                
                result.append({
                    'name': props.get('displayName'),
                    'severity': props.get('metadata', {}).get('severity'),
                    'status': status.get('code'),
                    'description': props.get('metadata', {}).get('description'),
                    'remediation': props.get('metadata', {}).get('remediationDescription'),
                    'resource_id': assessment.get('id')
                })
            
            return result
        except Exception as e:
            logger.error(f"Error fetching security assessments: {str(e)}")
            return []
    
    def get_update_assessments(self):
        """Get update/patch assessments"""
        if not self.subscription_id:
            return []
        
        # This requires Azure Update Management / Automation Account
        # For now, return empty - would need automation account configured
        return []
    
    def get_monitor_alerts(self):
        """Get Azure Monitor alert rules"""
        if not self.subscription_id:
            return []
        
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Insights/metricAlerts?api-version=2018-03-01"
        
        try:
            alerts = self._get_all_pages(url)
            
            result = []
            for alert in alerts:
                props = alert.get('properties', {})
                result.append({
                    'name': alert.get('name'),
                    'location': alert.get('location'),
                    'enabled': props.get('enabled', False),
                    'severity': props.get('severity'),
                    'description': props.get('description'),
                    'criteria': props.get('criteria')
                })
            
            return result
        except Exception as e:
            logger.error(f"Error fetching monitor alerts: {str(e)}")
            return []
    
    def get_network_topology(self):
        """Get network topology using Azure Resource Graph"""
        if not self.subscription_id:
            return []
        
        # This would require Azure Resource Graph API
        # Returns virtual networks, subnets, and connections
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Network/virtualNetworks?api-version=2023-05-01"
        
        try:
            vnets = self._get_all_pages(url)
            
            result = []
            for vnet in vnets:
                props = vnet.get('properties', {})
                result.append({
                    'name': vnet.get('name'),
                    'location': vnet.get('location'),
                    'resource_group': vnet.get('id', '').split('/resourceGroups/')[1].split('/')[0] if '/resourceGroups/' in vnet.get('id', '') else None,
                    'address_space': props.get('addressSpace', {}).get('addressPrefixes', []),
                    'subnets': [
                        {
                            'name': subnet.get('name'),
                            'address_prefix': subnet.get('properties', {}).get('addressPrefix')
                        }
                        for subnet in props.get('subnets', [])
                    ]
                })
            
            return result
        except Exception as e:
            logger.error(f"Error fetching network topology: {str(e)}")
            return []
    
    def get_role_assignments(self):
        """Get RBAC role assignments for the subscription"""
        if not self.subscription_id:
            return []
        
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01"
        
        try:
            assignments = self._get_all_pages(url)
            
            # Enrich with role definitions
            enriched = []
            for assignment in assignments:
                props = assignment.get('properties', {})
                role_def_id = props.get('roleDefinitionId', '')
                
                # Get role definition name
                role_name = 'Unknown'
                try:
                    role_url = f"https://management.azure.com{role_def_id}?api-version=2022-04-01"
                    role_response = requests.get(role_url, headers=self.headers)
                    role_response.raise_for_status()
                    role_name = role_response.json().get('properties', {}).get('roleName', 'Unknown')
                except:
                    pass
                
                enriched.append({
                    'id': assignment.get('id', ''),
                    'name': assignment.get('name', ''),
                    'principalId': props.get('principalId', ''),
                    'principalType': props.get('principalType', 'Unknown'),
                    'roleDefinitionId': role_def_id,
                    'roleName': role_name,
                    'scope': props.get('scope', ''),
                    'createdOn': props.get('createdOn', ''),
                    'createdBy': props.get('createdBy', '')
                })
            
            logger.info(f'Fetched {len(enriched)} role assignments from Azure')
            return enriched
        except Exception as e:
            logger.error(f"Error fetching role assignments: {str(e)}")
            return []
    
    def get_secure_score(self):
        """Get Microsoft Defender for Cloud secure score and controls"""
        try:
            token = self._get_token()
            headers = {'Authorization': f'Bearer {token}'}
            
            # Get secure score
            url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Security/secureScores/ascScore?api-version=2020-01-01"
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            score_data = response.json()
            properties = score_data.get('properties', {})
            
            # Get secure score controls
            controls_url = f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Security/secureScoreControls?api-version=2020-01-01"
            controls_response = requests.get(controls_url, headers=headers, timeout=30)
            controls_response.raise_for_status()
            
            controls = controls_response.json().get('value', [])
            
            result = {
                'score': properties.get('score', {}).get('current', 0),
                'maxScore': properties.get('score', {}).get('max', 0),
                'percentage': properties.get('score', {}).get('percentage', 0),
                'controls': []
            }
            
            for control in controls:
                ctrl_props = control.get('properties', {})
                result['controls'].append({
                    'displayName': ctrl_props.get('displayName', 'Unknown'),
                    'score': ctrl_props.get('score', {}).get('current', 0),
                    'maxScore': ctrl_props.get('score', {}).get('max', 0),
                    'healthyResources': ctrl_props.get('healthyResourceCount', 0),
                    'unhealthyResources': ctrl_props.get('unhealthyResourceCount', 0),
                    'notApplicableResources': ctrl_props.get('notApplicableResourceCount', 0),
                })
            
            logger.info(f"Fetched secure score: {result['score']}/{result['maxScore']} ({result['percentage']}%)")
            return result
        except Exception as e:
            logger.error(f"Error fetching secure score: {str(e)}")
            return {'score': 0, 'maxScore': 0, 'percentage': 0, 'controls': []}
    
    def get_key_vault_access_policies(self):
        """Get Azure Key Vault access policies"""
        try:
            # Get all key vaults
            key_vaults = self._get_all_pages(
                f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.KeyVault/vaults?api-version=2023-02-01"
            )
            
            access_policies = []
            for vault in key_vaults:
                vault_name = vault.get('name', 'Unknown')
                vault_props = vault.get('properties', {})
                policies = vault_props.get('accessPolicies', [])
                
                for policy in policies:
                    access_policies.append({
                        'vaultName': vault_name,
                        'vaultResourceGroup': vault.get('id', '').split('/')[4] if '/' in vault.get('id', '') else 'Unknown',
                        'tenantId': policy.get('tenantId', 'Unknown'),
                        'objectId': policy.get('objectId', 'Unknown'),
                        'applicationId': policy.get('applicationId', ''),
                        'permissions': {
                            'keys': ', '.join(policy.get('permissions', {}).get('keys', [])),
                            'secrets': ', '.join(policy.get('permissions', {}).get('secrets', [])),
                            'certificates': ', '.join(policy.get('permissions', {}).get('certificates', [])),
                        }
                    })
            
            logger.info(f"Fetched {len(access_policies)} Key Vault access policies from {len(key_vaults)} vaults")
            return access_policies
        except Exception as e:
            logger.error(f"Error fetching Key Vault access policies: {str(e)}")
            return []
    
    def get_nsg_flow_logs(self):
        """Get NSG Flow Log configurations"""
        try:
            # Get all network watchers
            watchers = self._get_all_pages(
                f"https://management.azure.com/subscriptions/{self.subscription_id}/providers/Microsoft.Network/networkWatchers?api-version=2023-05-01"
            )
            
            flow_logs = []
            for watcher in watchers:
                watcher_name = watcher.get('name', 'Unknown')
                resource_group = watcher.get('location', 'Unknown')
                
                # Get flow logs for this watcher
                try:
                    watcher_rg = watcher.get('id', '').split('/')[4] if '/' in watcher.get('id', '') else resource_group
                    flow_log_url = f"https://management.azure.com/subscriptions/{self.subscription_id}/resourceGroups/{watcher_rg}/providers/Microsoft.Network/networkWatchers/{watcher_name}/flowLogs?api-version=2023-05-01"
                    watcher_flow_logs = self._get_all_pages(flow_log_url)
                    
                    for log in watcher_flow_logs:
                        props = log.get('properties', {})
                        flow_logs.append({
                            'name': log.get('name', 'Unknown'),
                            'location': log.get('location', 'Unknown'),
                            'targetResourceId': props.get('targetResourceId', ''),
                            'storageId': props.get('storageId', ''),
                            'enabled': props.get('enabled', False),
                            'retentionDays': props.get('retentionPolicy', {}).get('days', 0),
                            'format': props.get('format', {}).get('type', 'Unknown'),
                            'version': props.get('format', {}).get('version', 0),
                        })
                except Exception as inner_e:
                    logger.warning(f"Could not fetch flow logs for watcher {watcher_name}: {str(inner_e)}")
            
            logger.info(f"Fetched {len(flow_logs)} NSG flow log configurations")
            return flow_logs
        except Exception as e:
            logger.error(f"Error fetching NSG flow logs: {str(e)}")
            return []
