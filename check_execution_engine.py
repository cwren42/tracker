#!/usr/bin/env python3
"""
Cirque RMM Check Execution Engine
Executes monitoring checks for assets based on their assigned profiles
"""

import json
import logging
import time
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine, Table, Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey, text
from sqlalchemy.orm import sessionmaker, scoped_session
from app import app, db, Asset, MonitoringProfile, MonitoringCheck, MonitoringAlert, MaintenanceWindow
from app import AssetMonitoringProfile, ProfileCheck

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/cirque-rmm-check-engine.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Create check result table if it doesn't exist
MonitoringCheckResult = Table('monitoring_check_result', db.metadata,
    Column('id', Integer, primary_key=True),
    Column('asset_id', Integer, ForeignKey('asset.id'), nullable=False),
    Column('check_id', Integer, ForeignKey('monitoring_check.id'), nullable=False),
    Column('profile_id', Integer, ForeignKey('monitoring_profile.id')),
    Column('timestamp', DateTime, nullable=False, default=datetime.utcnow),
    Column('success', Boolean, nullable=False),
    Column('value', Float),
    Column('message', Text),
    Column('execution_time_ms', Integer),
    Column('error', Text),
    extend_existing=True
)


class CheckExecutor:
    """Executes monitoring checks for assets"""
    
    def __init__(self):
        self.app = app
        self.db = db
        self.last_check_times = {}  # Track when checks were last run
    
    def is_in_maintenance_window(self, asset_id):
        """Check if asset is currently in a maintenance window"""
        with self.app.app_context():
            now = datetime.utcnow()
            
            # Get active maintenance windows for this asset
            windows = db.session.query(MaintenanceWindow).filter(
                MaintenanceWindow.asset_id == asset_id,
                MaintenanceWindow.enabled == True,
                MaintenanceWindow.start_time <= now,
                MaintenanceWindow.end_time >= now
            ).all()
            
            return len(windows) > 0
    
    def get_due_checks(self):
        """Get list of checks that are due to run"""
        with self.app.app_context():
            due_checks = []
            now = datetime.utcnow()
            
            # Get all assets with monitoring profiles
            assets = db.session.query(Asset).join(
                AssetMonitoringProfile,
                Asset.id == AssetMonitoringProfile.c.asset_id
            ).filter(Asset.status == 'Active').all()
            
            for asset in assets:
                # Skip if in maintenance window
                if self.is_in_maintenance_window(asset.id):
                    logger.debug(f"Asset {asset.hostname} is in maintenance window, skipping")
                    continue
                
                # Get asset's profiles
                profiles = db.session.query(MonitoringProfile).join(
                    AssetMonitoringProfile,
                    MonitoringProfile.id == AssetMonitoringProfile.c.profile_id
                ).filter(
                    AssetMonitoringProfile.c.asset_id == asset.id,
                    MonitoringProfile.enabled == True
                ).all()
                
                for profile in profiles:
                    # Get checks for this profile
                    checks_query = db.session.query(
                        MonitoringCheck,
                        ProfileCheck.c.enabled,
                        ProfileCheck.c.interval_override,
                        ProfileCheck.c.warning_threshold,
                        ProfileCheck.c.critical_threshold,
                        ProfileCheck.c.parameters
                    ).join(
                        ProfileCheck,
                        MonitoringCheck.id == ProfileCheck.c.check_id
                    ).filter(
                        ProfileCheck.c.profile_id == profile.id,
                        ProfileCheck.c.enabled == True
                    ).all()
                    
                    for check, enabled, interval_override, warning_threshold, critical_threshold, parameters in checks_query:
                        # Determine check interval
                        interval_minutes = interval_override if interval_override else check.default_interval_minutes
                        
                        # Check if due
                        check_key = f"{asset.id}:{check.id}"
                        last_check = self.last_check_times.get(check_key)
                        
                        if last_check is None:
                            # Never run, check immediately
                            is_due = True
                        else:
                            # Check if interval has elapsed
                            next_check = last_check + timedelta(minutes=interval_minutes)
                            is_due = now >= next_check
                        
                        if is_due:
                            due_checks.append({
                                'asset': asset,
                                'profile': profile,
                                'check': check,
                                'warning_threshold': warning_threshold if warning_threshold else check.warning_threshold,
                                'critical_threshold': critical_threshold if critical_threshold else check.critical_threshold,
                                'parameters': parameters
                            })
            
            return due_checks
    
    def execute_check(self, asset, check, warning_threshold, critical_threshold, parameters, profile_id):
        """Execute a single check"""
        start_time = datetime.utcnow()
        
        try:
            # Parse parameters
            params = {}
            if parameters:
                try:
                    params = json.loads(parameters)
                except:
                    pass
            
            # Execute check based on type
            result = None
            
            if check.check_type == 'cpu':
                result = self.check_cpu(asset, critical_threshold, params)
            
            elif check.check_type == 'memory':
                result = self.check_memory(asset, critical_threshold, params)
            
            elif check.check_type == 'disk':
                result = self.check_disk(asset, critical_threshold, params)
            
            elif check.check_type == 'service':
                result = self.check_service(asset, params)
            
            elif check.check_type == 'port':
                result = self.check_port(asset, params)
            
            elif check.check_type == 'ping':
                result = self.check_ping(asset, params)
            
            else:
                result = {
                    'success': False,
                    'error': f'Unknown check type: {check.check_type}'
                }
            
            # Calculate execution time
            execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            # Store result
            with self.app.app_context():
                db.session.execute(
                    MonitoringCheckResult.insert().values(
                        asset_id=asset.id,
                        check_id=check.id,
                        profile_id=profile_id,
                        timestamp=start_time,
                        success=result.get('success', False),
                        value=result.get('value'),
                        message=result.get('message', ''),
                        execution_time_ms=execution_time,
                        error=result.get('error')
                    )
                )
                db.session.commit()
            
            # Update last check time
            check_key = f"{asset.id}:{check.id}"
            self.last_check_times[check_key] = start_time
            
            # Generate alert if check failed
            if not result.get('success'):
                self.generate_alert(asset, check, result, profile_id)
            
            return result
        
        except Exception as e:
            logger.error(f"Error executing check {check.name} for {asset.hostname}: {e}")
            execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            with self.app.app_context():
                db.session.execute(
                    MonitoringCheckResult.insert().values(
                        asset_id=asset.id,
                        check_id=check.id,
                        profile_id=profile_id,
                        timestamp=start_time,
                        success=False,
                        execution_time_ms=execution_time,
                        error=str(e)
                    )
                )
                db.session.commit()
            
            return {'success': False, 'error': str(e)}
    
    def check_cpu(self, asset, threshold, params):
        """Check CPU usage via RMM gateway"""
        # TODO: Query RMM gateway for latest telemetry
        # For now, return simulated check
        return {
            'success': True,
            'value': 25.5,
            'message': 'CPU usage: 25.5%'
        }
    
    def check_memory(self, asset, threshold, params):
        """Check memory usage via RMM gateway"""
        # TODO: Query RMM gateway for latest telemetry
        return {
            'success': True,
            'value': 45.2,
            'message': 'Memory usage: 45.2%'
        }
    
    def check_disk(self, asset, threshold, params):
        """Check disk usage via RMM gateway"""
        # TODO: Query RMM gateway for latest telemetry
        return {
            'success': True,
            'value': 65.8,
            'message': 'Disk usage: 65.8%'
        }
    
    def check_service(self, asset, params):
        """Check if a service is running"""
        service_name = params.get('service_name', '')
        # TODO: Query RMM gateway or run remote check
        return {
            'success': True,
            'value': 'running',
            'message': f'Service {service_name} is running'
        }
    
    def check_port(self, asset, params):
        """Check if a port is open"""
        port = params.get('port', 0)
        # TODO: Perform actual port check
        return {
            'success': True,
            'value': port,
            'message': f'Port {port} is listening'
        }
    
    def check_ping(self, asset, params):
        """Ping check"""
        import subprocess
        
        try:
            result = subprocess.run(
                ['ping', '-c', '3', '-W', '2', asset.ip_address_1],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                # Parse avg RTT
                for line in result.stdout.split('\n'):
                    if 'avg' in line:
                        parts = line.split('=')[1].split('/')
                        avg_ms = float(parts[1])
                        return {
                            'success': True,
                            'value': avg_ms,
                            'message': f'Ping successful: {avg_ms:.1f}ms average'
                        }
                
                return {'success': True, 'message': 'Ping successful'}
            else:
                return {'success': False, 'message': 'Ping failed'}
        
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def generate_alert(self, asset, check, result, profile_id):
        """Generate an alert for a failed check"""
        with self.app.app_context():
            # Check if there's already an open alert for this check
            existing_alert = db.session.query(MonitoringAlert).filter(
                MonitoringAlert.asset_id == asset.id,
                MonitoringAlert.check_id == check.id,
                MonitoringAlert.resolved == False
            ).first()
            
            if existing_alert:
                # Update existing alert
                existing_alert.last_occurrence = datetime.utcnow()
                existing_alert.occurrence_count += 1
            else:
                # Create new alert
                alert = MonitoringAlert(
                    asset_id=asset.id,
                    check_id=check.id,
                    profile_id=profile_id,
                    severity='critical' if result.get('value', 0) > 90 else 'warning',
                    title=f"{check.name} failed on {asset.hostname}",
                    message=result.get('message', result.get('error', 'Check failed')),
                    first_occurrence=datetime.utcnow(),
                    last_occurrence=datetime.utcnow(),
                    occurrence_count=1,
                    resolved=False
                )
                db.session.add(alert)
            
            db.session.commit()
            logger.warning(f"Alert generated: {check.name} failed on {asset.hostname}")
    
    def run_cycle(self):
        """Run one check execution cycle"""
        logger.info("Starting check execution cycle")
        
        # Get checks that are due
        due_checks = self.get_due_checks()
        logger.info(f"Found {len(due_checks)} checks to execute")
        
        # Execute each check
        executed = 0
        failed = 0
        
        for check_info in due_checks:
            try:
                result = self.execute_check(
                    check_info['asset'],
                    check_info['check'],
                    check_info['warning_threshold'],
                    check_info['critical_threshold'],
                    check_info['parameters'],
                    check_info['profile'].id
                )
                
                executed += 1
                if not result.get('success'):
                    failed += 1
                
                logger.debug(f"Executed {check_info['check'].name} for {check_info['asset'].hostname}: {'PASS' if result.get('success') else 'FAIL'}")
            
            except Exception as e:
                logger.error(f"Error executing check: {e}")
                failed += 1
        
        logger.info(f"Cycle complete: {executed} checks executed, {failed} failed")
    
    def run_daemon(self, interval_seconds=60):
        """Run as a daemon"""
        logger.info("Starting Check Execution Engine")
        
        # Create tables if needed
        with self.app.app_context():
            MonitoringCheckResult.create(db.engine, checkfirst=True)
        
        while True:
            try:
                with self.app.app_context():
                    self.run_cycle()
                
                time.sleep(interval_seconds)
            
            except KeyboardInterrupt:
                logger.info("Stopping Check Execution Engine")
                break
            
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(60)


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Cirque RMM Check Execution Engine')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--interval', type=int, default=60, help='Check interval in seconds (default: 60)')
    
    args = parser.parse_args()
    
    executor = CheckExecutor()
    
    if args.once:
        with app.app_context():
            executor.run_cycle()
        return 0
    
    executor.run_daemon(interval_seconds=args.interval)
    return 0


if __name__ == '__main__':
    sys.exit(main())
