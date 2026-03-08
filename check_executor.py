#!/usr/bin/env python3
"""
Cirque IT Asset Tracker - Check Execution Engine
Runs monitoring checks based on profiles and generates alerts
"""

import os
import sys
import time
import json
import socket
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

class CheckExecutor:
    def __init__(self):
        self.running = True
        self.check_threads = {}
        self.last_check_times = {}
        
    def log(self, message):
        """Log message with timestamp"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {message}")
    
    def get_assets_with_profiles(self):
        """Get all assets that have monitoring profiles assigned"""
        with app.app_context():
            query = text("""
                SELECT DISTINCT 
                    a.id, a.hostname, a.ip_address, a.asset_type,
                    p.id as profile_id, p.name as profile_name
                FROM assets a
                JOIN asset_monitoring_profile amp ON a.id = amp.asset_id
                JOIN monitoring_profile p ON amp.profile_id = p.id
                WHERE a.is_active = 1
            """)
            result = db.session.execute(query)
            return [dict(row._mapping) for row in result]
    
    def get_profile_checks(self, profile_id):
        """Get all checks for a monitoring profile"""
        with app.app_context():
            query = text("""
                SELECT 
                    mc.id, mc.name, mc.description, mc.check_type, mc.check_command,
                    mc.warning_threshold, mc.critical_threshold, mc.check_interval_minutes,
                    mc.alert_after_failures, mc.enabled
                FROM monitoring_check mc
                JOIN profile_check pc ON mc.id = pc.check_id
                WHERE pc.profile_id = :profile_id AND mc.enabled = 1
                ORDER BY mc.id
            """)
            result = db.session.execute(query, {'profile_id': profile_id})
            return [dict(row._mapping) for row in result]
    
    def should_run_check(self, asset_id, check_id, interval_minutes):
        """Determine if enough time has passed to run check again"""
        check_key = f"{asset_id}_{check_id}"
        last_run = self.last_check_times.get(check_key)
        
        if not last_run:
            return True
        
        time_since_last = datetime.now() - last_run
        return time_since_last >= timedelta(minutes=interval_minutes)
    
    def record_check_time(self, asset_id, check_id):
        """Record when a check was last run"""
        check_key = f"{asset_id}_{check_id}"
        self.last_check_times[check_key] = datetime.now()
    
    def execute_windows_check(self, asset, check):
        """Execute check on Windows asset via RMM gateway"""
        try:
            # Build PowerShell command based on check type
            ps_command = self.build_windows_command(check)
            
            # Execute via RMM gateway
            result = self.execute_via_rmm(asset['id'], ps_command)
            
            return self.parse_check_result(result, check)
        
        except Exception as e:
            self.log(f"Error executing Windows check on {asset['hostname']}: {e}")
            return {
                'success': False,
                'status': 'error',
                'value': None,
                'message': str(e)
            }
    
    def build_windows_command(self, check):
        """Build PowerShell command for Windows check"""
        check_type = check['check_type']
        
        if check_type == 'cpu':
            return "(Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples.CookedValue"
        
        elif check_type == 'memory':
            return """
$mem = Get-CimInstance Win32_OperatingSystem
$used = $mem.TotalVisibleMemorySize - $mem.FreePhysicalMemory
$percent = [math]::Round(($used / $mem.TotalVisibleMemorySize) * 100, 2)
$percent
"""
        
        elif check_type == 'disk':
            # Assuming check_command contains drive letter like "C:"
            drive = check.get('check_command', 'C:')
            return f"(Get-PSDrive {drive.rstrip(':')}).Used / (Get-PSDrive {drive.rstrip(':')}).Used + (Get-PSDrive {drive.rstrip(':')}).Free * 100"
        
        elif check_type == 'service':
            # check_command contains service name
            service = check['check_command']
            return f"(Get-Service '{service}').Status"
        
        elif check_type == 'process':
            # check_command contains process name
            process = check['check_command']
            return f"(Get-Process '{process}' -ErrorAction SilentlyContinue) -ne $null"
        
        elif check_type == 'port':
            # check_command contains port number
            port = check['check_command']
            return f"Test-NetConnection -ComputerName localhost -Port {port} -InformationLevel Quiet"
        
        elif check_type == 'event_log':
            # Check for critical events in last hour
            return """
$events = Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; StartTime=(Get-Date).AddHours(-1)} -ErrorAction SilentlyContinue
$events.Count
"""
        
        elif check_type == 'certificate':
            # Check certificate expiration
            return """
$cert = Get-ChildItem Cert:\\LocalMachine\\My | Sort-Object NotAfter | Select-Object -First 1
if ($cert) { ($cert.NotAfter - (Get-Date)).Days } else { 9999 }
"""
        
        elif check_type == 'custom':
            # Use provided check_command directly
            return check['check_command']
        
        return check.get('check_command', 'echo "Unknown check type"')
    
    def execute_via_rmm(self, asset_id, command):
        """Execute command via RMM gateway"""
        with app.app_context():
            # Check if asset has active RMM connection
            query = text("""
                SELECT agent_id, last_seen 
                FROM rmm_gateway 
                WHERE asset_id = :asset_id 
                AND is_connected = 1
                ORDER BY last_seen DESC 
                LIMIT 1
            """)
            result = db.session.execute(query, {'asset_id': asset_id}).fetchone()
            
            if not result:
                return {'success': False, 'message': 'No active RMM connection'}
            
            agent_id = result[0]
            
            # Queue command for execution
            cmd_id = self.queue_rmm_command(agent_id, command)
            
            # Wait for result (max 30 seconds)
            return self.wait_for_rmm_result(cmd_id, timeout=30)
    
    def queue_rmm_command(self, agent_id, command):
        """Queue command in RMM system"""
        with app.app_context():
            query = text("""
                INSERT INTO rmm_commands (agent_id, command, command_type, status, created_at)
                VALUES (:agent_id, :command, 'powershell', 'pending', :created_at)
            """)
            db.session.execute(query, {
                'agent_id': agent_id,
                'command': command,
                'created_at': datetime.utcnow()
            })
            db.session.commit()
            return db.session.execute(text("SELECT last_insert_rowid()")).scalar()
    
    def wait_for_rmm_result(self, command_id, timeout=30):
        """Wait for RMM command result"""
        start_time = time.time()
        
        with app.app_context():
            while time.time() - start_time < timeout:
                query = text("""
                    SELECT status, result, exit_code 
                    FROM rmm_commands 
                    WHERE id = :cmd_id
                """)
                result = db.session.execute(query, {'cmd_id': command_id}).fetchone()
                
                if result and result[0] == 'completed':
                    return {
                        'success': True,
                        'output': result[1],
                        'exit_code': result[2]
                    }
                elif result and result[0] == 'failed':
                    return {
                        'success': False,
                        'message': result[1] or 'Command failed'
                    }
                
                time.sleep(1)
        
        return {'success': False, 'message': 'Command timeout'}
    
    def execute_linux_check(self, asset, check):
        """Execute check on Linux asset via SSH or agent"""
        try:
            # First try agent heartbeat data if available
            agent_result = self.check_linux_agent_data(asset['id'], check)
            if agent_result:
                return agent_result
            
            # Fall back to SSH execution
            return self.execute_via_ssh(asset, check)
        
        except Exception as e:
            self.log(f"Error executing Linux check on {asset['hostname']}: {e}")
            return {
                'success': False,
                'status': 'error',
                'value': None,
                'message': str(e)
            }
    
    def check_linux_agent_data(self, asset_id, check):
        """Check if Linux agent has recent data for this check"""
        with app.app_context():
            # Get most recent agent heartbeat (within last 10 minutes)
            query = text("""
                SELECT metrics 
                FROM linux_agent_heartbeat 
                WHERE asset_id = :asset_id 
                AND timestamp > datetime('now', '-10 minutes')
                ORDER BY timestamp DESC 
                LIMIT 1
            """)
            result = db.session.execute(query, {'asset_id': asset_id}).fetchone()
            
            if not result:
                return None
            
            metrics = json.loads(result[0])
            check_type = check['check_type']
            
            # Map check types to metrics
            if check_type == 'cpu' and 'cpu_percent' in metrics:
                return {
                    'success': True,
                    'status': 'ok',
                    'value': metrics['cpu_percent'],
                    'message': f"CPU: {metrics['cpu_percent']}%"
                }
            elif check_type == 'memory' and 'memory_percent' in metrics:
                return {
                    'success': True,
                    'status': 'ok',
                    'value': metrics['memory_percent'],
                    'message': f"Memory: {metrics['memory_percent']}%"
                }
            elif check_type == 'disk' and 'disk_percent' in metrics:
                return {
                    'success': True,
                    'status': 'ok',
                    'value': metrics['disk_percent'],
                    'message': f"Disk: {metrics['disk_percent']}%"
                }
        
        return None
    
    def execute_via_ssh(self, asset, check):
        """Execute check via SSH"""
        # This would require SSH credentials and paramiko library
        # For now, return not implemented
        return {
            'success': False,
            'status': 'error',
            'value': None,
            'message': 'SSH execution not yet implemented'
        }
    
    def parse_check_result(self, result, check):
        """Parse check result and determine status"""
        if not result.get('success'):
            return {
                'success': False,
                'status': 'error',
                'value': None,
                'message': result.get('message', 'Check failed')
            }
        
        try:
            # Extract value from output
            output = result.get('output', '').strip()
            
            # Try to parse as number
            try:
                value = float(output)
            except (ValueError, TypeError):
                # For non-numeric checks (service status, etc.)
                value = output
            
            # Determine status based on thresholds
            status = 'ok'
            message = f"Value: {value}"
            
            if check['check_type'] in ['cpu', 'memory', 'disk']:
                # Numeric threshold checks
                critical = check.get('critical_threshold')
                warning = check.get('warning_threshold')
                
                if critical and isinstance(value, (int, float)) and value >= critical:
                    status = 'critical'
                    message = f"CRITICAL: {value}% (threshold: {critical}%)"
                elif warning and isinstance(value, (int, float)) and value >= warning:
                    status = 'warning'
                    message = f"WARNING: {value}% (threshold: {warning}%)"
                else:
                    message = f"OK: {value}%"
            
            elif check['check_type'] == 'service':
                # Service check
                if output.lower() in ['running', 'true', '1']:
                    status = 'ok'
                    message = f"Service is running"
                else:
                    status = 'critical'
                    message = f"Service is not running"
            
            return {
                'success': True,
                'status': status,
                'value': value,
                'message': message
            }
        
        except Exception as e:
            return {
                'success': False,
                'status': 'error',
                'value': None,
                'message': f"Failed to parse result: {e}"
            }
    
    def save_check_result(self, asset_id, check_id, result):
        """Save check result to database"""
        with app.app_context():
            query = text("""
                INSERT INTO monitoring_check_result 
                (asset_id, check_id, status, value, message, checked_at)
                VALUES (:asset_id, :check_id, :status, :value, :message, :checked_at)
            """)
            db.session.execute(query, {
                'asset_id': asset_id,
                'check_id': check_id,
                'status': result['status'],
                'value': str(result['value']) if result['value'] is not None else None,
                'message': result['message'],
                'checked_at': datetime.utcnow()
            })
            db.session.commit()
    
    def check_for_alerts(self, asset_id, check_id, result):
        """Check if alert should be generated"""
        if result['status'] in ['ok', 'unknown']:
            # Clear any existing alerts for this check
            self.clear_alert(asset_id, check_id)
            return
        
        with app.app_context():
            # Get check configuration
            check_query = text("""
                SELECT alert_after_failures 
                FROM monitoring_check 
                WHERE id = :check_id
            """)
            check_config = db.session.execute(check_query, {'check_id': check_id}).fetchone()
            alert_after = check_config[0] if check_config else 1
            
            # Count consecutive failures
            failures_query = text("""
                SELECT COUNT(*) 
                FROM monitoring_check_result 
                WHERE asset_id = :asset_id 
                AND check_id = :check_id 
                AND status IN ('warning', 'critical', 'error')
                AND checked_at > (
                    SELECT MAX(checked_at) 
                    FROM monitoring_check_result 
                    WHERE asset_id = :asset_id 
                    AND check_id = :check_id 
                    AND status = 'ok'
                )
            """)
            failures_count = db.session.execute(failures_query, {
                'asset_id': asset_id,
                'check_id': check_id
            }).scalar() or 0
            
            if failures_count >= alert_after:
                self.generate_alert(asset_id, check_id, result, failures_count)
    
    def generate_alert(self, asset_id, check_id, result, failure_count):
        """Generate monitoring alert"""
        with app.app_context():
            # Check if alert already exists
            existing_query = text("""
                SELECT id 
                FROM monitoring_alert 
                WHERE asset_id = :asset_id 
                AND check_id = :check_id 
                AND status IN ('open', 'acknowledged')
            """)
            existing = db.session.execute(existing_query, {
                'asset_id': asset_id,
                'check_id': check_id
            }).fetchone()
            
            if existing:
                # Update existing alert
                update_query = text("""
                    UPDATE monitoring_alert 
                    SET failure_count = :failure_count,
                        last_failed_at = :last_failed_at,
                        message = :message
                    WHERE id = :alert_id
                """)
                db.session.execute(update_query, {
                    'alert_id': existing[0],
                    'failure_count': failure_count,
                    'last_failed_at': datetime.utcnow(),
                    'message': result['message']
                })
            else:
                # Create new alert
                insert_query = text("""
                    INSERT INTO monitoring_alert 
                    (asset_id, check_id, severity, status, message, failure_count, 
                     first_failed_at, last_failed_at, created_at)
                    VALUES (:asset_id, :check_id, :severity, 'open', :message, 
                            :failure_count, :failed_at, :failed_at, :created_at)
                """)
                db.session.execute(insert_query, {
                    'asset_id': asset_id,
                    'check_id': check_id,
                    'severity': result['status'],
                    'message': result['message'],
                    'failure_count': failure_count,
                    'failed_at': datetime.utcnow(),
                    'created_at': datetime.utcnow()
                })
                
                # Send email notification
                self.send_alert_email(asset_id, check_id, result)
            
            db.session.commit()
    
    def clear_alert(self, asset_id, check_id):
        """Clear/resolve alert when check passes"""
        with app.app_context():
            query = text("""
                UPDATE monitoring_alert 
                SET status = 'resolved', 
                    resolved_at = :resolved_at 
                WHERE asset_id = :asset_id 
                AND check_id = :check_id 
                AND status IN ('open', 'acknowledged')
            """)
            db.session.execute(query, {
                'asset_id': asset_id,
                'check_id': check_id,
                'resolved_at': datetime.utcnow()
            })
            db.session.commit()
    
    def send_alert_email(self, asset_id, check_id, result):
        """Send email notification for alert"""
        try:
            with app.app_context():
                # Get asset and check details
                query = text("""
                    SELECT a.hostname, a.ip_address, mc.name, mc.description
                    FROM assets a
                    JOIN monitoring_check mc ON mc.id = :check_id
                    WHERE a.id = :asset_id
                """)
                info = db.session.execute(query, {
                    'asset_id': asset_id,
                    'check_id': check_id
                }).fetchone()
                
                if not info:
                    return
                
                hostname, ip, check_name, check_desc = info
                
                # Build email
                subject = f"[{result['status'].upper()}] {hostname} - {check_name}"
                body = f"""
Monitoring Alert

Asset: {hostname} ({ip})
Check: {check_name}
Status: {result['status'].upper()}
Message: {result['message']}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Description: {check_desc}

--
Cirque IT Asset Tracker
"""
                
                # Get email settings from config
                # For now, log instead of sending
                self.log(f"ALERT EMAIL: {subject}")
                self.log(f"  To: itops@cirque.com")
                self.log(f"  {result['message']}")
        
        except Exception as e:
            self.log(f"Error sending alert email: {e}")
    
    def run_checks(self):
        """Main loop to run all checks"""
        self.log("Check Executor started")
        
        while self.running:
            try:
                # Get all assets with monitoring profiles
                assets = self.get_assets_with_profiles()
                
                for asset in assets:
                    # Get checks for this asset's profile
                    checks = self.get_profile_checks(asset['profile_id'])
                    
                    for check in checks:
                        # Check if it's time to run this check
                        if not self.should_run_check(asset['id'], check['id'], check['check_interval_minutes']):
                            continue
                        
                        self.log(f"Running check '{check['name']}' on {asset['hostname']}")
                        
                        # Execute check based on asset type
                        if asset['asset_type'] in ['Windows Server', 'Windows Workstation']:
                            result = self.execute_windows_check(asset, check)
                        elif asset['asset_type'] in ['Linux Server', 'Linux Workstation']:
                            result = self.execute_linux_check(asset, check)
                        else:
                            result = {
                                'success': False,
                                'status': 'error',
                                'value': None,
                                'message': f"Unsupported asset type: {asset['asset_type']}"
                            }
                        
                        # Save result
                        self.save_check_result(asset['id'], check['id'], result)
                        
                        # Check for alerts
                        self.check_for_alerts(asset['id'], check['id'], result)
                        
                        # Record check time
                        self.record_check_time(asset['id'], check['id'])
                
                # Sleep for 60 seconds before next cycle
                time.sleep(60)
            
            except Exception as e:
                self.log(f"Error in check loop: {e}")
                time.sleep(60)
    
    def stop(self):
        """Stop the executor"""
        self.log("Stopping Check Executor...")
        self.running = False


def main():
    executor = CheckExecutor()
    
    try:
        executor.run_checks()
    except KeyboardInterrupt:
        executor.stop()


if __name__ == '__main__':
    main()
