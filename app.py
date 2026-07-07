# ── stdlib ────────────────────────────────────────────────────────────────
import os
import subprocess
import time as _time
import threading
import io
import base64
import csv
import json
import logging
import re
import uuid
import secrets
import hmac
import qrcode
import requests
import msal
from datetime import datetime, timedelta, timezone

# ── Force server timezone to MST (Mountain Standard Time, UTC-7) ──────────
os.environ['TZ'] = 'America/Denver'
_time.tzset()

# ── Third-party ───────────────────────────────────────────────────────────
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, session)
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import text

# ── Internal services ─────────────────────────────────────────────────────
from m365_service import M365Service
from api_system import require_api_key
from ssh_manager import get_ssh_manager
import alert_service as _alert_svc
import workflow_engine as _wf_engine
import ai_engine as _ai_engine
import report_engine as _report_engine

# ── Extensions (unbound objects) ──────────────────────────────────────────
from extensions import db, login_manager, mail, limiter, csrf

# ── Models ────────────────────────────────────────────────────────────────
from models import (
    now_mst, allowed_file,
    User, AzureIntegrationConfig, Employee, Asset, RemoteSession,
    SupportTicket, TicketNote, TicketActivity, AssetHistory,
    License, LicenseAssignment, Setting, SystemDescription,
    Policy, PolicySection, Risk, ControlRiskMapping,
    DashboardWidget, CustomReport, LicenseInfo,
    MonitoringProfile, MonitoringCheck, MonitoringAlert,
    MaintenanceWindow, ProxmoxZfsPool, ProxmoxBackupJob,
    ProfileCheck, AssetMonitoringProfile,
    AuditTrail, _log_audit,
)

# ── SOC2 models ───────────────────────────────────────────────────────────
from soc2_models import (
    SOC2Control, EvidenceSnapshot, M365User, IntuneDevice,
    DeviceSoftware, AdminRoleSnapshot, ComplianceReport, AuditLog,
    StrikeGraphEvidence,
)

# ── Shared utilities (decorators, email helpers) ──────────────────────────
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification,
    send_asset_assignment_email, send_warranty_expiry_alert,
    send_lifecycle_alert,
)

# ── Configure logging ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
#  Application factory
# ══════════════════════════════════════════════════════════════════════════
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app = Flask(__name__)

# ── Configuration (centralized in config.py; secrets required from env) ─────
from config import Config
app.config.from_object(Config)
app.request_class.max_form_memory_size = app.config['MAX_FORM_MEMORY_SIZE']
CSRF_ENABLED = app.config['WTF_CSRF_ENABLED']  # kill-switch: TRACKER_CSRF_ENABLED=0


def _valid_agent_key(key):
    return bool(key) and key == app.config.get('LINUX_AGENT_API_KEY', '')

# ── Bind extensions to this app ───────────────────────────────────────────
db.init_app(app)
login_manager.init_app(app)
mail.init_app(app)
limiter.init_app(app)
# init_app always (not gated) so csrf_token() template global + <meta> tag keep
# working even when enforcement is off; CSRF_ENABLED only toggles enforcement.
csrf.init_app(app)

from license_service import license_service
license_service.init_app(app, db)

# ── Static-asset cache-busting ─────────────────────────────────────────────
# Append ?v={{ asset_version }} to CSS/JS so a deploy never serves stale assets.
# Version = current git commit (changes every deploy); falls back to start time.
try:
    _ASSET_VERSION = subprocess.check_output(
        ['git', 'rev-parse', '--short', 'HEAD'],
        cwd=os.path.dirname(os.path.abspath(__file__)), text=True,
        stderr=subprocess.DEVNULL).strip() or 'dev'
except Exception:
    _ASSET_VERSION = str(int(_time.time()))

@app.context_processor
def inject_asset_version():
    return {'asset_version': _ASSET_VERSION}

@app.context_processor
def inject_workhours_access():
    """Expose the work-hours visibility check to templates (nav gating)."""
    from utils import workhours_can_view
    return {'workhours_can_view': workhours_can_view}

# ── Security headers ──────────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # Content-Security-Policy. 'unsafe-inline' is retained for script/style because the
    # templates rely heavily on inline scripts/styles; the policy still meaningfully locks
    # down object/base/frame-ancestors/form-action and restricts external origins to the
    # CDNs actually in use (jsDelivr + Google Fonts).
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        # Allow the xterm.js terminal's WebSocket to reach the RMM gateway origins
        # (LAN + public Cloudflare). Without these, CSP connect-src 'self' blocks the
        # shell WS ("violates connect-src 'self'") since the gateway is a different origin.
        f"connect-src 'self' {os.environ.get('RMM_GATEWAY_URL', 'wss://rmm.cirquetools.com')} {os.environ.get('RMM_GATEWAY_URL_LAN', 'wss://rmm.corp.cirque.com')}; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'self'; "
        "form-action 'self'"
    )
    return response

# Routes

@app.context_processor
def inject_impersonation_state():
    """Make impersonation info available in every template."""
    try:
        if current_user.is_authenticated:
            real_admin_id = session.get('impersonate_real_admin_id')
            if real_admin_id:
                real_admin = User.query.get(int(real_admin_id))
                if real_admin and real_admin.role == 'admin':
                    return dict(impersonating=True, real_admin=real_admin,
                                photo_url=_photo_url)
    except Exception:
        pass
    return dict(impersonating=False, real_admin=None, photo_url=_photo_url)


def _photo_url(photo_rel):
    """Build a cache-busted static URL for an employee photo.

    photo_rel may be stored as 'employee_photos/employee_1.jpg?v=123456'
    We split off the ?v= before passing to url_for so Flask doesn't encode it.
    """
    if not photo_rel:
        return None
    parts = photo_rel.split('?', 1)
    base = url_for('static', filename='uploads/' + parts[0])
    return base + ('?' + parts[1] if len(parts) > 1 else '')


# ── Register Blueprints ───────────────────────────────────────────────────
from blueprints import auth, assets, dashboard, employees, internal_audit, licenses, management_review, phishing, policy_acknowledgements, security_training, system_description, vendor_management
from blueprints import isms, monitoring, readiness, reports, rmm, settings, soc2
from blueprints import tickets, vulnerabilities, ai, misc
from blueprints import backup, quarantine, patch_mgmt, systems, incidents
from blueprints import context as context_bp
from blueprints import teams_bot as teams_bot_bp

app.register_blueprint(auth.bp)
app.register_blueprint(assets.bp)
app.register_blueprint(dashboard.bp)
app.register_blueprint(employees.bp)
app.register_blueprint(licenses.bp)
app.register_blueprint(monitoring.bp)
app.register_blueprint(reports.bp)
app.register_blueprint(rmm.bp)
app.register_blueprint(settings.bp)
app.register_blueprint(soc2.bp)
app.register_blueprint(isms.bp)
app.register_blueprint(readiness.bp)
app.register_blueprint(internal_audit.bp)
app.register_blueprint(vendor_management.bp)
app.register_blueprint(management_review.bp)
app.register_blueprint(phishing.bp)
app.register_blueprint(policy_acknowledgements.bp)
app.register_blueprint(security_training.bp)
app.register_blueprint(system_description.bp)
app.register_blueprint(tickets.bp)
app.register_blueprint(vulnerabilities.bp)
app.register_blueprint(ai.bp)
app.register_blueprint(misc.bp)
app.register_blueprint(backup.bp)
app.register_blueprint(quarantine.bp)
app.register_blueprint(systems.bp)
app.register_blueprint(patch_mgmt.bp)
app.register_blueprint(incidents.bp)
app.register_blueprint(context_bp.bp)
app.register_blueprint(teams_bot_bp.bp)

# ── CSRF exemptions for non-browser endpoints ──────────────────────────────
# Agents and external API consumers authenticate via agent token / API key, not
# a browser session cookie, so the CSRF token mechanism does not apply to them.
# Exempt by URL prefix/exact-rule so adding a new agent route under these paths
# is covered automatically (safer than per-route decorators that are easy to miss).
_CSRF_EXEMPT_PREFIXES = (
    '/api/linux-agent/',     # Linux agent (X-API-Key)
    '/api/rmm/agent/',       # RMM agent: command_result, heartbeat, version, file, remove
    '/api/rmm/enroll',       # RMM enrollment
    '/api/rmm/screenshot/',  # RMM screenshot upload
    '/rmm/agent/',           # agent launcher/repair/tray/version/file
    '/agent/',               # misc/monitoring Linux agent install + heartbeat
    '/api/teams/',           # Teams bot messaging endpoint (Bot Framework JWT auth)
)
_CSRF_EXEMPT_RULES = {
    '/api/rmm/<agent_id>/software',         # RMM software inventory upload
    '/api/rmm/rustdesk-sync/<agent_id>',    # RMM agent RustDesk ID sync (agent-token auth)
    '/api/rmm/telemetry',                   # Linux agent telemetry POST
    '/api/rmm/system-info',                 # Linux agent system-info POST
    '/api/rmm/backup-start/<agent_id>',     # RMM backup agent callback
    '/api/rmm/backup-complete/<int:job_id>',# RMM backup agent callback
    '/api/rmm/backup-job/<int:job_id>',     # RMM backup agent callback (PATCH)
    '/api/asset/<int:asset_id>/software',   # @require_api_key('agent')
    '/api/support-tickets',                 # @require_api_key('create_tickets')
}
_csrf_exempted = 0
for _rule in app.url_map.iter_rules():
    if _rule.rule.startswith(_CSRF_EXEMPT_PREFIXES) or _rule.rule in _CSRF_EXEMPT_RULES:
        _vf = app.view_functions.get(_rule.endpoint)
        if _vf is not None:
            csrf.exempt(_vf)
            _csrf_exempted += 1
logger.info('CSRF: enforcement=%s, exempted %d agent/API endpoints', CSRF_ENABLED, _csrf_exempted)

# ── Start background threads ───────────────────────────────────────────────
_sla_thread = threading.Thread(
    target=tickets._ticket_sla_check, args=(app,), daemon=True
)
_sla_thread.start()

# Eagle Eyes scheduled productivity reports (daily/weekly/monthly email rollups).
# Cross-worker safe via an atomic last_sent_at claim, so running per-worker is fine.
from blueprints import rmm as _rmm_module
_eagle_report_thread = threading.Thread(
    target=_rmm_module._eagle_report_scheduler, args=(app,), daemon=True
)
_eagle_report_thread.start()

# Daily asset EOL/warranty auto-ticket check. Leader-elected so only ONE worker runs it
# (its ticket dedup isn't atomic across workers waking together): the first worker to grab
# an exclusive flock keeps the fd open for its lifetime and runs the thread; others skip.
def _try_become_eol_leader():
    import fcntl
    try:
        fd = os.open('/tmp/tracker_eol_leader.lock', os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd  # held for process lifetime -> lock stays acquired
    except OSError:
        return None
_eol_leader_fd = _try_become_eol_leader()   # module-global so the fd isn't GC'd/closed
if _eol_leader_fd is not None:
    from blueprints import assets as _assets_module
    _eol_thread = threading.Thread(
        target=_assets_module._asset_eol_check, args=(app,), daemon=True
    )
    _eol_thread.start()
    logger.info('Asset EOL check: this worker is the leader; daily thread started')

# Event bus — the connective tissue. One dispatcher thread per worker; only the
# cross-process flock winner actually fans events out to workflow triggers.
import event_bus
event_bus.start_event_dispatcher(app)
