"""
utils.py — Shared decorators, helpers, and email functions.

Imports from extensions.py and models.py only.
"""
import os
from datetime import timezone
from functools import wraps

from flask import redirect, url_for, flash, request, jsonify, current_app
from flask_login import current_user
from flask_mail import Message

# ── RMM constants ─────────────────────────────────────────────────────────────
RMM_GATEWAY_INTERNAL = os.environ.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')
RMM_GATEWAY_PUBLIC   = os.environ.get('RMM_GATEWAY_URL', 'wss://rmm.cirquetools.com')
RMM_TRACKER_URL      = os.environ.get('RMM_TRACKER_URL', 'https://tracker.corp.cirque.com')


def _valid_agent_key(key: str) -> bool:
    return bool(key) and key == current_app.config.get('LINUX_AGENT_API_KEY', '')


def _dt_iso(dt) -> 'str | None':
    """Return ISO 8601 string with UTC offset, or None."""
    if dt is None:
        return None
    if hasattr(dt, 'isoformat'):
        if getattr(dt, 'tzinfo', None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec='seconds')
    return str(dt)


def _get_or_create_site_enrollment_token() -> str:
    """Return the site-wide RMM enrollment token, creating it if absent."""
    import secrets as _sec
    from extensions import db
    from models import Setting
    from sqlalchemy import text
    setting = Setting.query.filter_by(key='rmm_site_enrollment_token').first()
    if not setting or not setting.value:
        if not setting:
            setting = Setting(key='rmm_site_enrollment_token')
            db.session.add(setting)
        setting.value = 'site_' + _sec.token_hex(32)
        db.session.commit()
    return setting.value


def _ensure_rmm_script_library_table():
    from extensions import db
    from sqlalchemy import text
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS rmm_script_library (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            file_type TEXT NOT NULL,
            shell TEXT NOT NULL,
            script_content TEXT NOT NULL,
            is_tested BOOLEAN NOT NULL DEFAULT false,
            last_tested_at TIMESTAMPTZ,
            last_tested_agent_id TEXT,
            last_test_result TEXT,
            created_by_user_id INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active BOOLEAN NOT NULL DEFAULT true
        )
    """))
    db.session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_rmm_script_library_active
            ON rmm_script_library (is_active, is_tested, name)
    """))
    db.session.commit()



from extensions import mail


# ══════════════════════════════════════════════════════════════════
#  Access-control decorators
# ══════════════════════════════════════════════════════════════════

def admin_required(f):
    """Decorator to require admin role"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def manager_required(f):
    """Decorator to require manager or admin role"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'manager']:
            flash('Access denied. Manager or Admin privileges required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def eagle_eyes_required(f):
    """Decorator to require admin or eagle_eyes role"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'eagle_eyes']:
            flash('Access denied. Eagle Eyes access required.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def ticket_access_required(f):
    """Decorator to allow admin, manager, eagle_eyes, viewer, or base_user access to tickets"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'manager', 'eagle_eyes', 'viewer', 'base_user']:
            flash('Access denied.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function

def license_required(f):
    """Decorator to check license validity before allowing access"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow license management endpoints
        if request.endpoint in ['get_license', 'save_license', 'verify_license', 'remove_license_key', 'settings']:
            return f(*args, **kwargs)
        
        # Import license_service here to avoid circular imports
        from license_service import license_service
        
        # Check license validity
        status = license_service.is_license_valid()
        
        if not status['valid']:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({
                    'error': 'License expired or invalid',
                    'message': status['message'],
                    'licenseExpired': True
                }), 403
            else:
                flash(f'❌ LICENSE EXPIRED: {status["message"]} - Please update your license to continue using the system.', 'danger')
                return redirect(url_for('settings.settings') + '#license-tab')
        
        return f(*args, **kwargs)
    
    return decorated_function



# ══════════════════════════════════════════════════════════════════
#  Email helpers
# ══════════════════════════════════════════════════════════════════

# ==================== EMAIL FUNCTIONS ====================

def send_email(subject, recipients, text_body, html_body=None):
    """Send email via configured SMTP server"""
    try:
        msg = Message(subject, recipients=recipients)
        msg.body = text_body
        if html_body:
            msg.html = html_body
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Failed to send email: {str(e)}")
        return False

def send_admin_notification(subject, message):
    """Send notification to all admin users"""
    try:
        admins = User.query.filter_by(role='admin').all()
        admin_emails = [admin.email for admin in admins if admin.email]
        
        if not admin_emails:
            current_app.logger.warning("No admin emails configured")
            return False
        
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #0d6efd;">Asset Tracker Notification</h2>
                    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        {message}
                    </div>
                    <p style="color: #6c757d; font-size: 12px; margin-top: 30px;">
                        This is an automated message from the Asset Tracker system.
                    </p>
                </div>
            </body>
        </html>
        """
        
        return send_email(subject, admin_emails, message, html_body)
    except Exception as e:
        current_app.logger.error(f"Failed to send admin notification: {str(e)}")
        return False

def send_asset_assignment_email(asset, employee, assigned_by):
    """Send email when asset is assigned to employee (if enabled)"""
    if not current_app.config['SEND_EMPLOYEE_EMAILS']:
        return False
    
    if not employee or not employee.email:
        return False
    
    try:
        subject = f"Asset Assigned: {asset.asset_tag}"
        
        text_body = f"""
Hello {employee.name},

An asset has been assigned to you:

Asset Tag: {asset.asset_tag}
Name: {asset.name}
Category: {asset.category}
Serial Number: {asset.serial_number or 'N/A'}
Status: {asset.status}

Assigned by: {assigned_by}

Please take care of this asset and report any issues immediately.

Thank you,
IT Asset Management Team
        """
        
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #0d6efd;">Asset Assignment Notice</h2>
                    <p>Hello {employee.name},</p>
                    <p>An asset has been assigned to you:</p>
                    
                    <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 8px; font-weight: bold; width: 40%;">Asset Tag:</td>
                                <td style="padding: 8px;">{asset.asset_tag}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; font-weight: bold;">Name:</td>
                                <td style="padding: 8px;">{asset.name}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; font-weight: bold;">Category:</td>
                                <td style="padding: 8px;">{asset.category}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; font-weight: bold;">Serial Number:</td>
                                <td style="padding: 8px;">{asset.serial_number or 'N/A'}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px; font-weight: bold;">Status:</td>
                                <td style="padding: 8px;">{asset.status}</td>
                            </tr>
                        </table>
                    </div>
                    
                    <p style="margin-top: 20px;"><strong>Assigned by:</strong> {assigned_by}</p>
                    <p>Please take care of this asset and report any issues immediately.</p>
                    
                    <p style="color: #6c757d; font-size: 12px; margin-top: 30px;">
                        This is an automated message from the Asset Tracker system.
                    </p>
                </div>
            </body>
        </html>
        """
        
        return send_email(subject, [employee.email], text_body, html_body)
    except Exception as e:
        current_app.logger.error(f"Failed to send assignment email: {str(e)}")
        return False

def send_warranty_expiry_alert(asset):
    """Send alert to admins for expiring warranty"""
    try:
        days_until_expiry = (asset.warranty_expiry - datetime.utcnow().date()).days
        
        subject = f"Warranty Expiring Soon: {asset.asset_tag}"
        message = f"""
        <p><strong>Asset:</strong> {asset.asset_tag} - {asset.name}</p>
        <p><strong>Category:</strong> {asset.category}</p>
        <p><strong>Warranty Expiry:</strong> {asset.warranty_expiry.strftime('%Y-%m-%d')}</p>
        <p><strong>Days Remaining:</strong> {days_until_expiry} days</p>
        <p style="color: #dc3545;">Please review and renew if necessary.</p>
        """
        
        return send_admin_notification(subject, message)
    except Exception as e:
        current_app.logger.error(f"Failed to send warranty alert: {str(e)}")
        return False

def send_lifecycle_alert(asset):
    """Send alert to admins for assets needing replacement"""
    try:
        status = asset.get_lifecycle_status()
        age = asset.get_age_years()
        
        subject = f"Asset Lifecycle Alert: {asset.asset_tag} - {status}"
        message = f"""
        <p><strong>Asset:</strong> {asset.asset_tag} - {asset.name}</p>
        <p><strong>Category:</strong> {asset.category}</p>
        <p><strong>Lifecycle Status:</strong> <span style="color: #dc3545;">{status}</span></p>
        <p><strong>Current Age:</strong> {age:.1f} years</p>
        <p><strong>Expected Life:</strong> {asset.expected_life_years} years</p>
        <p><strong>Replacement Date:</strong> {asset.replacement_date.strftime('%Y-%m-%d') if asset.replacement_date else 'Not set'}</p>
        <p style="color: #dc3545;">This asset may need replacement soon.</p>
        """
        
        return send_admin_notification(subject, message)
    except Exception as e:
        current_app.logger.error(f"Failed to send lifecycle alert: {str(e)}")
        return False

