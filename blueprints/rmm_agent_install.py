"""Agent installer/download + enrollment routes for the RMM blueprint.
Split out of blueprints/rmm.py; registers on the same 'rmm' blueprint.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import subprocess
import threading
import time as _time
from datetime import datetime, timedelta, timezone
import requests
from werkzeug.utils import secure_filename

from flask import (Blueprint, abort, current_app, flash, g, jsonify,
                   make_response, redirect, render_template, request,
                   send_file, session, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_, text

from extensions import db, limiter
from models import (
    AuditTrail, Asset, AssetHistory, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
)
from soc2_models import SOC2Control, EvidenceSnapshot
import logging
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
    RMM_GATEWAY_INTERNAL, RMM_GATEWAY_PUBLIC, RMM_TRACKER_URL,
    _valid_agent_key, _dt_iso, _get_or_create_site_enrollment_token,
    _ensure_rmm_script_library_table,
)
logger = logging.getLogger(__name__)


from blueprints.rmm import bp, _verify_agent_token, _agent_payload_dir, _agent_file
from api_system import require_api_key


@bp.route('/download/agent-installer')
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
def download_agent_ps1():
    """Serve the raw PowerShell installer script directly."""
    import os
    path = os.path.join(current_app.root_path, 'rmm_agent', 'install_agent.ps1')
    if not os.path.exists(path):
        return 'Script not found on server.', 404
    return send_file(path, as_attachment=True, download_name='CirqueRMM-Install.ps1',
                     mimetype='application/octet-stream')


@bp.route('/download/agent-bat')
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
@admin_required
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


def _get_or_create_site_enrollment_token() -> str:
    """Return the site-wide RMM enrollment token, creating it if it doesn't exist."""
    import secrets as _sec
    setting = Setting.query.filter_by(key='rmm_site_enrollment_token').first()
    if not setting or not setting.value:
        if not setting:
            setting = Setting(key='rmm_site_enrollment_token')
            db.session.add(setting)
        setting.value = 'site_' + _sec.token_hex(32)
        db.session.commit()
    return setting.value


@bp.route('/api/rmm/enroll', methods=['POST'])
def rmm_enroll():
    """Self-registration endpoint for new agents.

    The installer PS1 (embedded in the MSI) sends the site-wide enrollment token
    here on first install.  The server creates (or finds) an asset and an rmm_agent
    row, then returns a fresh per-device token that the PS1 writes to agent.conf.

    Request JSON:
        site_token  – site-wide enrollment token (from Settings → RMM)
        hostname    – the device's computer name (becomes asset name)
        agent_id    – optional override; defaults to hostname

    Response JSON:
        token       – per-device credential for ongoing agent auth
        agent_id    – the agent ID the token is bound to
        asset_id    – tracker asset ID
    """
    import hashlib as _hl, secrets as _sec
    data = request.get_json(silent=True) or {}
    site_token = data.get('site_token', '').strip()
    hostname   = data.get('hostname', '').strip()
    agent_id   = (data.get('agent_id') or hostname).strip()

    if not site_token or not hostname:
        return jsonify({'ok': False, 'error': 'site_token and hostname required'}), 400

    # Validate site token
    expected = _get_or_create_site_enrollment_token()
    if not hmac.compare_digest(site_token, expected):
        return jsonify({'ok': False, 'error': 'Invalid enrollment token'}), 403

    if not agent_id:
        return jsonify({'ok': False, 'error': 'hostname required'}), 400

    try:
        now = datetime.utcnow().isoformat()

        # Find or create an asset for this device.
        # Match by hostname FIRST — serials like "TobefilledbyO.E.M." are identical
        # across many machines and cannot be used as a reliable unique key.
        _GARBAGE_SERIALS = {
            'tobefilled', 'tobefilledbyoem', 'tobefillbyoem',
            'default string', 'system serial number',
            'not specified', 'none', 'n/a', 'na', 'o.e.m.', '',
        }
        def _is_garbage_serial(s: str) -> bool:
            return s.lower().replace(' ', '').replace('.', '').replace('-', '') in _GARBAGE_SERIALS

        asset = Asset.query.filter(Asset.name.ilike(hostname)).first()
        if not asset and not _is_garbage_serial(agent_id):
            # Only fall back to serial match when the serial is a real unique value
            asset = Asset.query.filter(Asset.serial_number == agent_id).first()
        if not asset:
            asset = Asset(
                asset_tag=f'RMM-{agent_id[:8].upper()}',
                name=hostname,
                category='Workstation',
                device_type='Windows Workstation',
                status='In Use',
            )
            db.session.add(asset)
            db.session.flush()  # get asset.id

        # Generate a fresh per-device token
        raw_token = 'agent_' + _sec.token_hex(32)
        token_hash = _hl.sha256(raw_token.encode()).hexdigest()

        existing = db.session.execute(
            text("SELECT id, asset_id FROM rmm_agent WHERE agent_id = :aid"),
            {'aid': agent_id}
        ).fetchone()
        if existing:
            db.session.execute(
                text("""UPDATE rmm_agent
                        SET agent_token_sha256 = :hash, asset_id = :asid,
                            last_seen_at = :now
                        WHERE agent_id = :aid"""),
                {'hash': token_hash, 'asid': asset.id, 'now': now, 'aid': agent_id}
            )
        else:
            db.session.execute(
                text("""INSERT INTO rmm_agent
                        (agent_id, asset_id, agent_token_sha256, enabled, created_at, last_seen_at)
                        VALUES (:aid, :asid, :hash, TRUE, :now, :now)"""),
                {'aid': agent_id, 'asid': asset.id, 'hash': token_hash, 'now': now}
            )

        # Rename reconciliation: a renamed device re-enrolls under a NEW agent_id
        # (agent_id defaults to hostname). Other agent rows may still point at the
        # asset this device USED to be (e.g. KEN-DELL's agent left pointing at the
        # old asset #125 after the box became Ken-Lenovo / asset #966). When this
        # enrollment resolved a hostname match to `asset.id`, re-point any OTHER
        # agent rows that (a) reference this same asset OR (b) carry the device's
        # old name, but are NOT this agent_id, and disable them so the live agent
        # is the single source of truth. Guard against regressing first-enrollment:
        # only touch rows that are genuinely stale duplicates, never this agent.
        # SAFETY: only ever disable a STALE agent (not seen in 7+ days, or never).
        # This fleet has pre-existing tangles where two genuinely-distinct live devices
        # share one asset_id — without this guard the asset_id arm would disable a live
        # agent. A live agent (recent last_seen_at) is never touched; only the dead
        # old-name row left behind by a rename gets retired.
        # `now` is an isoformat string (for the timestamp inserts above); compute the
        # 7-day cutoff from a real datetime, not the string, then pass it as isoformat
        # so Postgres compares it against the last_seen_at timestamp column.
        stale_cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        stale_rows = db.session.execute(
            text("""SELECT id, agent_id, asset_id FROM rmm_agent
                    WHERE agent_id != :aid
                      AND enabled = TRUE
                      AND (last_seen_at IS NULL OR last_seen_at < :stale_cutoff)
                      AND (asset_id = :asid OR LOWER(agent_id) = LOWER(:host))"""),
            {'aid': agent_id, 'asid': asset.id, 'host': hostname, 'stale_cutoff': stale_cutoff}
        ).fetchall()
        for row in stale_rows:
            db.session.execute(
                text("""UPDATE rmm_agent
                        SET enabled = FALSE, asset_id = :asid
                        WHERE id = :rid"""),
                {'asid': asset.id, 'rid': row.id}
            )
            logger.info(
                "RMM rename reconcile: disabled stale agent_id=%s (was asset_id=%s) "
                "in favor of agent_id=%s -> asset_id=%s",
                row.agent_id, row.asset_id, agent_id, asset.id
            )

        db.session.commit()

        logger.info(f"RMM self-enrollment: agent_id={agent_id} hostname={hostname} asset_id={asset.id}")
        return jsonify({'ok': True, 'token': raw_token, 'agent_id': agent_id, 'asset_id': asset.id})

    except Exception as e:
        logger.error(f"RMM enroll error: {e}")
        return jsonify({'ok': False, 'error': 'Server error during enrollment'}), 500


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
        'nssm.exe',  # served locally so installs don't depend on nssm.cc (often 503)
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
    _mime = 'application/octet-stream' if clean.lower().endswith('.exe') else 'text/plain'
    return send_file(path, as_attachment=False, mimetype=_mime)


@bp.route('/api/rmm/agent/<agent_id>/remove', methods=['POST'])
@login_required
def rmm_remove_agent(agent_id):
    """Remove an RMM agent and all associated data. Admin only."""
    if current_user.role != 'admin':
        return jsonify(ok=False, error='Admin required'), 403
    try:
        child_tables = [
            'alert_log', 'cve_patch_job', 'device_vulnerability',
            'eagle_eyes_exclusions', 'linux_agent_heartbeat',
            'rmm_agent_flags', 'rmm_availability', 'rmm_commands',
            'rmm_connect_token', 'rmm_eagle_alert_log', 'rmm_eagle_alert_rule',
            'rmm_eagle_config', 'rmm_eagle_current', 'rmm_eagle_event',
            'rmm_eagle_report_schedule', 'rmm_metrics_history', 'rmm_patch',
            'rmm_patch_job', 'rmm_pending_update', 'rmm_screenshot',
            'rmm_session_events', 'rmm_software', 'rmm_telemetry',
        ]
        for tbl in child_tables:
            db.session.execute(text(f"DELETE FROM {tbl} WHERE agent_id = :aid"), {'aid': agent_id})
        db.session.execute(text("DELETE FROM rmm_agent WHERE agent_id = :aid"), {'aid': agent_id})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500


@bp.route('/rmm/agent/launcher')
def rmm_agent_launcher():
    """Serve agent_launcher.py (self-healing wrapper). Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    launcher_path = os.path.join(current_app.root_path, 'rmm_agent', 'agent_launcher.py')
    if not os.path.exists(launcher_path):
        return jsonify({'error': 'launcher not found'}), 404
    return send_file(launcher_path, mimetype='text/x-python', as_attachment=False)


@bp.route('/rmm/agent/repair')
def rmm_agent_repair():
    """Serve agent_repair.ps1. Authenticated by agent token."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    repair_path = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent', 'agent_repair.ps1')
    return send_file(repair_path, mimetype='text/plain', as_attachment=False)


@bp.route('/rmm/agent/tray')
def rmm_agent_tray():
    """Serve tray.py to authenticated agents.

    Canary-gated to match /rmm/agent/version + /rmm/agent/file: agents listed in
    setting['rmm_agent_canary'] get the staged canary tray (rmm_agent/canary/tray.py,
    the 2.9.8 Install-software picker); everyone else gets the fleet default
    (rmm_agent/tray.py, 2.9.7). Uses the same _agent_payload_dir resolver so the
    served file matches the hash announced by /rmm/agent/tray-sha for that agent.
    """
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    tray_path = _agent_file(agent_id, 'tray.py')
    if not os.path.isfile(tray_path):
        return jsonify({'error': 'tray.py not found on server'}), 404
    return send_file(tray_path, mimetype='text/x-python', as_attachment=False)


@bp.route('/rmm/agent/tray-sha')
def rmm_agent_tray_sha():
    """Return SHA-256 of tray.py so agents can detect updates without downloading the full file.

    Canary-gated identically to /rmm/agent/tray (same _agent_payload_dir resolver),
    so the hash always matches the file that endpoint would serve for this agent.
    """
    import hashlib as _hl
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    tray_path = _agent_file(agent_id, 'tray.py')
    if not os.path.isfile(tray_path):
        return jsonify({'error': 'tray.py not found'}), 404
    with open(tray_path, 'rb') as f:
        sha = _hl.sha256(f.read()).hexdigest()
    return jsonify({'sha256': sha})


@bp.route('/rmm/agent/fixes')
@require_api_key('create_tickets')
def rmm_agent_fixes():
    """One-click fix library for the systray 'Request a fix' picker.

    Authenticated by the same tray API key the systray already uses to submit tickets
    (Authorization: Bearer <tray_api_key>, scope create_tickets) — NOT the per-agent
    token, which the tray process does not hold. Returns vetted, active fixes only
    (id/name/description); the SYSTEM scripts stay server-side — the agent only ever
    sends back a fix_id, which the apply_fix action re-validates before running.
    """
    rows = db.session.execute(text(
        "SELECT id, name, description FROM rmm_script_library "
        "WHERE is_fix=true AND is_active=true ORDER BY name")).fetchall()
    return jsonify({'fixes': [{'id': r[0], 'name': r[1], 'description': r[2] or ''} for r in rows]})


def _serve_agent_sig(agent_id, token, filename):
    """Return the detached RSA signature of the canary-aware served agent payload file, so
    the agent can verify authenticity before swapping. Matches the same _agent_file the
    /file + /tray endpoints serve, so the signature always covers the bytes that agent gets."""
    if not agent_id or not token or not _verify_agent_token(agent_id, token):
        return jsonify({'error': 'Unauthorized'}), 401
    path = _agent_file(agent_id, filename)
    if not os.path.isfile(path):
        return jsonify({'error': f'{filename} not found'}), 404
    try:
        import agent_update_signing
        if not agent_update_signing.signing_available():
            return jsonify({'error': 'signing not configured'}), 503
        return jsonify({'sig': agent_update_signing.sign_file(path),
                        'alg': 'RSA-3072-PKCS1v15-SHA256'})
    except Exception as e:
        current_app.logger.warning('agent payload signing failed for %s: %s', filename, e)
        return jsonify({'error': 'signing failed'}), 500


@bp.route('/rmm/agent/file-sig')
def rmm_agent_file_sig():
    """Detached signature of the served agent_client.py (self-update authenticity check)."""
    return _serve_agent_sig(request.args.get('agent_id', ''), request.args.get('token', ''),
                            'agent_client.py')


@bp.route('/rmm/agent/tray-sig')
def rmm_agent_tray_sig():
    """Detached signature of the served tray.py (tray-update authenticity check)."""
    return _serve_agent_sig(request.args.get('agent_id', ''), request.args.get('token', ''),
                            'tray.py')


@bp.route('/rmm/agent/tray-install')
def rmm_agent_tray_install():
    """Serve tray_install.py — authenticated by agent token or browser session."""
    agent_id = request.args.get('agent_id', '')
    token    = request.args.get('token', '')
    # Accept either a valid agent token OR an active browser session
    if not (current_user.is_authenticated or
            (agent_id and token and _verify_agent_token(agent_id, token))):
        return jsonify({'error': 'Unauthorized'}), 401
    path = os.path.join(os.path.dirname(__file__), '..', 'rmm_agent', 'tray_install.py')
    if not os.path.isfile(path):
        return jsonify({'error': 'tray_install.py not found'}), 404
    return send_file(path, mimetype='text/x-python',
                     as_attachment=True, download_name='tray_install.py')


