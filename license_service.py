"""
License Service for Asset Tracker
Handles license verification with remote license server
"""

import requests
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os
import socket

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
LICENSE_SERVER_URL = os.environ.get('LICENSE_SERVER_URL', 'https://license.corp.cirque.com/api')
LICENSE_API_KEY = os.environ.get('LICENSE_API_KEY', 'sk_86026fc2d5824b9f0514e115327ccb3078fd96883c62745454ea6d751c1d98b4')
CHECK_INTERVAL_HOURS = 24
GRACE_PERIOD_DAYS = 1


def get_device_id():
    """
    Generate a unique device ID for this installation
    Returns asset-tracker-01 format
    """
    return "asset-tracker-01"


class LicenseService:
    """Service for managing license verification"""
    
    def __init__(self, app=None, db=None):
        self.app = app
        self.db = db
        self.scheduler = None
        
    def init_app(self, app, db):
        """Initialize the license service with Flask app and database"""
        self.app = app
        self.db = db
        
    def verify_with_server(self, license_key, api_key=None, device_id=None):
        """
        Verify license with remote server
        
        Args:
            license_key: License key to verify
            api_key: Optional API key override (from database)
            device_id: Optional device ID override (from database)
            
        Returns:
            dict: Verification result with 'valid', 'status', and 'license' fields
        """
        try:
            # Use provided API key or fall back to environment variable
            api_key_to_use = api_key or LICENSE_API_KEY
            
            if not api_key_to_use:
                raise Exception("No API key configured")
            
            # Use provided device ID or generate from hostname
            device_id_to_use = device_id or get_device_id()
            
            url = f"{LICENSE_SERVER_URL}/verify"
            headers = {
                'Content-Type': 'application/json',
                'X-API-Key': api_key_to_use
            }
            data = {
                'licenseKey': license_key,
                'deviceId': device_id_to_use
            }
            
            logger.info(f"Verifying license with device ID: {device_id_to_use}")
            
            # Disable SSL verification for internal self-signed certificates
            response = requests.post(url, json=data, headers=headers, verify=False, timeout=10)
            
            logger.info(f"License server response: {response.status_code}")
            
            # Parse response - server returns JSON for both success and error cases
            try:
                result = response.json()
            except ValueError:
                raise Exception(f"Invalid JSON response from server: {response.text[:200]}")
            
            # Server returns 200 for valid licenses, may return 500 for invalid
            # But still returns JSON with valid/error fields
            if 'valid' in result:
                return result
            
            # If no 'valid' field, something is wrong with the response
            raise Exception(f"Unexpected response format: {result}")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"License server connection error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"License server error: {str(e)}")
            raise
    
    def is_license_valid(self):
        """
        Check if license is valid (including grace period)
        
        Returns:
            dict: {'valid': bool, 'message': str, 'warning': bool}
        """
        from app import LicenseInfo
        
        with self.app.app_context():
            license_info = LicenseInfo.query.order_by(LicenseInfo.id.desc()).first()
            
            if not license_info:
                return {
                    'valid': False,
                    'message': 'No license configured',
                    'warning': False
                }
            
            # Check if expired
            if license_info.expiry_date:
                if license_info.expiry_date < datetime.utcnow():
                    return {
                        'valid': False,
                        'message': 'License expired',
                        'warning': False
                    }
            
            # Check grace period if server was unreachable
            if license_info.grace_period_ends:
                if license_info.grace_period_ends < datetime.utcnow():
                    return {
                        'valid': False,
                        'message': 'License grace period expired, server unreachable',
                        'warning': False
                    }
                
                return {
                    'valid': True,
                    'message': 'License server unreachable, using grace period',
                    'warning': True
                }
            
            # Check status
            if license_info.status in ['invalid', 'expired']:
                return {
                    'valid': False,
                    'message': f'License {license_info.status}',
                    'warning': False
                }
            
            return {
                'valid': True,
                'message': 'License active',
                'warning': False
            }
    
    def perform_check(self):
        """Perform license check and update database"""
        from app import LicenseInfo
        
        with self.app.app_context():
            license_info = LicenseInfo.query.order_by(LicenseInfo.id.desc()).first()
            
            if not license_info:
                logger.warning('⚠️  No license configured, skipping check')
                return
            
            logger.info('🔄 Running periodic license check...')
            
            try:
                verification = self.verify_with_server(
                    license_info.license_key, 
                    license_info.api_key,
                    license_info.device_id
                )
                
                if verification.get('valid') and verification.get('license'):
                    lic_data = verification['license']
                    
                    # Parse expiry date
                    expiry_date = datetime.fromisoformat(lic_data['expiresAt'].replace('Z', '+00:00'))
                    
                    license_info.status = verification.get('status', 'active')
                    license_info.expiry_date = expiry_date
                    license_info.plan_name = lic_data.get('planName')
                    license_info.max_devices = lic_data.get('maxDevices')
                    license_info.last_checked = datetime.utcnow()
                    license_info.last_check_status = 'success'
                    license_info.grace_period_ends = None
                    license_info.updated_at = datetime.utcnow()
                    
                    self.db.session.commit()
                    
                    logger.info('✅ License check successful')
                    logger.info(f'   Days remaining: {lic_data.get("daysRemaining", "N/A")}')
                    
                else:
                    logger.error('❌ License invalid')
                    license_info.status = 'invalid'
                    license_info.last_checked = datetime.utcnow()
                    license_info.last_check_status = 'invalid'
                    license_info.grace_period_ends = None  # Clear grace period for invalid licenses
                    license_info.updated_at = datetime.utcnow()
                    self.db.session.commit()
                    
            except Exception as e:
                logger.error(f'❌ License server unreachable: {str(e)}')
                
                # Set grace period
                grace_period_end = datetime.utcnow() + timedelta(days=GRACE_PERIOD_DAYS)
                
                license_info.last_checked = datetime.utcnow()
                license_info.last_check_status = 'server_unreachable'
                license_info.grace_period_ends = grace_period_end
                license_info.updated_at = datetime.utcnow()
                self.db.session.commit()
                
                logger.warning(f'⚠️  Grace period activated: {GRACE_PERIOD_DAYS} day(s)')
    
    def verify_on_startup(self):
        """Verify license on application startup"""
        logger.info('🔐 Verifying license on startup...')
        
        status = self.is_license_valid()
        
        if status['valid']:
            logger.info('✅ License verified successfully')
            if status['message']:
                logger.info(f'   {status["message"]}')
            if status.get('warning'):
                logger.warning(f'   ⚠️  {status["message"]}')
        else:
            logger.error(f'❌ License validation failed: {status["message"]}')
            logger.error('   Please verify your license in Settings > License')
        
        return True  # Allow startup even if license invalid (show warning in UI)
    
    def start_periodic_check(self):
        """Start periodic license checking (every 24 hours)"""
        if self.scheduler is not None:
            logger.warning('Scheduler already running')
            return
        
        logger.info(f'🔐 Starting periodic license checks (every {CHECK_INTERVAL_HOURS} hours)')
        
        self.scheduler = BackgroundScheduler()
        
        # Schedule periodic checks
        self.scheduler.add_job(
            func=self.perform_check,
            trigger='interval',
            hours=CHECK_INTERVAL_HOURS,
            id='license_check',
            name='Periodic license verification',
            replace_existing=True
        )
        
        self.scheduler.start()
        
        # Perform initial check
        try:
            self.perform_check()
        except Exception as e:
            logger.error(f'Initial license check failed: {str(e)}')
    
    def shutdown(self):
        """Shutdown the scheduler"""
        if self.scheduler:
            self.scheduler.shutdown()


# Global instance
license_service = LicenseService()
