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
from extensions import db, login_manager, mail, limiter

# ── Models ────────────────────────────────────────────────────────────────
from models import (
    now_mst, allowed_file,
    User, AzureIntegrationConfig, Employee, Asset, RemoteSession,
    SupportTicket, TicketNote, TicketActivity, AssetHistory,
    License, LicenseAssignment, Setting, SystemDescription,
    Policy, PolicySection, Control, Risk, ControlRiskMapping,
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

# ── Security / session config ─────────────────────────────────────────────
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    raise RuntimeError('SECRET_KEY environment variable is not set. Set it in /etc/tracker/secrets.env')
app.config['SECRET_KEY'] = _secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://tracker_user:tracker_secure_2026@localhost/tracker'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'connect_args': {'options': '-c timezone=UTC'},
    'pool_size': 10,
    'max_overflow': 20,
    'pool_pre_ping': True,
    'pool_recycle': 1800,
}
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
app.config['REMEMBER_COOKIE_SECURE'] = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['UPLOAD_FOLDER'] = '/var/www/tracker/static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

# ── Email config ──────────────────────────────────────────────────────────
app.config['MAIL_SERVER'] = '10.15.0.4'
app.config['MAIL_PORT'] = 25
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = None
app.config['MAIL_PASSWORD'] = None
app.config['MAIL_DEFAULT_SENDER'] = 'assettracker@cirque.com'
app.config['SEND_EMPLOYEE_EMAILS'] = False

# ── Linux Agent key ───────────────────────────────────────────────────────
_linux_agent_key = os.environ.get('LINUX_AGENT_API_KEY')
if not _linux_agent_key:
    raise RuntimeError('LINUX_AGENT_API_KEY environment variable is not set. Set it in /etc/tracker/secrets.env')
app.config['LINUX_AGENT_API_KEY'] = _linux_agent_key

def _valid_agent_key(key):
    return bool(key) and key == app.config.get('LINUX_AGENT_API_KEY', '')

# ── Bind extensions to this app ───────────────────────────────────────────
db.init_app(app)
login_manager.init_app(app)
mail.init_app(app)
limiter.init_app(app)

from license_service import license_service
license_service.init_app(app, db)

# ── Security headers ──────────────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
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
from blueprints import auth, assets, dashboard, employees, licenses
from blueprints import monitoring, reports, rmm, settings, soc2
from blueprints import tickets, vulnerabilities, ai, misc
from blueprints import backup

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
app.register_blueprint(tickets.bp)
app.register_blueprint(vulnerabilities.bp)
app.register_blueprint(ai.bp)
app.register_blueprint(misc.bp)
app.register_blueprint(backup.bp)
