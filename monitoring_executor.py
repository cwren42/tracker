#!/usr/bin/env python3
"""
Monitoring Check Execution Engine
Executes monitoring checks for assets based on their assigned profiles
Generates alerts when checks fail
"""

import os
import sys
import time
import json
import logging
import signal
from datetime import datetime, timedelta
from pathlib import Path

# Add app directory to path
sys.path.insert(0, '/var/www/tracker')

# Import Flask app components
from app import app, db
from app import Asset, MonitoringProfile, MonitoringCheck, MonitoringAlert
from app import ProfileCheck, AssetMonitoringProfile, MaintenanceWindow
from sqlalchemy import and_, or_, text

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/www/tracker/monitoring_executor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MonitoringExecutor:
    # Threshold metric checks are debounced: a single over-threshold reading is
    # held; an alert only fires on the 2nd consecutive failure. Protects against
    # transient telemetry artifacts (e.g. psutil intermittently reporting 100%).
    DEBOUNCE_TYPES = {'cpu', 'memory', 'disk'}
    DEBOUNCE_MIN_CONSEC = 2

    def __init__(self):
        self.running = False
        self.last_cleanup = datetime.utcnow()
        self._consec_fail = {}  # (asset_id, check_id) -> consecutive failure count
        
    def is_in_maintenance_window(self, asset):
        """Check if asset is currently in a maintenance window"""
        # MaintenanceWindow model doesn't have asset_id yet
        # TODO: Add asset-specific maintenance windows
        # For now always return False
        return False
    
    def get_assets_to_monitor(self):
        """Get all assets with assigned monitoring profiles"""
        # Query using join since AssetMonitoringProfile is a Table, not a Model
        results = db.session.query(Asset, MonitoringProfile).join(
            AssetMonitoringProfile, Asset.id == AssetMonitoringProfile.c.asset_id
        ).join(
            MonitoringProfile, AssetMonitoringProfile.c.profile_id == MonitoringProfile.id
        ).filter(
            MonitoringProfile.enabled == True
        ).all()
        
        assets_with_profiles = []
        for asset, profile in results:
            assets_with_profiles.append({
                'asset': asset,
                'profile': profile,
                'assignment': None  # Table doesn't have additional fields
            })
        
        logger.info(f"Found {len(assets_with_profiles)} assets with active monitoring profiles")
        return assets_with_profiles
    
    def get_checks_for_profile(self, profile):
        """Get all checks assigned to a monitoring profile"""
        # Query using join since ProfileCheck is a Table, not a Model
        results = db.session.query(
            MonitoringCheck, 
            ProfileCheck.c.check_interval_override,
            ProfileCheck.c.warning_threshold,
            ProfileCheck.c.critical_threshold,
            ProfileCheck.c.parameters
        ).join(
            ProfileCheck, MonitoringCheck.id == ProfileCheck.c.check_id
        ).filter(
            ProfileCheck.c.profile_id == profile.id,
            MonitoringCheck.enabled == True
        ).all()
        
        checks = []
        for check, interval_override, warning_threshold, critical_threshold, parameters in results:
            # Use override if set, otherwise use profile's default interval
            interval = interval_override if interval_override else profile.check_interval_minutes
            checks.append({
                'check': check,
                'interval': interval,
                'warning_threshold': warning_threshold,
                'critical_threshold': critical_threshold,
                'parameters': parameters
            })
        
        return checks
    
    def should_run_check(self, asset, check, check_info, last_result_time):
        """Determine if a check should run now based on interval"""
        if not last_result_time:
            return True
        
        # Get interval from check_info
        interval_minutes = check_info.get('interval', 5)
        
        elapsed = (datetime.utcnow() - last_result_time).total_seconds() / 60
        should_run = elapsed >= interval_minutes
        
        if not should_run:
            logger.debug(f"Skipping {check.name} for {asset.name}: {elapsed:.1f}/{interval_minutes} minutes elapsed")
        
        return should_run
    
    def execute_check(self, asset, check, check_info, profile):
        """Execute a monitoring check for an asset"""
        logger.info(f"Executing check '{check.name}' for {asset.name}")
        
        check_type = check.check_type
        params = json.loads(check_info.get('parameters', '{}')) if check_info.get('parameters') else {}
        
        result = {
            'asset_id': asset.id,
            'check_id': check.id,
            'check_name': check.name,
           'check_type': check_type,
            'timestamp': datetime.utcnow(),
            'success': False,
            'value': None,
            'message': '',
            'response_time_ms': 0
        }
        
        start_time = time.time()
        
        try:
            # Execute check based on type (normalize linux_* to generic types)
            normalized_type = check_type.replace('linux_', '').replace('windows_', '')
            
            if normalized_type == 'cpu' or check_type == 'cpu':
                result.update(self.check_cpu(asset, params))
            
            elif normalized_type == 'memory' or check_type == 'memory':
                result.update(self.check_memory(asset, params))
            
            elif normalized_type == 'disk' or check_type == 'disk':
                result.update(self.check_disk(asset, params))
            
            elif check_type == 'service':
                result.update(self.check_service(asset, params))
            
            elif check_type == 'port':
                result.update(self.check_port(asset, params))
            
            elif check_type == 'process':
                result.update(self.check_process(asset, params))
            
            elif check_type == 'certificate':
                result.update(self.check_certificate(asset, params))
            
            elif check_type == 'http':
                result.update(self.check_http(asset, params))
            
            elif check_type == 'dns':
                result.update(self.check_dns(asset, params))
            
            elif check_type == 'ping':
                result.update(self.check_ping(asset, params))
            
            elif check_type == 'custom':
                result.update(self.check_custom(asset, params))
            
            elif normalized_type in ('status', 'selinux_status', 'uptime', 'agent_version',
                                     'linux_load', 'linux_updates', 'linux_load_avg',
                                     'load', 'updates', 'patches'):
                # Informational / agent-reported checks — the data comes from agent
                # telemetry, not an active server-side probe.
                result['success'] = True
                result['message'] = f"{check.name}: informational check (no active probe)"

            else:
                # An unknown check type is a server-side config gap, not a device
                # problem — mark it skipped so it doesn't raise a critical device alert.
                result['success'] = True
                result['message'] = f"Unsupported check type '{check_type}' — skipped (no active probe)"
        
        except Exception as e:
            logger.error(f"Error executing check {check.name}: {e}")
            result['success'] = False
            result['message'] = f"Error: {str(e)}"
        
        # Calculate response time
        result['response_time_ms'] = int((time.time() - start_time) * 1000)
        
        # Store result in database
        self.store_check_result(result)
        
        # Generate or resolve alerts based on result
        norm_type = check_type.replace('linux_', '').replace('windows_', '')
        key = (asset.id, check.id)
        if not result['success']:
            if norm_type in self.DEBOUNCE_TYPES or check_type in self.DEBOUNCE_TYPES:
                n = self._consec_fail.get(key, 0) + 1
                self._consec_fail[key] = n
                if n >= self.DEBOUNCE_MIN_CONSEC:
                    self.generate_alert(asset, check, result, profile)
                else:
                    logger.info(
                        f"Debounce: {check.name} on {asset.name} over threshold "
                        f"({n}/{self.DEBOUNCE_MIN_CONSEC}) — holding, no alert yet"
                    )
            else:
                self.generate_alert(asset, check, result, profile)
        else:
            self._consec_fail.pop(key, None)
            self.auto_resolve_alerts(asset, check)

        return result
    
    def check_cpu(self, asset, params):
        """Check CPU usage"""
        threshold = params.get('critical_threshold', 90)
        warning_threshold = params.get('warning_threshold', 80)
        
        # Get CPU telemetry from RMM agent or last known value
        cpu_usage = self.get_asset_telemetry(asset, 'cpu_usage')
        
        if cpu_usage is None:
            # No fresh telemetry (asset offline / not reporting) -> skip, don't alert.
            return {
                'success': True,
                'message': 'CPU usage: no fresh telemetry (skipped)',
                'value': None
            }
        
        success = cpu_usage < threshold
        severity = 'ok'
        
        if cpu_usage >= threshold:
            severity = 'critical'
        elif cpu_usage >= warning_threshold:
            severity = 'warning'
        
        return {
            'success': success,
            'value': cpu_usage,
            'message': f"CPU usage: {cpu_usage}% ({severity})",
            'severity': severity
        }
    
    def check_memory(self, asset, params):
        """Check memory usage"""
        threshold = params.get('critical_threshold', 90)
        warning_threshold = params.get('warning_threshold', 80)
        
        mem_usage = self.get_asset_telemetry(asset, 'memory_percent')
        
        if mem_usage is None:
            # No fresh telemetry (asset offline / not reporting) -> skip, don't alert.
            return {
                'success': True,
                'message': 'Memory usage: no fresh telemetry (skipped)',
                'value': None
            }
        
        success = mem_usage < threshold
        severity = 'ok'
        
        if mem_usage >= threshold:
            severity = 'critical'
        elif mem_usage >= warning_threshold:
            severity = 'warning'
        
        return {
            'success': success,
            'value': mem_usage,
            'message': f"Memory usage: {mem_usage}% ({severity})",
            'severity': severity
        }
    
    def check_disk(self, asset, params):
        """Check disk usage"""
        threshold = params.get('critical_threshold', 90)
        warning_threshold = params.get('warning_threshold', 80)
        # Default to root for Linux, C: for Windows
        mountpoint = params.get('mountpoint', '/')
        
        disk_usage = self.get_asset_telemetry(asset, 'disk_usage')
        
        if disk_usage is None:
            # No fresh telemetry (asset offline / not reporting) -> skip, don't alert.
            return {
                'success': True,
                'message': f'Disk usage ({mountpoint}): no fresh telemetry (skipped)',
                'value': None
            }
        
        success = disk_usage < threshold
        severity = 'ok'
        
        if disk_usage >= threshold:
            severity = 'critical'
        elif disk_usage >= warning_threshold:
            severity = 'warning'
        
        return {
            'success': success,
            'value': disk_usage,
            'message': f"Disk {mountpoint}: {disk_usage}% used ({severity})",
            'severity': severity
        }
    
    def check_service(self, asset, params):
        """Check if a service is running"""
        service_name = params.get('service_name')
        
        if not service_name:
            return {
                'success': False,
                'message': 'Service name not specified',
                'value': None
            }
        
        # Query RMM agent or use last known status
        service_status = self.check_asset_service(asset, service_name)

        if service_status is None:
            # No fresh telemetry (asset offline) -> skip, don't alert.
            return {
                'success': True,
                'message': f"Service '{service_name}': no fresh telemetry (skipped)",
                'value': None
            }

        success = service_status == 'running'

        return {
            'success': success,
            'value': service_status,
            'message': f"Service '{service_name}': {service_status}",
            'severity': 'critical' if not success else 'ok'
        }
    
    def check_port(self, asset, params):
        """Check if a port is listening"""
        port = params.get('port')
        
        if not port:
            return {
                'success': False,
                'message': 'Port not specified',
                'value': None
            }
        
        # Use socket or RMM agent to check port
        is_listening = self.check_asset_port(asset, port)
        
        return {
            'success': is_listening,
            'value': port,
            'message': f"Port {port}: {'listening' if is_listening else 'not listening'}",
            'severity': 'critical' if not is_listening else 'ok'
        }
    
    def check_process(self, asset, params):
        """Check if a process is running"""
        process_name = params.get('process_name')
        
        if not process_name:
            return {
                'success': False,
                'message': 'Process name not specified',
                'value': None
            }
        
        is_running = self.check_asset_process(asset, process_name)

        if is_running is None:
            # No process telemetry is collected today -> skip rather than guess.
            return {
                'success': True,
                'message': f"Process '{process_name}': no telemetry source (skipped)",
                'value': None
            }

        return {
            'success': is_running,
            'value': process_name,
            'message': f"Process '{process_name}': {'running' if is_running else 'not running'}",
            'severity': 'critical' if not is_running else 'ok'
        }
    
    def check_ping(self, asset, params):
        """Check if asset is reachable via ping"""
        import subprocess
        
        # Use IP address if available, otherwise use hostname
        target = asset.ip_address or asset.name
        
        if not target:
            return {
                'success': False,
                'message': 'No IP address or hostname configured',
                'value': None,
                'severity': 'critical'
            }
        
        # Ping command (send 2 packets with 2 second timeout)
        try:
            # Linux ping command
            result = subprocess.run(
                ['ping', '-c', '2', '-W', '2', target],
                capture_output=True,
                timeout=5
            )
            
            is_reachable = (result.returncode == 0)
            
            return {
                'success': is_reachable,
                'value': 1 if is_reachable else 0,
                'message': f"Ping {target}: {'reachable' if is_reachable else 'unreachable'}",
                'severity': 'critical' if not is_reachable else 'ok'
            }
            
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'value': 0,
                'message': f"Ping {target}: timeout (no response)",
                'severity': 'critical'
            }
        except Exception as e:
            return {
                'success': False,
                'value': None,
                'message': f"Ping {target}: error ({str(e)})",
                'severity': 'critical'
            }
    
    def check_certificate(self, asset, params):
        """Check SSL certificate expiration"""
        import ssl
        import socket
        from datetime import datetime
        
        hostname = params.get('hostname', asset.ip_address or asset.name)
        port = params.get('port', 443)
        warning_days = params.get('warning_days', 30)
        critical_days = params.get('critical_days', 7)
        
        try:
            context = ssl.create_default_context()
            with socket.create_connection((hostname, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()
                    
                    # Parse expiry date
                    expiry_str = cert['notAfter']
                    expiry_date = datetime.strptime(expiry_str, '%b %d %H:%M:%S %Y %Z')
                    
                    days_until_expiry = (expiry_date - datetime.utcnow()).days
                    
                    success = days_until_expiry >= critical_days
                    severity = 'ok'
                    
                    if days_until_expiry < critical_days:
                        severity = 'critical'
                    elif days_until_expiry < warning_days:
                        severity = 'warning'
                    
                    return {
                        'success': success,
                        'value': days_until_expiry,
                        'message': f"Certificate expires in {days_until_expiry} days ({severity})",
                        'severity': severity
                    }
        
        except Exception as e:
            return {
                'success': False,
                'message': f"Certificate check failed: {str(e)}",
                'value': None,
                'severity': 'critical'
            }
    
    def check_http(self, asset, params):
        """Check HTTP endpoint"""
        import requests
        
        url = params.get('url', f"http://{asset.ip_address or asset.name}")
        expected_status = params.get('expected_status', 200)
        timeout = params.get('timeout', 10)
        
        try:
            response = requests.get(url, timeout=timeout, verify=False)
            success = response.status_code == expected_status
            
            return {
                'success': success,
                'value': response.status_code,
                'message': f"HTTP {url}: Status {response.status_code} (expected {expected_status})",
                'severity': 'critical' if not success else 'ok'
            }
        
        except Exception as e:
            return {
                'success': False,
                'message': f"HTTP check failed: {str(e)}",
                'value': None,
                'severity': 'critical'
            }
    
    def check_dns(self, asset, params):
        """Check DNS resolution"""
        import socket
        
        hostname = params.get('hostname', asset.name)
        expected_ip = params.get('expected_ip')
        
        try:
            resolved_ip = socket.gethostbyname(hostname)
            
            if expected_ip:
                success = resolved_ip == expected_ip
                message = f"DNS {hostname}: {resolved_ip} (expected {expected_ip})"
            else:
                success = True
                message = f"DNS {hostname}: {resolved_ip}"
            
            return {
                'success': success,
                'value': resolved_ip,
                'message': message,
                'severity': 'critical' if not success else 'ok'
            }
        
        except Exception as e:
            return {
                'success': False,
                'message': f"DNS check failed: {str(e)}",
                'value': None,
                'severity': 'critical'
            }
    
    def check_ping(self, asset, params):
        """Ping check"""
        import subprocess
        
        target = asset.ip_address or asset.name
        count = params.get('count', 4)
        timeout = params.get('timeout', 5)
        
        try:
            # Use ping command
            cmd = ['ping', '-c', str(count), '-W', str(timeout), target]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
            
            success = result.returncode == 0
            
            # Parse ping output for stats
            output_lines = result.stdout.split('\n')
            message = "Ping successful" if success else "Ping failed"
            
            for line in output_lines:
                if 'packet loss' in line:
                    message = line.strip()
                    break
            
            return {
                'success': success,
                'message': message,
                'value': 1 if success else 0,
                'severity': 'critical' if not success else 'ok'
            }
        
        except Exception as e:
            return {
                'success': False,
                'message': f"Ping check failed: {str(e)}",
                'value': None,
                'severity': 'critical'
            }
    
    def check_custom(self, asset, params):
        """Execute custom check command"""
        command = params.get('command')
        expected_output = params.get('expected_output')
        expected_exit_code = params.get('expected_exit_code', 0)
        
        if not command:
            return {
                'success': False,
                'message': 'Custom command not specified',
                'value': None
            }
        
        # This would need to be executed via RMM agent or SSH
        # For now, return placeholder
        return {
            'success': False,
            'message': 'Custom check execution not yet implemented',
            'value': None,
            'severity': 'warning'
        }
    
    def get_asset_telemetry(self, asset, metric):
        """Get real telemetry for an asset from the RMM agent feed (rmm_telemetry).

        Returns None when the asset has no FRESH telemetry (offline / not reporting)
        so the caller can skip rather than fire a bogus alert. Freshness is gated on
        last_seen (captured_at is agent-clock-skewed and unreliable). Keyed by asset_id;
        hostname is frequently blank in this table.
        """
        row = db.session.execute(text("""
            SELECT cpu_percent, ram_percent, disk_json
            FROM rmm_telemetry
            WHERE asset_id = :aid
              AND last_seen > now() - interval '30 minutes'
            ORDER BY last_seen DESC
            LIMIT 1
        """), {"aid": asset.id}).fetchone()

        if not row:
            return None  # no fresh telemetry -> caller treats as skip, not failure

        cpu_percent, ram_percent, disk_json = row

        if metric == 'cpu_usage':
            return round(float(cpu_percent), 1) if cpu_percent is not None else None

        if metric == 'memory_percent':
            return round(float(ram_percent), 1) if ram_percent is not None else None

        if metric == 'disk_usage':
            # disk_json: [{"mountpoint": "C:\\", "percent": 65.7, "drive_type": 3, "bus_type": "NVMe", ...}]
            # Report the busiest FIXED disk (drive_type 3); ignore removable/network.
            # ALSO exclude USB/SD/MMC external drives: Windows reports USB SSDs/HDDs
            # as drive_type 3 ("fixed"), so drive_type alone can't tell them apart --
            # bus_type does. A full Seagate Expansion must not alert. (bus_type is
            # absent on pre-2.9.58 agents -> those keep the drive_type-only behavior.)
            if not disk_json:
                return None
            try:
                disks = json.loads(disk_json) if isinstance(disk_json, str) else disk_json
            except (ValueError, TypeError):
                return None
            pcts = [d.get('percent') for d in disks
                    if d.get('drive_type') == 3
                    and str(d.get('bus_type') or '').lower() not in ('usb', 'sd', 'mmc')
                    and d.get('percent') is not None]
            return round(max(pcts), 1) if pcts else None

        return None
    
    def check_asset_service(self, asset, service_name):
        """Resolve a named service's state from real agent telemetry.

        Source = rmm_telemetry.services_down: the agent's list of auto-start
        services that are currently Stopped. Returns:
          'stopped' -> service_name is in that list (auto service not running)
          'running' -> fresh telemetry exists and service is NOT listed down
          None      -> no fresh telemetry (asset offline) -> caller skips
        Matches the service short name OR display name, case-insensitive.
        """
        row = db.session.execute(text("""
            SELECT services_down
            FROM rmm_telemetry
            WHERE asset_id = :aid
              AND last_seen > now() - interval '30 minutes'
            ORDER BY last_seen DESC
            LIMIT 1
        """), {"aid": asset.id}).fetchone()
        if not row:
            return None
        try:
            down = json.loads(row[0]) if row[0] else []
        except (ValueError, TypeError):
            down = []
        target = (service_name or '').strip().lower()
        for svc in down:
            if target in (str(svc.get('name', '')).lower(),
                          str(svc.get('display', '')).lower()):
                return 'stopped'
        return 'running'
    
    def check_asset_port(self, asset, port):
        """Check if port is listening"""
        import socket
        
        target = asset.ip_address or asset.name
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((target, int(port)))
            sock.close()
            return result == 0
        except:
            return False
    
    def check_asset_process(self, asset, process_name):
        """Per-process state is NOT collected in telemetry today, so this cannot be
        answered from stored data. Return None so check_process skips rather than
        inventing a result (was previously random). A real implementation needs a
        live agent probe or a new process-list telemetry field."""
        return None
    
    def store_check_result(self, result):
        """Store check result in database"""
        try:
            # Store in monitoring_check_result table (if it exists)
            # For now, just log it
            logger.info(f"Check result: {result['check_name']} - {'PASS' if result['success'] else 'FAIL'}: {result['message']}")
        except Exception as e:
            logger.error(f"Error storing check result: {e}")
    
    def generate_alert(self, asset, check, result, profile):
        """Generate a monitoring alert when check fails"""
        try:
            # Check if alert already exists for this asset+check combination
            existing_alert = MonitoringAlert.query.filter_by(
                asset_id=asset.id,
                check_id=check.id,
                status='open'
            ).first()
            
            now = datetime.utcnow()
            
            if existing_alert:
                # Update existing alert
                existing_alert.failure_count = (existing_alert.failure_count or 0) + 1
                existing_alert.last_failed_at = now
                existing_alert.message = result.get('message', 'Check failed')
                existing_alert.details = json.dumps(result, default=str)
                logger.info(f"Updated existing alert #{existing_alert.id} (failure #{existing_alert.failure_count})")
            else:
                # Create new alert
                alert = MonitoringAlert(
                    asset_id=asset.id,
                    check_id=check.id,
                    severity=result.get('severity', 'critical'),
                    message=f"{check.name} failed on {asset.name}",
                    details=json.dumps(result, default=str),
                    status='open',
                    triggered_at=now,
                    failure_count=1,
                    first_failed_at=now,
                    last_failed_at=now
                )
                db.session.add(alert)
                logger.warning(f"Generated new alert: {asset.name} - {check.name}: {result['message']}")
                
            db.session.commit()
        
        except Exception as e:
            logger.error(f"Error generating alert: {e}")
            db.session.rollback()
    
    def auto_resolve_alerts(self, asset, check):
        """Auto-resolve alerts when check passes"""
        try:
            existing_alerts = MonitoringAlert.query.filter_by(
                asset_id=asset.id,
                check_id=check.id,
                status='open'
            ).all()
            
            for alert in existing_alerts:
                alert.status = 'resolved'
                alert.resolved_at = datetime.utcnow()
                logger.info(f"Auto-resolved alert: {asset.name} - {check.name}")
            
            if existing_alerts:
                db.session.commit()
        
        except Exception as e:
            logger.error(f"Error auto-resolving alerts: {e}")
            db.session.rollback()
    
    def cleanup_old_alerts(self):
        """Clean up old resolved alerts"""
        try:
            # Delete resolved alerts older than 30 days
            cutoff_date = datetime.utcnow() - timedelta(days=30)
            
            old_alerts = MonitoringAlert.query.filter(
                and_(
                    MonitoringAlert.status == 'resolved',
                    MonitoringAlert.resolved_at < cutoff_date
                )
            ).all()
            
            for alert in old_alerts:
                db.session.delete(alert)
            
            if old_alerts:
                db.session.commit()
                logger.info(f"Cleaned up {len(old_alerts)} old alerts")
        
        except Exception as e:
            logger.error(f"Error cleaning up alerts: {e}")
            db.session.rollback()
    
    def run_monitoring_cycle(self):
        """Execute one monitoring cycle"""
        logger.info("=== Starting monitoring cycle ===")
        
        with app.app_context():
            assets_with_profiles = self.get_assets_to_monitor()
            
            if not assets_with_profiles:
                logger.warning("No assets with monitoring profiles found")
                return
            
            total_checks = 0
            successful_checks = 0
            failed_checks = 0
            
            for item in assets_with_profiles:
                asset = item['asset']
                profile = item['profile']
                
                # Skip if in maintenance window
                if self.is_in_maintenance_window(asset):
                    logger.info(f"Skipping {asset.name} - in maintenance window")
                    continue
                
                logger.info(f"Processing {asset.name} with profile '{profile.name}'")
                
                # Get checks for this profile
                checks = self.get_checks_for_profile(profile)
                
                for check_info in checks:
                    check = check_info['check']
                    
                    # Execute check
                    result = self.execute_check(asset, check, check_info, profile)
                    
                    total_checks += 1
                    if result['success']:
                        successful_checks += 1
                    else:
                        failed_checks += 1
                    
                    # Small delay between checks to avoid overload
                    time.sleep(0.5)
            
            logger.info(f"=== Monitoring cycle complete: {total_checks} checks ({successful_checks} passed, {failed_checks} failed) ===")
            
            # Cleanup old alerts periodically (once per hour)
            if (datetime.utcnow() - self.last_cleanup).total_seconds() > 3600:
                self.cleanup_old_alerts()
                self.last_cleanup = datetime.utcnow()
    
    def run(self, interval=300):
        """Run monitoring executor continuously"""
        self.running = True
        logger.info(f"Starting Monitoring Executor (interval: {interval}s)")
        
        # Handle shutdown signals
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
        try:
            while self.running:
                self.run_monitoring_cycle()
                
                # Wait for next interval
                logger.info(f"Waiting {interval} seconds until next cycle...")
                time.sleep(interval)
        
        except Exception as e:
            logger.error(f"Fatal error in monitoring executor: {e}")
        finally:
            logger.info("Monitoring Executor stopped")
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Monitoring Check Execution Engine')
    parser.add_argument('--interval', type=int, default=300, help='Check interval in seconds (default: 300)')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    
    args = parser.parse_args()
    
    executor = MonitoringExecutor()
    
    if args.once:
        executor.run_monitoring_cycle()
    else:
        executor.run(interval=args.interval)
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
