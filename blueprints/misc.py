import json
import os
import re
import subprocess
import threading
import time as _time
from datetime import datetime, timedelta, timezone
try:
    import report_engine as _report_engine
except ImportError:
    _report_engine = None
import requests

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
import alert_service as _alert_svc
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, RMM_GATEWAY_PUBLIC, RMM_TRACKER_URL,
    _valid_agent_key, _get_or_create_site_enrollment_token,
)
logger = logging.getLogger(__name__)


bp = Blueprint('misc', __name__)


# ==================== LINUX AGENT API ====================


# ==================== API ENDPOINTS ====================


# ── Agent installer download ─────────────────────────────────────────────────



# ── Restored: /download/agent-installer ──


# ── Restored: /download/agent-ps1 ──


# ── Restored: /download/agent-file/<path:filename> ──


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FOR RAW DB ACCESS (security/workflow/report routes)
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    from pg_db import pg_connect
    return pg_connect()


# ─────────────────────────────────────────────────────────────────────────────
# ALERT CENTER
# ─────────────────────────────────────────────────────────────────────────────



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


@bp.route('/agent/download')
def agent_download():
    """Serve the Linux agent Python script for download"""
    agent_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'agent.py')
    return send_file(agent_path, as_attachment=True, download_name='cirque-rmm-agent')


@bp.route('/agent/install.sh')
def agent_install_script():
    """Serve the agent installer script with the API key pre-filled"""
    from flask import Response
    script_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'install.sh')
    with open(script_path, 'r') as f:
        script = f.read()
    # Inject current API key so the installer works without any manual config
    api_key = current_app.config.get('LINUX_AGENT_API_KEY', '')
    script = script.replace(
        'API_KEY="${API_KEY:-}"',
        f'API_KEY="${{API_KEY:-{api_key}}}"'
    )
    return Response(script, mimetype='text/x-shellscript')


@bp.route('/agent/service')
def agent_service_file():
    """Serve the systemd service file"""
    service_path = os.path.join(os.path.dirname(__file__), 'linux_agent', 'cirque-rmm-agent.service')
    return send_file(service_path, mimetype='text/plain')


@bp.route('/api/linux-agent/heartbeat', methods=['POST'])
@limiter.limit("120 per minute")
def api_linux_agent_heartbeat():
    """Receive heartbeat from Linux monitoring agent"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not _valid_agent_key(api_key):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    data = request.get_json()
    
    try:
        agent_id = data.get('agent_id')
        asset_id = data.get('asset_id')
        system_info = data.get('system_info', {})
        metrics = data.get('metrics', {})
        disks = data.get('disks', [])
        services_running = data.get('services_running', 0)
        updates = data.get('updates', {})
        
        # Save heartbeat to database
        cursor = db.session.execute(text("""
            INSERT INTO linux_agent_heartbeat 
            (agent_id, asset_id, system_info, metrics, disks, services_running, 
             updates_available, security_updates, timestamp)
            VALUES (:agent_id, :asset_id, :system_info, :metrics, :disks, 
                    :services_running, :updates_available, :security_updates, :timestamp)
        """), {
            'agent_id': agent_id,
            'asset_id': asset_id,
            'system_info': json.dumps(system_info),
            'metrics': json.dumps(metrics),
            'disks': json.dumps(disks),
            'services_running': services_running,
            'updates_available': updates.get('available', 0),
            'security_updates': updates.get('security', 0),
            'timestamp': datetime.utcnow()
        })
        db.session.commit()
        
        # If asset_id provided, update last seen
        if asset_id:
            cursor = db.session.execute(text("""
                UPDATE assets 
                SET last_seen = :last_seen 
                WHERE id = :asset_id
            """), {
                'last_seen': datetime.utcnow(),
                'asset_id': asset_id
            })
            db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Heartbeat received',
            'timestamp': datetime.utcnow().isoformat()
        })
    
    except Exception as e:
        print(f"Error processing agent heartbeat: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/linux-agent/checks', methods=['GET'])
@limiter.limit("120 per minute")
def api_linux_agent_checks():
    """Get checks that Linux agent should execute"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not _valid_agent_key(api_key):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    asset_id = request.args.get('asset_id')
    if not asset_id:
        return jsonify({'success': False, 'error': 'asset_id required'}), 400
    
    try:
        # Get checks for this asset's profile
        result = db.session.execute(text("""
            SELECT 
                mc.id, mc.name, mc.check_type, mc.check_command,
                mc.warning_threshold, mc.critical_threshold, 
                mc.check_interval_minutes
            FROM monitoring_check mc
            JOIN profile_check pc ON mc.id = pc.check_id
            JOIN asset_monitoring_profile amp ON pc.profile_id = amp.profile_id
            WHERE amp.asset_id = :asset_id AND mc.enabled = true
        """), {'asset_id': asset_id})
        
        checks = []
        for row in result:
            checks.append({
                'id': row[0],
                'name': row[1],
                'type': row[2],
                'command': row[3],
                'warning_threshold': row[4],
                'critical_threshold': row[5],
                'interval_minutes': row[6]
            })
        
        return jsonify({
            'success': True,
            'checks': checks
        })
    
    except Exception as e:
        print(f"Error getting agent checks: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/linux-agent/check-result', methods=['POST'])
@limiter.limit("120 per minute")
def api_linux_agent_check_result():
    """Receive check result from Linux agent"""
    # Check API key
    api_key = request.headers.get('X-API-Key')
    if not _valid_agent_key(api_key):
        return jsonify({'success': False, 'error': 'Invalid API key'}), 401
    
    data = request.get_json()
    
    try:
        asset_id = data.get('asset_id')
        check_id = data.get('check_id')
        status = data.get('status')
        value = data.get('value')
        message = data.get('message')
        
        # Save check result
        cursor = db.session.execute(text("""
            INSERT INTO monitoring_check_result 
            (asset_id, check_id, status, value, message, checked_at)
            VALUES (:asset_id, :check_id, :status, :value, :message, :checked_at)
        """), {
            'asset_id': asset_id,
            'check_id': check_id,
            'status': status,
            'value': str(value) if value is not None else None,
            'message': message,
            'checked_at': datetime.utcnow()
        })
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Check result saved'
        })
    
    except Exception as e:
        print(f"Error saving check result: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/asset/search')
@login_required
@license_required
def api_asset_search():
    """API endpoint to search for asset by tag (for QR scanner)"""
    asset_tag = request.args.get('tag', '')
    
    if not asset_tag:
        return jsonify({'success': False, 'message': 'Asset tag required'}), 400
    
    asset = Asset.query.filter_by(asset_tag=asset_tag).first()
    
    if asset:
        return jsonify({
            'success': True,
            'asset_id': asset.id,
            'asset_tag': asset.asset_tag,
            'name': asset.name,
            'category': asset.category,
            'status': asset.status
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Asset not found'
        }), 404


@bp.route('/download/agent-installer')
@login_required
@license_required
def download_agent_installer():
    """Serve the EXE installer (preferred), then MSI, then PS1 fallback."""
    import os
    # NSIS-built EXE: no msiexec Custom Action engine, most reliable
    exe_path = os.path.join(current_app.root_path, 'rmm_agent', 'CirqueRMM.exe')
    if os.path.exists(exe_path):
        return send_file(exe_path, as_attachment=True, download_name='CirqueRMM.exe',
                         mimetype='application/octet-stream')
    # Legacy MSI fallback
    msi_path = os.path.join(current_app.root_path, 'rmm_agent', 'CirqueRMM.msi')
    if os.path.exists(msi_path):
        return send_file(msi_path, as_attachment=True, download_name='CirqueRMM.msi',
                         mimetype='application/x-msi')
    # Last resort: bare PS1
    ps1_path = os.path.join(current_app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(ps1_path):
        return "Installer not found on server.", 404
    return send_file(ps1_path, as_attachment=True, download_name='CirqueRMM-Install.ps1',
                     mimetype='application/octet-stream')


@bp.route('/download/agent-ps1')
@login_required
@license_required
def download_agent_ps1():
    """Serve the raw PowerShell installer script directly."""
    import os
    path = os.path.join(current_app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(path):
        return 'Script not found on server.', 404
    return send_file(path, as_attachment=True, download_name='CirqueRMM-Install.ps1',
                     mimetype='application/octet-stream')


@bp.route('/download/deploy-ps1')
@login_required
@license_required
def download_deploy_ps1():
    """Serve the simple one-liner deploy script (off-LAN safe)."""
    import os
    path = os.path.join(current_app.root_path, 'rmm_agent', 'deploy_agent.ps1')
    if not os.path.exists(path):
        return 'Script not found on server.', 404
    return send_file(path, as_attachment=True, download_name='deploy_agent.ps1',
                     mimetype='application/octet-stream')


@bp.route('/get/deploy-ps1')
def download_deploy_ps1_public():
    """Public (no login) endpoint so 'irm https://…/get/deploy-ps1 | iex' works."""
    import os
    path = os.path.join(current_app.root_path, 'rmm_agent', 'deploy_agent.ps1')
    if not os.path.exists(path):
        return 'Script not found on server.', 404
    return send_file(path, as_attachment=False, download_name='deploy_agent.ps1',
                     mimetype='text/plain')


@bp.route('/get/agent-exe')
def download_agent_exe_public():
    """Public (no login) EXE download — used by deploy_agent.ps1 and irm|iex flows."""
    import os
    exe_path = os.path.join(current_app.root_path, 'rmm_agent', 'CirqueRMM.exe')
    if not os.path.exists(exe_path):
        return 'Installer not found on server.', 404
    return send_file(exe_path, as_attachment=True, download_name='CirqueRMM.exe',
                     mimetype='application/octet-stream')


@bp.route('/download/agent-bat')
@login_required
@license_required
def download_agent_bat():
    """Serve a .bat launcher that runs the PS1 bypassing execution policy.

    The user saves this .bat alongside CirqueRMM-Install.ps1 (or it downloads
    the PS1 automatically) and just double-clicks it as Administrator.
    """
    import hmac as _hmac
    site_token  = _get_or_create_site_enrollment_token()
    tracker_url = RMM_TRACKER_URL.rstrip('/')
    ps1_url     = f"{tracker_url}/download/site-install.ps1?t={site_token}"

    bat = (
        "@echo off\r\n"
        ":: Cirque RMM Agent Installer Launcher\r\n"
        ":: Right-click this file and choose 'Run as administrator'\r\n"
        "setlocal\r\n"
        "\r\n"
        ":: Check for admin rights\r\n"
        "net session >nul 2>&1\r\n"
        "if %errorlevel% neq 0 (\r\n"
        "    echo ERROR: Please right-click and choose 'Run as administrator'\r\n"
        "    pause\r\n"
        "    exit /b 1\r\n"
        ")\r\n"
        "\r\n"
        "echo Installing Cirque RMM Agent...\r\n"
        "echo Log: C:\\CirqueRMM\\logs\\setup.log\r\n"
        "echo.\r\n"
        "\r\n"
        f"powershell.exe -NoProfile -ExecutionPolicy Bypass -NonInteractive -Command \""
        f"[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
        f"irm '{ps1_url}' | iex\"\r\n"
        "\r\n"
        "if %errorlevel% neq 0 (\r\n"
        "    echo.\r\n"
        "    echo Installation failed. Check C:\\CirqueRMM\\logs\\setup.log\r\n"
        "    echo Also check %%TEMP%%\\CirqueRMM_install.log\r\n"
        ") else (\r\n"
        "    echo.\r\n"
        "    echo Installation complete!\r\n"
        ")\r\n"
        "pause\r\n"
    )

    from flask import Response
    return Response(bat.encode('ascii'),
                    mimetype='application/octet-stream',
                    headers={'Content-Disposition': 'attachment; filename="CirqueRMM-Install.bat"'})


@bp.route('/download/agent-msi')
@login_required
@license_required
def download_agent_msi():
    """Serve the MSI installer directly."""
    import os
    msi_path = os.path.join(current_app.root_path, 'rmm_agent', 'CirqueRMM.msi')
    if not os.path.exists(msi_path):
        return 'MSI installer not found on server.', 404
    return send_file(msi_path, as_attachment=True, download_name='CirqueRMM.msi',
                     mimetype='application/x-msi')


@bp.route('/download/site-install.ps1')
def download_site_install_ps1():
    """Serve a pre-configured PS1 authenticated by the site token in ?t=.

    Designed for the one-liner:  irm 'https://.../download/site-install.ps1?t=TOKEN' | iex
    No browser session required — the site token itself is the credential.

    Strips the param() block so iex can run the script without errors.
    iex cannot handle param() blocks — only script files/-File mode can.
    """
    import os, re as _re
    t = request.args.get('t', '').strip()
    expected = _get_or_create_site_enrollment_token()
    import hmac as _hmac
    if not t or not _hmac.compare_digest(t, expected):
        return 'Invalid or missing site token.', 403

    ps1_src = os.path.join(current_app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(ps1_src):
        return 'install_agent.ps1 not found on server.', 404

    # ?public=1 bypasses the internal URL entirely — use for devices that
    # can resolve but not reach the corporate network (e.g. China offices).
    use_public = request.args.get('public', '').strip() in ('1', 'true', 'yes')
    if use_public:
        tracker_url = 'https://tracker.cirquetools.com'
        gateway_url = 'wss://rmm.cirquetools.com'
    else:
        tracker_url = RMM_TRACKER_URL.rstrip('/')
        gateway_url = RMM_GATEWAY_PUBLIC.rstrip('/')
    tracker_url_fallback = 'https://tracker.cirquetools.com'
    gateway_url_fallback = 'wss://rmm.cirquetools.com'
    site_token  = expected

    ps1 = open(ps1_src, encoding='utf-8-sig').read()

    # ── Strip #Requires and <# ... #> comment block ───────────────────────
    ps1 = _re.sub(r'#Requires[^\n]*\n', '', ps1)
    ps1 = _re.sub(r'<#.*?#>', '', ps1, flags=_re.DOTALL)

    # ── Strip the param( ... ) block ──────────────────────────────────────
    # iex evaluates expressions, not script definitions — param() is invalid.
    # Match 'param' then everything up to the matching closing paren on its own line.
    ps1 = _re.sub(r'\bparam\s*\(.*?\n\)', '', ps1, flags=_re.DOTALL)

    # ── Prepend direct variable assignments ───────────────────────────────
    header = (
        "# Cirque RMM Agent - auto-configured by tracker server\n"
        "# Run as Administrator. Logs: C:\\CirqueRMM\\logs\\setup.log\n"
        f"$SiteToken          = '{site_token}'\n"
        f"$TrackerUrl         = '{tracker_url}'\n"
        f"$TrackerUrlFallback = '{tracker_url_fallback}'\n"
        f"$GatewayUrl         = '{gateway_url}'\n"
        f"$GatewayUrlFallback = '{gateway_url_fallback}'\n"
        "$Token       = ''\n"
        "$InstallDir  = 'C:\\CirqueRMM'\n"
        "$NssmPath    = 'C:\\Program Files\\NSSM\\nssm.exe'\n"
        "$AgentId     = ''\n"
        "$SkipDownload = $false\n\n"
    )
    ps1 = header + ps1.lstrip()

    # ── Scrub non-ASCII so PowerShell 5.1 never chokes on encoding ────────
    ps1 = ps1.replace('\u2192', '->').replace('\u2014', '--').replace('\u2013', '-')
    ps1 = _re.sub(r'[^\x00-\x7f]', '-', ps1)

    from flask import Response
    # No BOM — irm returns a plain string; BOM confuses the scriptblock parser
    return Response(ps1.encode('utf-8'),
                    mimetype='text/plain; charset=utf-8',
                    headers={'Content-Disposition': 'inline; filename="CirqueRMM-Install.ps1"'})


@bp.route('/download/deploy-silent.ps1')
def download_deploy_silent_ps1():
    """Serve a silent deployment wrapper script (no site-token auth needed — designed
    to be uploaded to GPO / Intune / PSRemoting as a SYSTEM-run startup script).

    Checks if the service already exists before doing anything.
    Creates a hidden scheduled task that runs the installer as SYSTEM so
    no console window is ever visible to the logged-in user.
    The task self-deletes after the installer completes.
    Auth: site token embedded in the URL (?t=TOKEN) same as site-install.ps1.
    """
    import hmac as _hmac
    t = request.args.get('t', '').strip()
    expected = _get_or_create_site_enrollment_token()
    if not t or not _hmac.compare_digest(t, expected):
        return 'Invalid or missing site token.', 403

    tracker_url  = RMM_TRACKER_URL.rstrip('/')
    installer_url = f"{tracker_url}/download/site-install.ps1?t={expected}"

    script = f"""# Cirque RMM Agent - Silent Mass Deployment Script
# Safe for GPO Startup Scripts, Intune Script Policies, or PSRemoting.
# Runs as SYSTEM - no console window is shown to users.
# If the agent is already installed this script exits immediately.

$ServiceName  = 'CirqueRMM'
$TaskName     = 'CirqueRMM-SilentInstall'
$InstallerUrl = '{installer_url}'

# Already installed? Nothing to do.
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {{
    Write-Host "CirqueRMM already installed - exiting."
    exit 0
}}

# Build an Argument string that downloads and runs the installer completely hidden
$psArgs = "-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -Command " +
    "`"& {{ [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " +
    "irm '$InstallerUrl' | iex }}`""

# Create a one-time scheduled task that runs as SYSTEM (guaranteed no window)
$action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs
$trigger   = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(10)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

# Start immediately rather than waiting for the trigger
Start-ScheduledTask -TaskName $TaskName

Write-Host "Cirque RMM silent install task created and started on $env:COMPUTERNAME."
Write-Host "The agent will enroll and start within 1-2 minutes."
Write-Host "Check C:\\CirqueRMM\\logs\\setup.log for progress."
"""

    from flask import Response
    return Response(script.encode('utf-8'),
                    mimetype='text/plain; charset=utf-8',
                    headers={'Content-Disposition': 'inline; filename="CirqueRMM-Deploy-Silent.ps1"'})


@bp.route('/download/agent-file/<path:filename>')
def download_agent_file(filename):
    """Serve individual agent files for the installer.

    Auth: either a logged-in browser session OR ?t=<site_enrollment_token>.
    This allows the PS1 one-liner (irm | iex) to download files without a session.
    """
    import os, posixpath, hmac as _hmac
    # Whitelist allowed files (prevent path traversal)
    allowed = {
        'agent_client.py', 'agent_launcher.py', 'tray.py',
        'requirements.txt', 'version.txt',
        'cirque_icon_ico.b64', 'cirque_icon_png.b64',
    }
    clean = posixpath.basename(filename)
    if clean not in allowed:
        return "File not available.", 404

    # Accept site token in ?t= (for PS1 one-liner) or a valid login session
    t = request.args.get('t', '').strip()
    if t:
        expected = _get_or_create_site_enrollment_token()
        if not _hmac.compare_digest(t, expected):
            return "Invalid token.", 403
    elif not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=request.url))

    path = os.path.join(current_app.root_path, 'rmm_agent', clean)
    if not os.path.exists(path):
        return f"{clean} not found on server.", 404
    return send_file(path, as_attachment=False, mimetype='text/plain')


@bp.route('/alerts/center')
@login_required
def alert_center():
    users = db.session.execute(text('SELECT id, username, full_name FROM "user" ORDER BY username')).mappings().fetchall()
    return render_template('alert_center.html', users=[dict(u) for u in users])


@bp.route('/api/alerts/rules', methods=['GET', 'POST'])
@login_required
def api_alert_rules():
    con = _alert_svc._get_db()
    try:
        if request.method == 'GET':
            cat = request.args.get('category')
            if cat:
                rows = con.execute("SELECT * FROM alert_rule WHERE category=? ORDER BY alert_type", (cat,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM alert_rule ORDER BY category, alert_type").fetchall()
            return jsonify(ok=True, rules=[dict(r) for r in rows])
        d = request.get_json(force=True)
        con.execute(
            """INSERT INTO alert_rule (category, alert_type, label, threshold_value, threshold_unit,
               enabled, auto_ticket, ticket_priority, assigned_to_user_id, email_notify,
               teams_notify, teams_webhook_url, cooldown_minutes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d.get('category', 'agent'), d.get('alert_type', ''), d.get('label', ''),
             d.get('threshold_value', 0), d.get('threshold_unit', ''),
             1 if d.get('enabled', True) else 0,
             1 if d.get('auto_ticket') else 0,
             d.get('ticket_priority', 'Normal'), d.get('assigned_to_user_id'),
             1 if d.get('email_notify', True) else 0,
             1 if d.get('teams_notify') else 0,
             d.get('teams_webhook_url', ''), d.get('cooldown_minutes', 60))
        )
        con.commit()
        return jsonify(ok=True)
    finally:
        con.close()


@bp.route('/api/alerts/rules/<int:rid>', methods=['PUT', 'DELETE'])
@login_required
def api_alert_rule(rid):
    con = _alert_svc._get_db()
    try:
        if request.method == 'DELETE':
            con.execute("DELETE FROM alert_rule WHERE id=?", (rid,))
            con.commit()
            return jsonify(ok=True)
        d = request.get_json(force=True)
        con.execute(
            """UPDATE alert_rule SET label=?, threshold_value=?, threshold_unit=?,
               enabled=?, auto_ticket=?, ticket_priority=?, assigned_to_user_id=?,
               email_notify=?, teams_notify=?, teams_webhook_url=?, cooldown_minutes=?
               WHERE id=?""",
            (d.get('label', ''), d.get('threshold_value', 0), d.get('threshold_unit', ''),
             1 if d.get('enabled', True) else 0,
             1 if d.get('auto_ticket') else 0,
             d.get('ticket_priority', 'Normal'), d.get('assigned_to_user_id'),
             1 if d.get('email_notify', True) else 0,
             1 if d.get('teams_notify') else 0,
             d.get('teams_webhook_url', ''), d.get('cooldown_minutes', 60), rid)
        )
        con.commit()
        return jsonify(ok=True)
    finally:
        con.close()


@bp.route('/api/alerts/rules/<int:rid>/toggle', methods=['POST'])
@login_required
def api_alert_rule_toggle(rid):
    con = _alert_svc._get_db()
    try:
        row = con.execute("SELECT enabled FROM alert_rule WHERE id=?", (rid,)).fetchone()
        if not row:
            return jsonify(ok=False, error='Not found'), 404
        new_state = 0 if row['enabled'] else 1
        con.execute("UPDATE alert_rule SET enabled=? WHERE id=?", (new_state, rid))
        con.commit()
        return jsonify(ok=True, enabled=bool(new_state))
    finally:
        con.close()


@bp.route('/api/alerts/log')
@login_required
def api_alert_log():
    con = _alert_svc._get_db()
    try:
        cat = request.args.get('category')
        limit = int(request.args.get('limit', 100))
        if cat:
            rows = con.execute(
                "SELECT * FROM alert_log WHERE category=? ORDER BY fired_at DESC LIMIT ?", (cat, limit)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM alert_log ORDER BY fired_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return jsonify(ok=True, log=[dict(r) for r in rows])
    finally:
        con.close()


@bp.route('/api/notifications/bell')
@login_required
def api_notifications_bell():
    con = _alert_svc._get_db()
    try:
        # Auto-trim: remove entries older than 30 days and keep table manageable
        con.execute("DELETE FROM notification_bell WHERE created_at < NOW() - INTERVAL '30 days'")
        con.commit()
        unread = con.execute("SELECT COUNT(*) FROM notification_bell WHERE read_flag=false").fetchone()[0]
        recent = con.execute("SELECT * FROM notification_bell ORDER BY created_at DESC LIMIT 20").fetchall()
        return jsonify(ok=True, unread=unread, items=[dict(r) for r in recent])
    finally:
        con.close()


@bp.route('/api/notifications/mark-read', methods=['POST'])
@login_required
def api_notifications_mark_read():
    con = _alert_svc._get_db()
    try:
        ids = (request.get_json(force=True) or {}).get('ids')
        if ids:
            con.execute(f"UPDATE notification_bell SET read_flag=true WHERE id IN ({','.join('?'*len(ids))})", ids)
        else:
            con.execute("UPDATE notification_bell SET read_flag=true")
        con.commit()
        return jsonify(ok=True)
    finally:
        con.close()


@bp.route('/api/reports/templates', methods=['GET'])
@login_required
def api_report_templates():
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, name, description, report_type, is_builtin, created_at FROM report_templates ORDER BY is_builtin DESC, name"
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/reports/runs', methods=['GET'])
@login_required
def api_report_runs_list():
    db_conn = get_db()
    rows = db_conn.execute(
        "SELECT id, name, report_type, status, row_count, file_csv, file_pdf, generated_by, generated_at, completed_at FROM report_runs ORDER BY id DESC LIMIT 100"
    ).fetchall()
    db_conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/reports/run', methods=['POST'])
@login_required
def api_report_run():
    data    = request.get_json()
    rtype   = data.get('report_type')
    name    = data.get('name') or rtype
    config  = data.get('config', {})
    tmpl_id = data.get('template_id')
    if not rtype:
        return jsonify({'error': 'report_type required'}), 400
    db_conn = get_db()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cur = db_conn.execute(
        "INSERT INTO report_runs (template_id, name, report_type, config, status, generated_by, generated_at) VALUES (?,?,?,?,?,?,?)",
        (tmpl_id, name, rtype, json.dumps(config), 'pending', current_user.username, now)
    )
    run_id = cur.lastrowid
    db_conn.commit(); db_conn.close()

    def _bg():
        _report_engine.run_report(run_id, tmpl_id, name, rtype, config, current_user.username)
    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({'ok': True, 'run_id': run_id})


@bp.route('/api/reports/runs/<int:run_id>', methods=['GET'])
@login_required
def api_report_run_status(run_id):
    db_conn = get_db()
    row = db_conn.execute("SELECT * FROM report_runs WHERE id=?", (run_id,)).fetchone()
    db_conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))


@bp.route('/api/reports/runs/<int:run_id>/data', methods=['GET'])
@login_required
def api_report_run_data(run_id):
    db_conn = get_db()
    row = db_conn.execute("SELECT * FROM report_runs WHERE id=?", (run_id,)).fetchone()
    db_conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if row['status'] != 'ready':
        return jsonify({'error': 'Report not ready yet', 'status': row['status']}), 202
    rtype   = row['report_type']
    config  = json.loads(row['config'] or '{}')
    fetcher = _report_engine.FETCHERS.get(rtype)
    if not fetcher:
        return jsonify({'error': 'Unknown report type'}), 400
    cols, rows = fetcher(config)
    return jsonify({'cols': cols, 'rows': rows, 'count': len(rows)})


@bp.route('/api/reports/download/<string:filename>')
@login_required
def api_report_download(filename):
    safe = os.path.basename(filename)
    path = os.path.join(_report_engine.REPORT_DIR, safe)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    mimetype = 'text/csv' if safe.endswith('.csv') else 'application/pdf' if safe.endswith('.pdf') else 'text/html'
    return send_file(path, as_attachment=True, download_name=safe, mimetype=mimetype)