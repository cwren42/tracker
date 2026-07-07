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
from secret_store import encrypt_secret, encrypt_if_secret, decrypt_secret
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
    AuditTrail, Asset, AssetHistory, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping, _log_audit,
)
from soc2_models import SOC2Control, EvidenceSnapshot
import logging
from license_service import license_service
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, _get_or_create_site_enrollment_token, _dt_iso,
)
logger = logging.getLogger(__name__)


bp = Blueprint('settings', __name__)


# ==================== SETTINGS ====================


# NOTE: .sh/bash intentionally omitted — agents run any non-cmd script as PowerShell,
# so a .sh would silently run under PowerShell. Re-add with a real agent bash branch
# (AGENT_VERSION bump + self-update) if/when Linux script execution is needed.
_SCRIPT_FILE_TYPES = {
    '.ps1': 'powershell',
    '.bat': 'cmd',
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
        'license',
        'directory', 'rmm', 'scripts', 'eagleeye',
        'unifi', 'proxmox', 'ai', 'cloudflare'
    }
    section_endpoints = {
        'license': 'settings.settings_license',
        'directory': 'settings.settings_directory',
        'rmm': 'settings.settings_rmm',
        'scripts': 'settings.settings_scripts',
        'eagleeye': 'settings.settings_eagleeye',
        'unifi': 'settings.settings_unifi',
        'proxmox': 'settings.settings_proxmox',
        'ai': 'settings.settings_ai',
        'cloudflare': 'settings.settings_cloudflare',
    }
    if section and section not in allowed_sections:
        flash('Unknown settings page.', 'warning')
        return redirect(url_for('settings.settings'))

    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_unifi':
            # Save UniFi credentials
            def _save(key, val):
                s = Setting.query.filter_by(key=key).first()
                if not s:
                    s = Setting(key=key)
                    db.session.add(s)
                s.value = encrypt_if_secret(key, val)
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

        elif action == 'update_ad':
            ad_enabled = request.form.get('ad_enabled') == 'on'
            ad_server = request.form.get('ad_server', '').strip()
            ad_port = request.form.get('ad_port', '').strip()
            ad_use_ssl = request.form.get('ad_use_ssl') == 'on'
            ad_base_dn = request.form.get('ad_base_dn', '').strip()
            ad_bind_username = request.form.get('ad_bind_username', '').strip()
            ad_bind_password = request.form.get('ad_bind_password', '')
            ad_user_ou_dn = request.form.get('ad_user_ou_dn', '').strip()
            ad_computer_ou_dn = request.form.get('ad_computer_ou_dn', '').strip()
            ad_ou_as_department = request.form.get('ad_ou_as_department') == 'on'

            try:
                settings_to_update = {
                    'ad_enabled': 'true' if ad_enabled else 'false',
                    'ad_server': ad_server,
                    'ad_port': ad_port,
                    'ad_use_ssl': 'true' if ad_use_ssl else 'false',
                    'ad_base_dn': ad_base_dn,
                    'ad_bind_username': ad_bind_username,
                    'ad_user_ou_dn': ad_user_ou_dn,
                    'ad_computer_ou_dn': ad_computer_ou_dn,
                    'ad_ou_as_department': 'true' if ad_ou_as_department else 'false',
                }
                for key, value in settings_to_update.items():
                    setting = Setting.query.filter_by(key=key).first()
                    if not setting:
                        setting = Setting(key=key)
                    setting.value = encrypt_if_secret(key, value)
                    setting.updated_by = current_user.username
                    setting.updated_at = datetime.utcnow()
                    db.session.add(setting)

                # Only update password if provided
                if ad_bind_password:
                    setting = Setting.query.filter_by(key='ad_bind_password').first()
                    if not setting:
                        setting = Setting(key='ad_bind_password')
                    setting.value = encrypt_secret(ad_bind_password)
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
                    setting.value = encrypt_if_secret(key, value)
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

    # GET — each section has its own dedicated page now. Send the user to the
    # requested section, or default the bare /settings to the License page.
    # (Previously a GET fell through here with no return -> 500.)
    if section:
        return redirect(url_for(section_endpoints[section]))
    return redirect(url_for('settings.settings_license'))


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
        'ad_computer_ou_dn': get_setting_value('ad_computer_ou_dn', ''),
        'ad_ou_as_department': get_setting_value('ad_ou_as_department', 'true') == 'true',
    }
    
    return render_template('settings_directory.html', ad_config=ad_config)


def _rmm_agent_status():
    """Stable vs canary agent versions, the canary roster, and the live version spread
    (from rmm_telemetry) for the /settings/rmm visibility panel. Fail-soft to defaults."""
    base = os.path.join(current_app.root_path, 'rmm_agent')

    def _ver(*parts):
        try:
            with open(os.path.join(base, *parts)) as f:
                return f.read().strip()
        except Exception:
            return '?'

    roster, spread = [], []
    try:
        row = db.session.execute(
            text("SELECT value FROM setting WHERE key='rmm_agent_canary'")).fetchone()
        if row and row[0]:
            roster = [p.strip() for p in re.split(r'[,\s]+', row[0]) if p.strip()]
    except Exception:
        pass
    try:
        rows = db.session.execute(text("""
            SELECT COALESCE(NULLIF(agent_version,''),'(unknown)') AS v, COUNT(*) AS n
            FROM rmm_telemetry GROUP BY 1 ORDER BY n DESC, v DESC""")).fetchall()
        spread = [{'version': r[0], 'count': r[1]} for r in rows]
    except Exception:
        pass

    return {
        'stable_version': _ver('version.txt'),
        'canary_version': _ver('canary', 'version.txt'),
        'canary_roster': roster,
        'version_spread': spread,
    }


@bp.route('/settings/rmm', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_rmm():
    if request.method == 'POST':
        return settings('rmm')

    rmm_site_token = _get_or_create_site_enrollment_token()
    return render_template('settings_rmm.html', rmm_site_token=rmm_site_token,
                           agent_status=_rmm_agent_status())


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
        # Display the linked ASSET's current name, not the raw agent_id. agent_id is
        # the hostname pinned at enrollment and does NOT follow a rename (BRENDA-DELL2
        # -> shellee-dell), so it reads stale; the asset carries the current name.
        # Join asset (1:1), NOT rmm_telemetry (many-per-agent -> duplicate rows).
        all_agents = db.session.execute(text(
            """SELECT ra.agent_id, COALESCE(a.name, ra.agent_id) AS hostname
               FROM rmm_agent ra
               LEFT JOIN asset a ON a.id = ra.asset_id
               WHERE ra.enabled = true
               ORDER BY COALESCE(a.name, ra.agent_id)"""
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

    # Work-Hours report access allowlist (admins always implicitly allowed).
    try:
        from utils import workhours_viewers_list
        context['workhours_viewers'] = ', '.join(workhours_viewers_list())
    except Exception:
        context['workhours_viewers'] = ''

    # Work-Hours device exclusion (glob patterns; lab/build/test/kiosk/prod boxes).
    try:
        from utils import workhours_excluded_patterns
        context['workhours_excluded_devices'] = ', '.join(workhours_excluded_patterns())
    except Exception:
        context['workhours_excluded_devices'] = ''

    return render_template('settings_eagleeye.html', **context)


@bp.route('/settings/workhours-access', methods=['POST'])
@login_required
@admin_required
def settings_workhours_access():
    """Save the Work-Hours report access allowlist (CSV of usernames).

    Admins are always implicitly allowed and need not be listed. Manager-scoping
    (workhours_manager_map) is pure data entry and is not edited here."""
    raw = request.form.get('workhours_viewers', '')
    # Normalize: split on comma/whitespace, dedupe (case-insensitive), keep order.
    seen = set()
    cleaned = []
    for tok in raw.replace('\n', ',').replace(' ', ',').split(','):
        t = tok.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(t)
    value = ','.join(cleaned)
    try:
        s = Setting.query.filter_by(key='workhours_viewers').first()
        if not s:
            s = Setting(key='workhours_viewers')
            db.session.add(s)
        s.value = value
        s.updated_by = current_user.username
        s.updated_at = datetime.utcnow()
        _log_audit('setting', 0, 'workhours.allowlist_update', {'viewers': value})
        db.session.commit()
        flash('Work-Hours access list updated.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving Work-Hours access list: {e}', 'danger')
    return redirect(url_for('settings.settings_eagleeye'))


@bp.route('/settings/workhours-excluded-devices', methods=['POST'])
@login_required
@admin_required
def settings_workhours_excluded_devices():
    """Save the Work-Hours device-exclusion list (comma/newline-separated globs).

    Patterns are fnmatch-style ('*', '?', '[1-5]') matched whole-string,
    case-insensitively, against the device name (asset.name else agent_id). These are
    lab/build/test/kiosk/prod boxes hidden from the report — layered on top of the
    server-class exclusion and the viewer allowlist/manager scoping."""
    raw = request.form.get('workhours_excluded_devices', '')
    # Normalize: split on comma/newline (NOT spaces — patterns have no spaces but keep it
    # simple/forgiving), dedupe case-insensitively, preserve order.
    seen = set()
    cleaned = []
    for tok in raw.replace('\n', ',').split(','):
        t = tok.strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(t)
    value = ', '.join(cleaned)
    try:
        s = Setting.query.filter_by(key='workhours_excluded_devices').first()
        if not s:
            s = Setting(key='workhours_excluded_devices')
            db.session.add(s)
        s.value = value
        s.updated_by = current_user.username
        s.updated_at = datetime.utcnow()
        _log_audit('setting', 0, 'workhours.excluded_devices_update', {'patterns': value})
        db.session.commit()
        flash('Work-Hours device exclusion list updated.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving Work-Hours device exclusion list: {e}', 'danger')
    return redirect(url_for('settings.settings_eagleeye'))


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
        # Display the linked ASSET's current name, not the raw agent_id. agent_id is
        # the hostname pinned at enrollment and does NOT follow a rename (BRENDA-DELL2
        # -> shellee-dell), so it reads stale; the asset carries the current name.
        # Join asset (1:1), NOT rmm_telemetry (many-per-agent -> duplicate rows).
        all_agents = db.session.execute(text(
            """SELECT ra.agent_id, COALESCE(a.name, ra.agent_id) AS hostname
               FROM rmm_agent ra
               LEFT JOIN asset a ON a.id = ra.asset_id
               WHERE ra.enabled = true
               ORDER BY COALESCE(a.name, ra.agent_id)"""
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
        # Audit: who excluded which device from Eagle Eyes visibility (rides this transaction).
        _log_audit('eagle_eyes_exclusions', 0, 'eagle_eyes.exclusion_add',
                   {'agent_id': agent_id, 'hostname': hostname, 'notes': notes})
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
        # Audit: who REMOVED an Eagle Eyes exclusion (device becomes visible again).
        _log_audit('eagle_eyes_exclusions', 0, 'eagle_eyes.exclusion_remove',
                   {'agent_id': agent_id})
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
@admin_required
def api_setting_get(key):
    allowed = {'teams_webhook_url'}
    if key not in allowed:
        return jsonify(ok=False, error='Not allowed'), 403
    s = db.session.execute(text("SELECT value FROM setting WHERE key=:k"), {'k': key}).fetchone()
    return jsonify(ok=True, key=key, value=s[0] if s else '')


@bp.route('/api/settings', methods=['POST'])
@login_required
@admin_required
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


# Script-library routes split into a sibling module (registered on bp above)
from blueprints import settings_scripts  # noqa: E402,F401
