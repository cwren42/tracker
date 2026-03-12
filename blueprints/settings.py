import base64
import json
import os
import re
import subprocess
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
try:
    import ai_engine as _ai_engine
except ImportError:
    _ai_engine = None

from flask import (Blueprint, abort, current_app, flash, g, jsonify,
                   redirect, render_template, request, send_file, session,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_, text

from extensions import db, limiter
from models import (
    AuditTrail, Asset, AssetHistory, Control, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
)
from soc2_models import SOC2Control, EvidenceSnapshot
import logging
from license_service import license_service
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, _get_or_create_site_enrollment_token,
)
logger = logging.getLogger(__name__)


bp = Blueprint('settings', __name__)


# ==================== SETTINGS ====================


_SCRIPT_FILE_TYPES = {
    '.ps1': 'powershell',
    '.bat': 'cmd',
    '.sh': 'bash',
}


# ── Eagle Eyes Exclusions ──────────────────────────────────────────────────────


# ==================== LICENSE API ENDPOINTS ====================



@bp.route('/settings', defaults={'section': None}, methods=['GET', 'POST'])
@bp.route('/settings/<section>', methods=['GET', 'POST'])
@login_required
@admin_required
def settings(section):
    """Admin settings page for email configuration and testing"""
    allowed_sections = {
        'theme', 'license', 'email',
        'directory', 'rmm', 'scripts', 'eagleeye',
        'unifi', 'proxmox', 'ai', 'cloudflare'
    }
    section_endpoints = {
        'theme': 'settings_theme',
        'license': 'settings_license',
        'email': 'settings_email',
        'directory': 'settings_directory',
        'rmm': 'settings_rmm',
        'scripts': 'settings_scripts',
        'eagleeye': 'settings_eagleeye',
        'unifi': 'settings_unifi',
        'proxmox': 'settings_proxmox',
        'ai': 'settings_ai',
        'cloudflare': 'settings_cloudflare',
    }
    if section and section not in allowed_sections:
        flash('Unknown settings page.', 'warning')
        return redirect(url_for('settings.settings'))

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'test_email':
            # Test email to admin
            test_email = request.form.get('test_email')
            if test_email:
                try:
                    subject = "Asset Tracker - Test Email"
                    message = f"""
                    <p>This is a test email from the Asset Tracker system.</p>
                    <p><strong>Date/Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                    <p><strong>Sent by:</strong> {current_user.username}</p>
                    <p>If you received this email, your SMTP configuration is working correctly!</p>
                    """
                    
                    if send_email(subject, [test_email], "Test email from Asset Tracker", message):
                        flash('Test email sent successfully! Check your inbox.', 'success')
                    else:
                        flash('Failed to send test email. Check server logs for details.', 'danger')
                except Exception as e:
                    flash(f'Error sending test email: {str(e)}', 'danger')
            else:
                flash('Please enter an email address for testing.', 'warning')
        
        elif action == 'toggle_employee_emails':
            # Toggle employee email notifications
            current_app.config['SEND_EMPLOYEE_EMAILS'] = not current_app.config['SEND_EMPLOYEE_EMAILS']
            status = "enabled" if current_app.config['SEND_EMPLOYEE_EMAILS'] else "disabled"
            flash(f'Employee email notifications {status}.', 'success')
        
        elif action == 'update_sender':
            # Update default sender email
            new_sender = request.form.get('sender_email')
            if new_sender:
                current_app.config['MAIL_DEFAULT_SENDER'] = new_sender
                flash('Default sender email updated.', 'success')
        
        elif action == 'update_smtp':
            # Update SMTP settings
            smtp_server = request.form.get('smtp_server', '').strip()
            smtp_port = request.form.get('smtp_port', '').strip()
            smtp_username = request.form.get('smtp_username', '').strip()
            smtp_password = request.form.get('smtp_password', '').strip()
            use_tls = request.form.get('use_tls') == 'on'
            use_ssl = request.form.get('use_ssl') == 'on'
            sender_email = request.form.get('sender_email', '').strip()
            
            try:
                # Update settings in database
                settings_to_update = {
                    'smtp_server': smtp_server,
                    'smtp_port': smtp_port,
                    'smtp_username': smtp_username,
                    'smtp_use_tls': 'true' if use_tls else 'false',
                    'smtp_use_ssl': 'true' if use_ssl else 'false',
                    'smtp_sender': sender_email
                }
                
                # Only save password if provided
                if smtp_password:
                    settings_to_update['smtp_password'] = smtp_password
                
                for key, value in settings_to_update.items():
                    setting = Setting.query.filter_by(key=key).first()
                    if not setting:
                        setting = Setting(key=key)
                    setting.value = value
                    setting.updated_by = current_user.username
                    setting.updated_at = datetime.utcnow()
                    db.session.add(setting)
                
                db.session.commit()
                
                # Update app config
                current_app.config['MAIL_SERVER'] = smtp_server
                current_app.config['MAIL_PORT'] = int(smtp_port) if smtp_port else 25
                current_app.config['MAIL_USERNAME'] = smtp_username if smtp_username else None
                if smtp_password:
                    current_app.config['MAIL_PASSWORD'] = smtp_password
                current_app.config['MAIL_USE_TLS'] = use_tls
                current_app.config['MAIL_USE_SSL'] = use_ssl
                current_app.config['MAIL_DEFAULT_SENDER'] = sender_email
                
                flash('SMTP settings updated successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error updating SMTP settings: {str(e)}', 'danger')

        elif action == 'update_unifi':
            # Save UniFi credentials
            def _save(key, val):
                s = Setting.query.filter_by(key=key).first()
                if not s:
                    s = Setting(key=key)
                    db.session.add(s)
                s.value = val
                s.updated_by = current_user.username
                s.updated_at = datetime.utcnow()
            raw_host = request.form.get('unifi_host', '').strip().rstrip('/')
            # Normalize scheme
            if raw_host and not raw_host.startswith(('http://', 'https://')):
                raw_host = 'https://' + raw_host
            _save('unifi_host', raw_host)
            _save('unifi_username', request.form.get('unifi_username', '').strip())
            pw = request.form.get('unifi_password', '')
            if pw:  # only overwrite if a new password was submitted
                _save('unifi_password', pw)
            _save('unifi_site', request.form.get('unifi_site', 'default').strip() or 'default')
            db.session.commit()
            flash('UniFi settings saved successfully!', 'success')

        elif action == 'update_theme':
            # Update user's theme preference
            theme = request.form.get('theme', 'dark')
            current_user.theme = theme
            db.session.commit()
            flash('Theme updated successfully!', 'success')

        elif action == 'update_ad':
            ad_enabled = request.form.get('ad_enabled') == 'on'
            ad_server = request.form.get('ad_server', '').strip()
            ad_port = request.form.get('ad_port', '').strip()
            ad_use_ssl = request.form.get('ad_use_ssl') == 'on'
            ad_base_dn = request.form.get('ad_base_dn', '').strip()
            ad_bind_username = request.form.get('ad_bind_username', '').strip()
            ad_bind_password = request.form.get('ad_bind_password', '')
            ad_user_ou_dn = request.form.get('ad_user_ou_dn', '').strip()

            try:
                settings_to_update = {
                    'ad_enabled': 'true' if ad_enabled else 'false',
                    'ad_server': ad_server,
                    'ad_port': ad_port,
                    'ad_use_ssl': 'true' if ad_use_ssl else 'false',
                    'ad_base_dn': ad_base_dn,
                    'ad_bind_username': ad_bind_username,
                    'ad_user_ou_dn': ad_user_ou_dn,
                }
                for key, value in settings_to_update.items():
                    setting = Setting.query.filter_by(key=key).first()
                    if not setting:
                        setting = Setting(key=key)
                    setting.value = value
                    setting.updated_by = current_user.username
                    setting.updated_at = datetime.utcnow()
                    db.session.add(setting)

                # Only update password if provided
                if ad_bind_password:
                    setting = Setting.query.filter_by(key='ad_bind_password').first()
                    if not setting:
                        setting = Setting(key='ad_bind_password')
                    setting.value = ad_bind_password
                    setting.updated_by = current_user.username
                    setting.updated_at = datetime.utcnow()
                    db.session.add(setting)

                db.session.commit()
                flash('AD/LDAP settings updated successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error updating AD/LDAP settings: {str(e)}', 'danger')

        elif action == 'update_cloudflare':
            cf_enabled = request.form.get('cf_enabled') == 'on'
            cf_account_id = request.form.get('cf_account_id', '').strip()
            cf_tunnel_id = request.form.get('cf_tunnel_id', '').strip()
            cf_tunnel_name = request.form.get('cf_tunnel_name', '').strip()
            cf_hostname = request.form.get('cf_hostname', '').strip()

            try:
                settings_to_update = {
                    'cf_enabled': 'true' if cf_enabled else 'false',
                    'cf_account_id': cf_account_id,
                    'cf_tunnel_id': cf_tunnel_id,
                    'cf_tunnel_name': cf_tunnel_name,
                    'cf_hostname': cf_hostname,
                }
                for key, value in settings_to_update.items():
                    setting = Setting.query.filter_by(key=key).first()
                    if not setting:
                        setting = Setting(key=key)
                    setting.value = value
                    setting.updated_by = current_user.username
                    setting.updated_at = datetime.utcnow()
                    db.session.add(setting)

                db.session.commit()
                flash('Cloudflare settings updated successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error updating Cloudflare settings: {str(e)}', 'danger')
        
        if section:
            return redirect(url_for(section_endpoints[section]))
        return redirect(url_for('dashboard.index'))


@bp.route('/settings/theme', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_theme():
    if request.method == 'POST':
        return settings('theme')
    return render_template('settings_theme.html')


@bp.route('/settings/license', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_license():
    if request.method == 'POST':
        return settings('license')
    
    # GET request — load license information
    def get_setting_value(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value is not None else default
    
    license_config = {
        'license_key': get_setting_value('license_key', ''),
        'license_api_key': get_setting_value('license_api_key', ''),
        'license_device_id': get_setting_value('license_device_id', ''),
        'license_company': get_setting_value('license_company', ''),
    }
    
    return render_template('settings_license.html', license_config=license_config)


@bp.route('/settings/email', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_email():
    if request.method == 'POST':
        return settings('email')
    
    # GET request — render the Email settings page
    def get_setting_value(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value is not None else default

    config = {
        'mail_server': get_setting_value('smtp_server', current_app.config['MAIL_SERVER']),
        'mail_port': get_setting_value('smtp_port', str(current_app.config['MAIL_PORT'])),
        'mail_username': get_setting_value('smtp_username', current_app.config.get('MAIL_USERNAME', '')),
        'mail_password': get_setting_value('smtp_password', current_app.config.get('MAIL_PASSWORD', '')),
        'mail_use_tls': get_setting_value('smtp_use_tls', 'false') == 'true',
        'mail_use_ssl': get_setting_value('smtp_use_ssl', 'false') == 'true',
        'mail_sender': get_setting_value('smtp_sender', current_app.config['MAIL_DEFAULT_SENDER']),
    }
    
    return render_template('settings_email.html', config=config)


@bp.route('/settings/directory', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_directory():
    if request.method == 'POST':
        return settings('directory')
    
    # GET request — render the Directory settings page
    def get_setting_value(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value is not None else default

    ad_config = {
        'ad_enabled': get_setting_value('ad_enabled', 'false') == 'true',
        'ad_server': get_setting_value('ad_server', ''),
        'ad_port': get_setting_value('ad_port', '636'),
        'ad_use_ssl': get_setting_value('ad_use_ssl', 'true') == 'true',
        'ad_base_dn': get_setting_value('ad_base_dn', ''),
        'ad_bind_username': get_setting_value('ad_bind_username', ''),
        'ad_bind_password': get_setting_value('ad_bind_password', ''),
        'ad_user_ou_dn': get_setting_value('ad_user_ou_dn', ''),
    }
    
    return render_template('settings_directory.html', ad_config=ad_config)


@bp.route('/settings/rmm', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_rmm():
    if request.method == 'POST':
        return settings('rmm')
    
    rmm_site_token = _get_or_create_site_enrollment_token()
    return render_template('settings_rmm.html', rmm_site_token=rmm_site_token)


@bp.route('/settings/scripts', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_scripts():
    if request.method == 'POST':
        return settings('scripts')
    return render_template('settings_scripts.html')


@bp.route('/settings/eagleeye', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_eagleeye():
    if request.method == 'POST':
        return settings('eagleeye')
    
    # GET request — render the Eagle Eyes settings page
    # Load all available agents and current exclusions
    try:
        exclusions = db.session.execute(text(
            "SELECT agent_id, hostname FROM eagle_eyes_exclusions ORDER BY COALESCE(hostname, agent_id)"
        )).mappings().fetchall()
        all_agents = db.session.execute(text(
            """SELECT ec.agent_id, COALESCE(t.hostname, ec.agent_id) AS hostname
               FROM rmm_eagle_config ec
               LEFT JOIN rmm_telemetry t ON t.agent_id = ec.agent_id
               WHERE ec.enabled = true
               ORDER BY COALESCE(t.hostname, ec.agent_id)"""
        )).mappings().fetchall()
        exclusions_list = [dict(r) for r in exclusions]
        all_agents_list = [dict(r) for r in all_agents]
        excluded_ids = {r['agent_id'] for r in exclusions_list}
        active_agents = [r for r in all_agents_list if r['agent_id'] not in excluded_ids]
        
        context = {
            'ee_all_agents': all_agents_list,
            'ee_active_agents': active_agents,
            'ee_excluded_agents': exclusions_list,
        }
    except Exception as e:
        context = {'ee_all_agents': [], 'ee_active_agents': [], 'ee_excluded_agents': [], 'ee_error': str(e)}
    
    return render_template('settings_eagleeye.html', **context)


@bp.route('/settings/unifi', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_unifi():
    if request.method == 'POST':
        return settings('unifi')  # Handle form submission via settings()
    
    # GET request — render the UniFi settings page
    def get_setting_value(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value is not None else default

    unifi_config = {
        'host': get_setting_value('unifi_host', ''),
        'username': get_setting_value('unifi_username', ''),
        'password_set': bool(get_setting_value('unifi_password', '')),
        'site': get_setting_value('unifi_site', 'default'),
        'last_sync_status': get_setting_value('unifi_last_sync_status', ''),
        'last_sync_message': get_setting_value('unifi_last_sync_message', ''),
        'last_sync_time': get_setting_value('unifi_last_sync_time', ''),
    }
    
    return render_template('settings_unifi.html', unifi_config=unifi_config)


@bp.route('/settings/proxmox', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_proxmox():
    if request.method == 'POST':
        return settings('proxmox')  # Handle form submission via settings()
    
    # GET request — render the Proxmox settings page
    def get_setting_value(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value is not None else default

    proxmox_settings = {
        'cluster_host': get_setting_value('proxmox_cluster_host', ''),
        'cluster_token_id': get_setting_value('proxmox_cluster_token_id', ''),
        'cluster_token_set': bool(get_setting_value('proxmox_cluster_token_secret', '')),
        'cluster_verify_ssl': get_setting_value('proxmox_cluster_verify_ssl', '0'),
        'backup_host': get_setting_value('proxmox_backup_host', ''),
        'backup_token_id': get_setting_value('proxmox_backup_token_id', ''),
        'backup_token_set': bool(get_setting_value('proxmox_backup_token_secret', '')),
        'backup_verify_ssl': get_setting_value('proxmox_backup_verify_ssl', '0'),
        'stale_hours': get_setting_value('proxmox_stale_hours', '26'),
    }
    
    return render_template('settings_proxmox.html', proxmox_settings=proxmox_settings)


@bp.route('/settings/ai', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_ai():
    if request.method == 'POST':
        return settings('ai')  # Handle form submission via settings()
    
    # GET request — render the AI settings page
    def get_setting_value(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value is not None else default

    openai_key = get_setting_value('openai_api_key', '')
    ai_config = {
        'openai_api_key_masked': (openai_key[:8] + '…' + openai_key[-4:]) if openai_key else '',
        'openai_model': get_setting_value('openai_model', 'gpt-4o'),
        'ai_ticket_enabled': get_setting_value('ai_ticket_enabled', 'false') == 'true',
        'ai_ticket_auto_mode': get_setting_value('ai_ticket_auto_mode', 'false') == 'true',
        'ai_security_monitor_enabled': get_setting_value('ai_security_monitor_enabled', 'false') == 'true',
    }
    
    return render_template('settings_ai.html', ai_config=ai_config)


def _get_cloudflare_service_status():
    """Return cloudflared tunnel service status for UI/API display."""
    service_name = 'cloudflared'
    result = {
        'service_name': service_name,
        'installed': False,
        'active': False,
        'enabled_on_boot': False,
        'status': 'unknown',
        'message': '',
    }

    def _run_cmd(cmd, timeout=5):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    try:
        # Use absolute path to avoid PATH issues under systemd services.
        cmd = ['/bin/systemctl', 'is-active', service_name]
        is_active = _run_cmd(cmd)
        if is_active.returncode != 0 and not (is_active.stdout or '').strip():
            is_active = _run_cmd(['/usr/bin/systemctl', 'is-active', service_name])
        active_text = (is_active.stdout or '').strip()
        result['active'] = active_text == 'active'
        result['installed'] = active_text in {'active', 'inactive', 'failed', 'activating', 'deactivating'}
        result['status'] = active_text or 'unknown'
    except FileNotFoundError:
        result['message'] = 'systemctl is not available on this host.'
        return result
    except Exception as e:
        result['message'] = str(e)
        return result

    try:
        cmd = ['/bin/systemctl', 'is-enabled', service_name]
        is_enabled = _run_cmd(cmd)
        if is_enabled.returncode != 0 and not (is_enabled.stdout or '').strip():
            is_enabled = _run_cmd(['/usr/bin/systemctl', 'is-enabled', service_name])
        enabled_text = (is_enabled.stdout or '').strip()
        result['enabled_on_boot'] = enabled_text == 'enabled'
        if enabled_text and enabled_text not in {'enabled', 'disabled'}:
            result['message'] = enabled_text
    except Exception:
        # Non-fatal for page rendering
        pass

    return result


def _mask_secret(value, prefix=8, suffix=4):
    if not value:
        return ''
    if len(value) <= prefix + suffix:
        return '*' * len(value)
    return f"{value[:prefix]}...{value[-suffix:]}"


def _decode_cloudflare_tunnel_token(token):
    """Decode cloudflared JWT tunnel token payload for account/tunnel IDs."""
    result = {}
    try:
        raw = (token or '').strip()
        parts = raw.split('.')
        # cloudflared may provide either JWT-like token (3 parts)
        # or base64url-encoded JSON blob (single part).
        payload = parts[1] if len(parts) == 3 else raw
        payload += '=' * (-len(payload) % 4)
        payload_obj = json.loads(base64.urlsafe_b64decode(payload.encode('utf-8')).decode('utf-8'))
        result['cf_account_id'] = payload_obj.get('a', '')
        result['cf_tunnel_id'] = payload_obj.get('t', '')
    except Exception:
        # Non-fatal; UI will still show service status.
        return {}
    return result


def _read_cloudflare_server_config():
    """Read Cloudflare tunnel config directly from server files/service."""
    result = {
        'source': 'none',
        'cf_account_id': '',
        'cf_tunnel_id': '',
        'cf_tunnel_name': '',
        'cf_hostname': '',
        'cf_token_masked': '',
    }

    config_path = '/etc/cloudflared/config.yml'
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                raw = f.read()
            tunnel_match = re.search(r'^\s*tunnel:\s*([^\n#]+)', raw, flags=re.MULTILINE)
            host_match = re.search(r'^\s*-\s*hostname:\s*([^\n#]+)', raw, flags=re.MULTILINE)
            if tunnel_match:
                result['cf_tunnel_id'] = tunnel_match.group(1).strip().strip('"\'')
            if host_match:
                result['cf_hostname'] = host_match.group(1).strip().strip('"\'')
            result['source'] = f'file:{config_path}'
            return result
        except Exception:
            # Fall back to systemd service token inspection.
            pass

    try:
        show = subprocess.run(
            ['systemctl', 'show', '-p', 'ExecStart', 'cloudflared'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        exec_line = (show.stdout or '').strip()
        token_match = re.search(r'--token\s+([^\s;]+)', exec_line)
        if token_match:
            token = token_match.group(1).strip()
            result['cf_token_masked'] = _mask_secret(token, prefix=10, suffix=6)
            decoded = _decode_cloudflare_tunnel_token(token)
            result.update(decoded)
            result['source'] = 'systemd:cloudflared-token'
    except Exception:
        pass

    return result


@bp.route('/settings/cloudflare', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_cloudflare():
    if request.method == 'POST':
        return settings('cloudflare')

    def get_setting_value(key, default=''):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s and s.value is not None else default

    cf_config = {
        'cf_enabled': get_setting_value('cf_enabled', 'false') == 'true',
        'cf_account_id': get_setting_value('cf_account_id', ''),
        'cf_tunnel_id': get_setting_value('cf_tunnel_id', ''),
        'cf_tunnel_name': get_setting_value('cf_tunnel_name', ''),
        'cf_hostname': get_setting_value('cf_hostname', ''),
    }

    server_cf = _read_cloudflare_server_config()
    # Auto-populate empty DB-backed fields from actual server config.
    for key in ('cf_account_id', 'cf_tunnel_id', 'cf_tunnel_name', 'cf_hostname'):
        if not cf_config.get(key) and server_cf.get(key):
            cf_config[key] = server_cf.get(key)

    return render_template(
        'settings_cloudflare.html',
        cf_config=cf_config,
        cf_status=_get_cloudflare_service_status(),
        cf_server=server_cf,
    )


@bp.route('/api/cloudflare/status', methods=['GET'])
@login_required
@admin_required
def api_cloudflare_status():
    return jsonify({'ok': True, 'status': _get_cloudflare_service_status()})


@bp.route('/api/cloudflare/tunnel/toggle', methods=['POST'])
@login_required
@admin_required
def api_cloudflare_tunnel_toggle():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled'))
    # Use full path to sudo — systemd sets a minimal PATH without /usr/bin.
    # For stop: use 'kill' which sends SIGTERM immediately and returns — no waiting.
    # For start: use '--no-block' so systemctl returns before the service fully starts.
    sudo = '/usr/bin/sudo' if os.path.exists('/usr/bin/sudo') else '/bin/sudo'
    systemctl = '/bin/systemctl' if os.path.exists('/bin/systemctl') else '/usr/bin/systemctl'
    if enabled:
        cmd = [sudo, '-n', systemctl, 'start', '--no-block', 'cloudflared']
    else:
        cmd = [sudo, '-n', systemctl, 'kill', 'cloudflared']

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or 'Failed to toggle cloudflared service.').strip()
            return jsonify({'ok': False, 'error': err, 'status': _get_cloudflare_service_status()}), 500
    except FileNotFoundError:
        return jsonify({'ok': False, 'error': 'systemctl/sudo not available on this host.'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

    # Poll until the service reaches the expected state (max 6 seconds).
    import time
    desired = 'active' if enabled else 'inactive'
    for _ in range(12):
        time.sleep(0.5)
        status = _get_cloudflare_service_status()
        if enabled and status['active']:
            break
        if not enabled and not status['active']:
            break
    else:
        status = _get_cloudflare_service_status()

    return jsonify({'ok': True, 'status': status})


def _normalize_script_file_type(raw: str) -> str:
    ft = (raw or '').strip().lower()
    if not ft:
        raise ValueError('file_type is required')
    if not ft.startswith('.'):
        ft = f'.{ft}'
    if ft not in _SCRIPT_FILE_TYPES:
        raise ValueError('file_type must be one of: .ps1, .bat, .sh')
    return ft


def _ensure_rmm_script_library_table():
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


def _send_script_to_agent(agent_id: str, shell: str, code: str, timeout_s: int, reason: str):
    import json as _json, urllib.request as _req, urllib.error as _err
    agent_row = db.session.execute(
        text("SELECT asset_id FROM rmm_agent WHERE agent_id = :aid AND enabled = true LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    asset_id = agent_row[0] if agent_row else None

    res = db.session.execute(
        text("""INSERT INTO rmm_session (asset_id, started_by_user_id, reason, started_at)
                VALUES (:aid, :uid, :reason, NOW()) RETURNING id"""),
        {'aid': asset_id, 'uid': current_user.id if hasattr(current_user, 'id') else None, 'reason': reason}
    )
    db.session.commit()
    session_id = int(res.scalar() or 0)

    payload = _json.dumps({
        'type': 'run_script',
        'shell': shell,
        'code': code,
        'timeout': int(timeout_s),
        'session_id': session_id,
    }).encode()
    try:
        req = _req.Request(
            f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _req.urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
        if not result.get('ok'):
            return False, session_id, result.get('error', 'Gateway error')
    except _err.HTTPError as e:
        try:
            body = _json.loads(e.read())
            msg = body.get('error') or body.get('detail') or str(e)
        except Exception:
            msg = str(e)
        return False, session_id, msg
    except Exception as e:
        return False, session_id, str(e)
    return True, session_id, None


def _wait_for_script_result(session_id: int, timeout_s: int):
    import json as _json
    deadline = _time.time() + max(5, int(timeout_s) + 15)
    while _time.time() < deadline:
        row = db.session.execute(
            text("""SELECT data_json
                    FROM rmm_event
                    WHERE session_id = :sid
                      AND actor_type = 'agent'
                      AND event_type = 'script_result'
                    ORDER BY id DESC
                    LIMIT 1"""),
            {'sid': session_id}
        ).fetchone()
        if row:
            try:
                return _json.loads(row[0] or '{}')
            except Exception:
                return {'stdout': '', 'stderr': 'Invalid result payload', 'exit_code': 1}
        _time.sleep(1.0)
    return None


@bp.route('/api/settings/scripts', methods=['GET'])
@login_required
@admin_required
def api_settings_scripts_list():
    _ensure_rmm_script_library_table()
    rows = db.session.execute(text("""
        SELECT id, name, description, file_type, shell, script_content,
               is_tested, last_tested_at, last_tested_agent_id,
               created_at, updated_at
        FROM rmm_script_library
        WHERE is_active = true
        ORDER BY name ASC, id DESC
    """)).mappings().fetchall()
    scripts = []
    for r in rows:
        item = dict(r)
        item['last_tested_at'] = _dt_iso(item.get('last_tested_at'))
        item['created_at'] = _dt_iso(item.get('created_at'))
        item['updated_at'] = _dt_iso(item.get('updated_at'))
        scripts.append(item)
    return jsonify(ok=True, scripts=scripts)


@bp.route('/api/settings/scripts', methods=['POST'])
@login_required
@admin_required
def api_settings_scripts_save():
    _ensure_rmm_script_library_table()
    data = request.get_json(force=True) or {}
    script_id = data.get('id')
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    script_content = (data.get('script_content') or '').strip()
    try:
        file_type = _normalize_script_file_type(data.get('file_type'))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    if not name:
        return jsonify(ok=False, error='name is required'), 400
    if not script_content:
        return jsonify(ok=False, error='script_content is required'), 400

    shell = _SCRIPT_FILE_TYPES[file_type]
    if script_id:
        row = db.session.execute(
            text("SELECT script_content, file_type FROM rmm_script_library WHERE id = :id AND is_active = true"),
            {'id': int(script_id)}
        ).fetchone()
        if not row:
            return jsonify(ok=False, error='script not found'), 404
        content_changed = (row[0] or '') != script_content or (row[1] or '') != file_type
        db.session.execute(text("""
            UPDATE rmm_script_library
            SET name = :name,
                description = :description,
                file_type = :file_type,
                shell = :shell,
                script_content = :script_content,
                is_tested = CASE WHEN :changed THEN false ELSE is_tested END,
                updated_at = NOW()
            WHERE id = :id
        """), {
            'id': int(script_id),
            'name': name,
            'description': description,
            'file_type': file_type,
            'shell': shell,
            'script_content': script_content,
            'changed': bool(content_changed),
        })
        db.session.commit()
        return jsonify(ok=True, id=int(script_id), message='Script updated')

    row = db.session.execute(text("""
        INSERT INTO rmm_script_library
            (name, description, file_type, shell, script_content,
             created_by_user_id, created_at, updated_at, is_active)
        VALUES
            (:name, :description, :file_type, :shell, :script_content,
             :uid, NOW(), NOW(), true)
        RETURNING id
    """), {
        'name': name,
        'description': description,
        'file_type': file_type,
        'shell': shell,
        'script_content': script_content,
        'uid': current_user.id if hasattr(current_user, 'id') else None,
    }).fetchone()
    db.session.commit()
    return jsonify(ok=True, id=int(row[0]), message='Script saved')


@bp.route('/api/settings/scripts/upload', methods=['POST'])
@login_required
@admin_required
def api_settings_scripts_upload():
    _ensure_rmm_script_library_table()
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(ok=False, error='file is required'), 400

    filename = secure_filename(f.filename)
    if '.' not in filename:
        return jsonify(ok=False, error='uploaded file must have an extension'), 400
    ext = f".{filename.rsplit('.', 1)[1].lower()}"
    try:
        file_type = _normalize_script_file_type(ext)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    raw = f.read()
    if len(raw) > 1024 * 1024:
        return jsonify(ok=False, error='script file too large (max 1 MB)'), 400
    try:
        script_content = raw.decode('utf-8')
    except UnicodeDecodeError:
        script_content = raw.decode('latin-1')

    script_content = script_content.replace('\r\n', '\n').strip()
    if not script_content:
        return jsonify(ok=False, error='script file is empty'), 400

    name = (request.form.get('name') or os.path.splitext(filename)[0]).strip()
    description = (request.form.get('description') or '').strip()
    shell = _SCRIPT_FILE_TYPES[file_type]

    row = db.session.execute(text("""
        INSERT INTO rmm_script_library
            (name, description, file_type, shell, script_content,
             created_by_user_id, created_at, updated_at, is_active)
        VALUES
            (:name, :description, :file_type, :shell, :script_content,
             :uid, NOW(), NOW(), true)
        RETURNING id
    """), {
        'name': name,
        'description': description,
        'file_type': file_type,
        'shell': shell,
        'script_content': script_content,
        'uid': current_user.id if hasattr(current_user, 'id') else None,
    }).fetchone()
    db.session.commit()
    return jsonify(ok=True, id=int(row[0]), message='Script uploaded')


@bp.route('/api/settings/scripts/<int:script_id>', methods=['DELETE'])
@login_required
@admin_required
def api_settings_scripts_delete(script_id):
    _ensure_rmm_script_library_table()
    db.session.execute(text("""
        UPDATE rmm_script_library
        SET is_active = false, updated_at = NOW()
        WHERE id = :id
    """), {'id': script_id})
    db.session.commit()
    return jsonify(ok=True)


@bp.route('/api/settings/scripts/<int:script_id>/test', methods=['POST'])
@login_required
@admin_required
def api_settings_scripts_test(script_id):
    _ensure_rmm_script_library_table()
    data = request.get_json(force=True) or {}
    agent_id = (data.get('agent_id') or '').strip()
    timeout_s = int(data.get('timeout', 90) or 90)
    if not agent_id:
        return jsonify(ok=False, error='agent_id is required'), 400

    script = db.session.execute(text("""
        SELECT id, name, file_type, shell, script_content
        FROM rmm_script_library
        WHERE id = :id AND is_active = true
    """), {'id': script_id}).mappings().fetchone()
    if not script:
        return jsonify(ok=False, error='script not found'), 404

    online_row = db.session.execute(text("""
        SELECT 1
        FROM rmm_agent
        WHERE agent_id = :aid
          AND enabled = true
          AND last_seen_at > NOW() - INTERVAL '5 minutes'
    """), {'aid': agent_id}).fetchone()
    if not online_row:
        return jsonify(ok=False, error='selected agent is offline'), 400

    ok, session_id, err = _send_script_to_agent(
        agent_id=agent_id,
        shell=script['shell'],
        code=script['script_content'],
        timeout_s=timeout_s,
        reason=f"Test script: {script['name']}",
    )
    if not ok:
        return jsonify(ok=False, error=err or 'failed to dispatch script'), 502

    result = _wait_for_script_result(session_id, timeout_s)
    if result is None:
        db.session.execute(text("""
            UPDATE rmm_script_library
            SET is_tested = false,
                last_tested_at = NOW(),
                last_tested_agent_id = :aid,
                last_test_result = :res,
                updated_at = NOW()
            WHERE id = :id
        """), {
            'id': script_id,
            'aid': agent_id,
            'res': 'Timed out waiting for script_result',
        })
        db.session.commit()
        return jsonify(ok=False, error='Timed out waiting for script result'), 504

    exit_code = int(result.get('exit_code', 1) or 1)
    stdout = (result.get('stdout') or '').strip()
    stderr = (result.get('stderr') or '').strip()
    summary = (stdout[:1000] + ('\n...' if len(stdout) > 1000 else '')).strip()
    if stderr:
        summary = (summary + '\nSTDERR:\n' + stderr[:1000]).strip()

    db.session.execute(text("""
        UPDATE rmm_script_library
        SET is_tested = :tested,
            last_tested_at = NOW(),
            last_tested_agent_id = :aid,
            last_test_result = :res,
            updated_at = NOW()
        WHERE id = :id
    """), {
        'id': script_id,
        'tested': exit_code == 0,
        'aid': agent_id,
        'res': summary,
    })
    db.session.commit()

    return jsonify(
        ok=(exit_code == 0),
        session_id=session_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        tested=(exit_code == 0),
    )


@bp.route('/api/settings/scripts/generate', methods=['POST'])
@login_required
@admin_required
def api_settings_scripts_generate():
    data = request.get_json(force=True) or {}
    prompt = (data.get('prompt') or '').strip()
    try:
        file_type = _normalize_script_file_type(data.get('file_type'))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    if not prompt:
        return jsonify(ok=False, error='prompt is required'), 400

    script_language = {
        '.ps1': 'PowerShell',
        '.bat': 'Windows Batch (.bat)',
        '.sh': 'POSIX shell (.sh)',
    }[file_type]

    try:
        raw = _ai_engine._openai_chat(
            [
                {
                    'role': 'system',
                    'content': (
                        f'Generate only {script_language} code. '
                        'Return code only with no markdown fences and no extra explanation.'
                    ),
                },
                {
                    'role': 'user',
                    'content': prompt,
                },
            ],
            max_tokens=1200,
        )
        code = (raw or '').strip()
        if code.startswith('```'):
            code = code.strip('`')
            nl = code.find('\n')
            if nl != -1:
                code = code[nl + 1:]
            if code.endswith('```'):
                code = code[:-3]
            code = code.strip()
        return jsonify(ok=True, script_content=code)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@bp.route('/api/unifi/test', methods=['POST'])
@login_required
@admin_required
def api_unifi_test():
    """Test UniFi controller connection."""
    from unifi_service import UnifiService, load_unifi_config
    config = load_unifi_config(Setting)
    if not config:
        return jsonify({'success': False, 'message': 'UniFi credentials not configured'})
    svc = UnifiService(**config)
    result = svc.test_connection()
    return jsonify(result)


@bp.route('/api/unifi/sync', methods=['POST'])
@login_required
@admin_required
def api_unifi_sync():
    """Manually trigger a UniFi device sync."""
    from unifi_service import sync_unifi_assets
    try:
        summary = sync_unifi_assets(app, db, Asset, Setting, AssetHistory, MonitoringAlert)
        return jsonify({'success': True, 'summary': summary})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@bp.route('/api/ad/test', methods=['POST'])
@login_required
@admin_required
def api_ad_test():
    try:
        from ldap_service import load_ad_config, LDAPService

        cfg = load_ad_config(Setting)
        service = LDAPService(cfg)
        result = service.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/ad/user/disable', methods=['POST'])
@login_required
@admin_required
def api_ad_disable_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    if not username:
        return jsonify({'success': False, 'error': 'username is required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.disable_user(username))


@bp.route('/api/ad/user/enable', methods=['POST'])
@login_required
@admin_required
def api_ad_enable_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    if not username:
        return jsonify({'success': False, 'error': 'username is required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.enable_user(username))


@bp.route('/api/ad/user/delete', methods=['POST'])
@login_required
@admin_required
def api_ad_delete_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    if not username:
        return jsonify({'success': False, 'error': 'username is required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.delete_user(username))


@bp.route('/api/ad/user/create', methods=['POST'])
@login_required
@admin_required
def api_ad_create_user():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    user_principal_name = (payload.get('user_principal_name') or payload.get('upn') or '').strip()
    display_name = (payload.get('display_name') or '').strip()
    email = (payload.get('email') or '').strip() or None
    password = payload.get('password') or None
    enable = bool(payload.get('enable', True))
    ou_dn = (payload.get('ou_dn') or '').strip() or None

    if not username or not user_principal_name or not display_name:
        return jsonify({'success': False, 'error': 'username, user_principal_name, display_name are required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    result = service.create_user(
        username=username,
        user_principal_name=user_principal_name,
        display_name=display_name,
        email=email,
        password=password,
        enable=enable,
        ou_dn=ou_dn,
    )
    status = 200 if result.get('success') else 400
    return jsonify(result), status


@bp.route('/api/ad/group/add-member', methods=['POST'])
@login_required
@admin_required
def api_ad_group_add_member():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    group_dn = (payload.get('group_dn') or '').strip()
    if not username or not group_dn:
        return jsonify({'success': False, 'error': 'username and group_dn are required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.add_user_to_group(username, group_dn))


@bp.route('/api/ad/group/remove-member', methods=['POST'])
@login_required
@admin_required
def api_ad_group_remove_member():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    group_dn = (payload.get('group_dn') or '').strip()
    if not username or not group_dn:
        return jsonify({'success': False, 'error': 'username and group_dn are required'}), 400

    from ldap_service import load_ad_config, LDAPService
    service = LDAPService(load_ad_config(Setting))
    return jsonify(service.remove_user_from_group(username, group_dn))


@bp.route('/api/settings/eagle-eye-exclusions', methods=['GET'])
@login_required
@admin_required
def api_eagle_exclusions_list():
    """Return all eagle-eyes exclusions and all available agents for the picker."""
    try:
        exclusions = db.session.execute(text(
            "SELECT agent_id, hostname, notes, added_by, added_at FROM eagle_eyes_exclusions ORDER BY COALESCE(hostname, agent_id)"
        )).mappings().fetchall()
        all_agents = db.session.execute(text(
            """SELECT ec.agent_id, COALESCE(t.hostname, ec.agent_id) AS hostname
               FROM rmm_eagle_config ec
               LEFT JOIN rmm_telemetry t ON t.agent_id = ec.agent_id
               WHERE ec.enabled = true
               ORDER BY COALESCE(t.hostname, ec.agent_id)"""
        )).mappings().fetchall()
        excluded_ids = {r['agent_id'] for r in exclusions}
        return jsonify(
            ok=True,
            exclusions=[dict(r) for r in exclusions],
            all_agents=[dict(r) for r in all_agents if r['agent_id'] not in excluded_ids]
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/settings/eagle-eye-exclusions', methods=['POST'])
@login_required
@admin_required
def api_eagle_exclusions_add():
    """Add an agent to the Eagle Eyes exclusion list."""
    try:
        data = request.get_json(force=True)
        agent_id = (data.get('agent_id') or '').strip()
        notes    = (data.get('notes') or '').strip()
        if not agent_id:
            return jsonify(ok=False, error='agent_id is required'), 400
        # Resolve hostname
        row = db.session.execute(text(
            "SELECT COALESCE(t.hostname, :aid) AS hostname FROM rmm_agent a LEFT JOIN rmm_telemetry t ON t.agent_id = a.agent_id WHERE a.agent_id = :aid"
        ), {'aid': agent_id}).mappings().fetchone()
        hostname = row['hostname'] if row else agent_id
        db.session.execute(text(
            """INSERT INTO eagle_eyes_exclusions (agent_id, hostname, notes, added_by, added_at)
               VALUES (:agent_id, :hostname, :notes, :added_by, now())
               ON CONFLICT (agent_id) DO UPDATE SET hostname=EXCLUDED.hostname, notes=EXCLUDED.notes, added_by=EXCLUDED.added_by, added_at=now()"""
        ), {'agent_id': agent_id, 'hostname': hostname, 'notes': notes, 'added_by': current_user.username})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500


@bp.route('/api/settings/eagle-eye-exclusions/<path:agent_id>', methods=['DELETE'])
@login_required
@admin_required
def api_eagle_exclusions_remove(agent_id):
    """Remove an agent from the Eagle Eyes exclusion list."""
    try:
        db.session.execute(text(
            "DELETE FROM eagle_eyes_exclusions WHERE agent_id = :aid"
        ), {'aid': agent_id})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500


@bp.route('/init-db')
def init_db():
    """Initialize database and create admin user"""
    db.create_all()
    
    # Check if admin exists
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@company.com',
            password_hash=generate_password_hash('admin123'),
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        return 'Database initialized! Admin user created (username: admin, password: admin123)'
    
    return 'Database already initialized!'


@bp.route('/api/license', methods=['GET'])
@login_required
@admin_required
def get_license():
    """Get current license information"""
    try:
        license_info = LicenseInfo.query.order_by(LicenseInfo.id.desc()).first()
        
        if not license_info:
            return jsonify({'exists': False})
        
        # Calculate days remaining if expiry date exists
        days_remaining = None
        if license_info.expiry_date:
            exp = license_info.expiry_date
            from datetime import timezone as _tz
            now = datetime.now(_tz.utc) if (exp.tzinfo is not None) else datetime.utcnow()
            delta = exp - now
            days_remaining = max(0, delta.days)
        
        return jsonify({
            'exists': True,
            'license': {
                'id': license_info.id,
                'license_key': license_info.license_key,
                'api_key': license_info.api_key[:8] + '...' if license_info.api_key and len(license_info.api_key) > 8 else None,
                'device_id': license_info.device_id,
                'status': license_info.status,
                'company_name': license_info.company_name,
                'plan_name': license_info.plan_name,
                'expiry_date': license_info.expiry_date.isoformat() if license_info.expiry_date else None,
                'days_remaining': days_remaining,
                'max_devices': license_info.max_devices,
                'last_checked': license_info.last_checked.isoformat() if license_info.last_checked else None,
                'last_check_status': license_info.last_check_status,
                'grace_period_ends': license_info.grace_period_ends.isoformat() if license_info.grace_period_ends else None,
                'created_at': license_info.created_at.isoformat(),
                'updated_at': license_info.updated_at.isoformat()
            }
        })
    except Exception as e:
        logger.error(f'Error fetching license: {str(e)}')
        return jsonify({'error': 'Failed to fetch license'}), 500


@bp.route('/api/license', methods=['POST'])
@login_required
@admin_required
def save_license():
    """Save or update license"""
    try:
        data = request.get_json()
        license_key = data.get('licenseKey')
        company_name = data.get('companyName')
        api_key = data.get('apiKey')
        device_id = data.get('deviceId')
        
        if not license_key:
            return jsonify({'error': 'License key is required'}), 400
        
        # Delete existing licenses
        LicenseInfo.query.delete()
        
        # Create new license
        new_license = LicenseInfo(
            license_key=license_key,
            company_name=company_name,
            api_key=api_key if api_key else None,
            device_id=device_id if device_id else None,
            status='pending'
        )
        db.session.add(new_license)
        db.session.commit()
        
        return jsonify({
            'message': 'License saved successfully',
            'license': {
                'id': new_license.id,
                'license_key': new_license.license_key,
                'company_name': new_license.company_name,
                'api_key': '***' if new_license.api_key else None,
                'device_id': new_license.device_id,
                'status': new_license.status
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error saving license: {str(e)}')
        return jsonify({'error': 'Failed to save license'}), 500


@bp.route('/api/license/verify', methods=['POST'])
@login_required
@admin_required
def verify_license():
    """Manually verify license with server"""
    try:
        license_service.perform_check()
        
        license_info = LicenseInfo.query.order_by(LicenseInfo.id.desc()).first()
        
        if not license_info:
            return jsonify({'error': 'No license found'}), 404
        
        # Calculate days remaining
        days_remaining = None
        if license_info.expiry_date:
            delta = license_info.expiry_date - datetime.utcnow()
            days_remaining = max(0, delta.days)
        
        return jsonify({
            'message': 'License verification completed',
            'license': {
                'id': license_info.id,
                'status': license_info.status,
                'plan_name': license_info.plan_name,
                'expiry_date': license_info.expiry_date.isoformat() if license_info.expiry_date else None,
                'days_remaining': days_remaining,
                'last_checked': license_info.last_checked.isoformat() if license_info.last_checked else None,
                'last_check_status': license_info.last_check_status,
                'grace_period_ends': license_info.grace_period_ends.isoformat() if license_info.grace_period_ends else None
            }
        })
    except Exception as e:
        logger.error(f'Error verifying license: {str(e)}')
        return jsonify({'error': f'Failed to verify license: {str(e)}'}), 500


@bp.route('/api/license/<int:license_id>', methods=['DELETE'])
@login_required
@admin_required
def remove_license_key(license_id):
    """Delete a license"""
    try:
        license_info = LicenseInfo.query.get_or_404(license_id)
        db.session.delete(license_info)
        db.session.commit()
        
        return jsonify({'message': 'License removed successfully'})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error deleting license: {str(e)}')
        return jsonify({'error': 'Failed to remove license'}), 500


@bp.route('/api/settings/<key>', methods=['GET'])
@login_required
def api_setting_get(key):
    allowed = {'teams_webhook_url'}
    if key not in allowed:
        return jsonify(ok=False, error='Not allowed'), 403
    s = db.session.execute(text("SELECT value FROM setting WHERE key=:k"), {'k': key}).fetchone()
    return jsonify(ok=True, key=key, value=s[0] if s else '')


@bp.route('/api/settings', methods=['POST'])
@login_required
def api_setting_set():
    data = request.get_json(force=True) or {}
    key  = data.get('key', '')
    val  = data.get('value', '')
    allowed = {'teams_webhook_url'}
    if key not in allowed:
        return jsonify(ok=False, error='Not allowed'), 403
    existing = db.session.execute(text("SELECT id FROM setting WHERE key=:k"), {'k': key}).fetchone()
    if existing:
        db.session.execute(text("UPDATE setting SET value=:v WHERE key=:k"), {'k': key, 'v': val})
    else:
        db.session.execute(text("INSERT INTO setting (key, value) VALUES (:k, :v)"), {'k': key, 'v': val})
    db.session.commit()
    return jsonify(ok=True)