"""
blueprints/backup.py — Windows Backup feature.

UI routes:
  GET  /settings/backup                    — policy management page
  POST /api/backup/policy                  — create policy (JSON)
  PUT  /api/backup/policy/<id>             — update policy (JSON)
  DELETE /api/backup/policy/<id>           — delete policy

Agent routes (auth: ?agent_id=…&token=… query params):
  GET  /api/rmm/backup-policy/<agent_id>   — agent fetches its effective config
  POST /api/rmm/backup-start/<agent_id>    — agent signals job start → returns job_id
  PATCH /api/rmm/backup-job/<job_id>       — agent posts incremental progress
  POST /api/rmm/backup-complete/<job_id>   — agent posts final result

Admin / UI:
  GET  /api/rmm/backup-jobs/<agent_id>     — job history for asset tab
  POST /api/rmm/backup-trigger/<agent_id>  — queue manual trigger via rmm_commands
  POST /api/backup/assign                  — assign/update agent policy assignment
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from flask import (Blueprint, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import text

from extensions import db
from models import RmmBackupPolicy, RmmAgentBackupPolicy, RmmBackupJob, RmmBackupNas
from utils import admin_required, _dt_iso


def _make_fernet() -> Fernet:
    """Derive a stable Fernet key from SECRET_KEY so NAS passwords survive restarts."""
    secret = os.environ.get('SECRET_KEY', 'fallback-insecure').encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def _encrypt_nas_pw(plaintext: str) -> str:
    if not plaintext:
        return ''
    return _make_fernet().encrypt(plaintext.encode()).decode()


def _decrypt_nas_pw(ciphertext: str) -> str:
    if not ciphertext:
        return ''
    try:
        return _make_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return ''

bp = Blueprint('backup', __name__)


# ─── Auth helper (mirrors rmm.py _verify_agent_token) ────────────────────────

def _verify_token(agent_id: str, token: str) -> bool:
    """Validate agent_id + raw token against rmm_agent table."""
    if not agent_id or not token:
        return False
    h = hashlib.sha256(token.encode()).hexdigest()
    row = db.session.execute(
        text("SELECT id FROM rmm_agent WHERE agent_id = :aid AND agent_token_sha256 = :h AND enabled = true"),
        {'aid': agent_id, 'h': h}
    ).fetchone()
    return row is not None


def _auth_agent():
    """Return (agent_id, ok) from ?agent_id=&token= query params."""
    agent_id = request.args.get('agent_id', '').strip()
    token = request.args.get('token', '').strip()
    return agent_id, _verify_token(agent_id, token)


# ─── Helper: bytes → human-readable ──────────────────────────────────────────

def _fmt_bytes(b: int) -> str:
    if b is None:
        return '0 B'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'


# ─── Settings page ────────────────────────────────────────────────────────────

@bp.route('/settings/backup')
@login_required
@admin_required
def settings_backup():
    policies = RmmBackupPolicy.query.order_by(RmmBackupPolicy.name).all()
    for p in policies:
        p.agent_count = db.session.execute(
            text("SELECT COUNT(*) FROM rmm_agent_backup_policy WHERE policy_id = :pid"),
            {'pid': p.id}
        ).scalar()

    nas_list = RmmBackupNas.query.order_by(RmmBackupNas.name).all()

    agents = db.session.execute(text("""
        SELECT ra.agent_id,
               COALESCE(NULLIF(t.hostname, ''), NULLIF(a.name, ''), ra.agent_id) AS hostname,
               abp.policy_id,
               abp.enabled AS agent_enabled,
               p.name AS policy_name
        FROM rmm_agent ra
        LEFT JOIN rmm_telemetry t ON t.agent_id = ra.agent_id
        LEFT JOIN asset a ON a.id = ra.asset_id
        LEFT JOIN rmm_agent_backup_policy abp ON abp.agent_id = ra.agent_id
        LEFT JOIN rmm_backup_policy p ON p.id = abp.policy_id
        WHERE ra.enabled = true
        ORDER BY hostname
    """)).mappings().fetchall()

    return render_template('settings_backup.html',
                           policies=policies,
                           nas_list=nas_list,
                           agents=[dict(a) for a in agents])


# ─── Policy CRUD (JSON API) ───────────────────────────────────────────────────

@bp.route('/api/backup/policy', methods=['POST'])
@login_required
@admin_required
def api_backup_policy_create():
    d = request.get_json(force=True) or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify(ok=False, error='Name is required'), 400
    nas = (d.get('nas_unc_path') or '').strip()
    if not nas:
        return jsonify(ok=False, error='NAS UNC path is required'), 400

    p = RmmBackupPolicy(
        name=name,
        description=(d.get('description') or '').strip() or None,
        enabled=bool(d.get('enabled', True)),
        nas_unc_path=nas,
        nas_type=d.get('nas_type', 'smb'),
        include_paths=d.get('include_paths') or [],
        exclude_extensions=d.get('exclude_extensions') or [
            '.tmp', '.log', '.iso', '.vhd', '.vmdk', '.vhdx'],
        exclude_folders=d.get('exclude_folders') or [
            'node_modules', '.git', '$RECYCLE.BIN', 'Windows',
            'Program Files', 'Program Files (x86)'],
        max_file_size_mb=int(d.get('max_file_size_mb') or 500),
        full_backup_interval_days=int(d.get('full_backup_interval_days') or 7),
        retention_days=int(d.get('retention_days') or 30),
    )
    try:
        db.session.add(p)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True, id=p.id)


@bp.route('/api/backup/policy/<int:pid>', methods=['PUT'])
@login_required
@admin_required
def api_backup_policy_update(pid):
    p = RmmBackupPolicy.query.get_or_404(pid)
    d = request.get_json(force=True) or {}

    if 'name' in d:
        p.name = (d['name'] or '').strip()
    if 'description' in d:
        p.description = (d['description'] or '').strip() or None
    if 'enabled' in d:
        p.enabled = bool(d['enabled'])
    if 'nas_unc_path' in d:
        p.nas_unc_path = (d['nas_unc_path'] or '').strip()
    if 'nas_type' in d:
        p.nas_type = d['nas_type']
    if 'include_paths' in d:
        p.include_paths = d['include_paths']
    if 'exclude_extensions' in d:
        p.exclude_extensions = d['exclude_extensions']
    if 'exclude_folders' in d:
        p.exclude_folders = d['exclude_folders']
    if 'max_file_size_mb' in d:
        p.max_file_size_mb = int(d['max_file_size_mb'])
    if 'full_backup_interval_days' in d:
        p.full_backup_interval_days = int(d['full_backup_interval_days'])
    if 'retention_days' in d:
        p.retention_days = int(d['retention_days'])
    p.updated_at = datetime.utcnow()

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True)


@bp.route('/api/backup/policy/<int:pid>', methods=['DELETE'])
@login_required
@admin_required
def api_backup_policy_delete(pid):
    p = RmmBackupPolicy.query.get_or_404(pid)
    try:
        # Detach agents first (FK is SET NULL so they stay, just lose policy_id)
        db.session.execute(
            text("UPDATE rmm_agent_backup_policy SET policy_id = NULL WHERE policy_id = :pid"),
            {'pid': pid}
        )
        db.session.delete(p)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True)


# ─── NAS Appliance CRUD ───────────────────────────────────────────────────────

@bp.route('/api/backup/nas', methods=['POST'])
@login_required
@admin_required
def api_backup_nas_create():
    d = request.get_json(force=True) or {}
    name = (d.get('name') or '').strip()
    unc = (d.get('unc_path') or '').strip()
    if not name or not unc:
        return jsonify(ok=False, error='Name and UNC path are required'), 400
    pw = (d.get('nas_password') or '').strip()
    nas = RmmBackupNas(
        name=name,
        nas_type=(d.get('nas_type') or 'smb').strip(),
        unc_path=unc,
        notes=(d.get('notes') or '').strip() or None,
        enabled=bool(d.get('enabled', True)),
        auth_method=(d.get('auth_method') or 'smb_local').strip(),
        nas_username=(d.get('nas_username') or '').strip() or None,
        nas_password_enc=_encrypt_nas_pw(pw) if pw else None,
        sftp_port=int(d['sftp_port']) if d.get('sftp_port') else 22,
        sftp_remote_path=(d.get('sftp_remote_path') or '').strip() or None,
    )
    try:
        db.session.add(nas)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True, id=nas.id)


@bp.route('/api/backup/nas/<int:nid>', methods=['PUT'])
@login_required
@admin_required
def api_backup_nas_update(nid):
    nas = RmmBackupNas.query.get_or_404(nid)
    d = request.get_json(force=True) or {}
    if 'name' in d:
        nas.name = d['name'].strip()
    if 'unc_path' in d:
        nas.unc_path = d['unc_path'].strip()
    if 'nas_type' in d:
        nas.nas_type = d['nas_type'].strip()
    if 'notes' in d:
        nas.notes = (d['notes'] or '').strip() or None
    if 'enabled' in d:
        nas.enabled = bool(d['enabled'])
    if 'auth_method' in d:
        nas.auth_method = (d['auth_method'] or 'smb_local').strip()
    if 'nas_username' in d:
        nas.nas_username = (d['nas_username'] or '').strip() or None
    # Only update password if a new one was supplied
    pw = (d.get('nas_password') or '').strip()
    if pw:
        nas.nas_password_enc = _encrypt_nas_pw(pw)
    if 'sftp_port' in d:
        nas.sftp_port = int(d['sftp_port']) if d['sftp_port'] else 22
    if 'sftp_remote_path' in d:
        nas.sftp_remote_path = (d['sftp_remote_path'] or '').strip() or None
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True)


@bp.route('/api/backup/nas/<int:nid>', methods=['DELETE'])
@login_required
@admin_required
def api_backup_nas_delete(nid):
    nas = RmmBackupNas.query.get_or_404(nid)
    try:
        db.session.delete(nas)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True)


# ─── Agent assignment ─────────────────────────────────────────────────────────

@bp.route('/api/backup/assign', methods=['POST'])
@login_required
@admin_required
def api_backup_assign():
    d = request.get_json(force=True) or {}
    agent_id = (d.get('agent_id') or '').strip()
    policy_id = d.get('policy_id')  # None = unassign
    enabled = bool(d.get('enabled', True))
    extra = d.get('extra_paths') or []

    if not agent_id:
        return jsonify(ok=False, error='agent_id required'), 400

    existing = RmmAgentBackupPolicy.query.filter_by(agent_id=agent_id).first()
    if existing:
        existing.policy_id = policy_id
        existing.enabled = enabled
        existing.extra_paths = extra
        existing.updated_at = datetime.utcnow()
    else:
        existing = RmmAgentBackupPolicy(
            agent_id=agent_id,
            policy_id=policy_id,
            enabled=enabled,
            extra_paths=extra,
        )
        db.session.add(existing)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True)


# ─── Agent API: fetch config ──────────────────────────────────────────────────

@bp.route('/api/rmm/backup-policy/<agent_id>')
def api_rmm_backup_policy(agent_id):
    """Agent calls this to get its effective backup configuration."""
    _, ok = _auth_agent()
    if not ok:
        return jsonify(ok=False, error='Unauthorized'), 401

    assignment = RmmAgentBackupPolicy.query.filter_by(agent_id=agent_id).first()
    if not assignment or not assignment.enabled or not assignment.policy_id:
        return jsonify(ok=True, enabled=False, policy=None)

    p = RmmBackupPolicy.query.get(assignment.policy_id)
    if not p or not p.enabled:
        return jsonify(ok=True, enabled=False, policy=None)

    # Merge extra_paths into include_paths
    base_paths = p.include_paths or []
    extra_paths = assignment.extra_paths or []
    all_paths = base_paths + [ep for ep in extra_paths if ep not in base_paths]

    # Look up NAS appliance to include isolated credentials for the agent
    nas = RmmBackupNas.query.filter_by(unc_path=p.nas_unc_path).first()
    nas_creds = None
    if nas:
        nas_creds = {
            'auth_method': nas.auth_method,
            'username': nas.nas_username,
            'password': _decrypt_nas_pw(nas.nas_password_enc),
            'sftp_port': nas.sftp_port or 22,
            'sftp_remote_path': nas.sftp_remote_path,
        }

    return jsonify(ok=True, enabled=True, policy={
        'id': p.id,
        'name': p.name,
        'nas_unc_path': p.nas_unc_path,
        'nas_type': p.nas_type,
        'nas_creds': nas_creds,
        'include_paths': all_paths,
        'exclude_extensions': p.exclude_extensions or [],
        'exclude_folders': p.exclude_folders or [],
        'max_file_size_mb': p.max_file_size_mb,
        'full_backup_interval_days': p.full_backup_interval_days,
        'retention_days': p.retention_days,
    })


# ─── Agent API: report job start ─────────────────────────────────────────────

@bp.route('/api/rmm/backup-start/<agent_id>', methods=['POST'])
def api_rmm_backup_start(agent_id):
    """Agent calls this when a backup job begins. Returns job_id for progress updates."""
    _, ok = _auth_agent()
    if not ok:
        return jsonify(ok=False, error='Unauthorized'), 401

    d = request.get_json(force=True) or {}
    job = RmmBackupJob(
        agent_id=agent_id,
        job_type=d.get('job_type', 'full'),
        status='running',
        snapshot_path=d.get('snapshot_path'),
        triggered_by=d.get('triggered_by', 'scheduled'),
    )
    try:
        db.session.add(job)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True, job_id=job.id)


# ─── Agent API: incremental progress update ───────────────────────────────────

@bp.route('/api/rmm/backup-job/<int:job_id>', methods=['PATCH'])
def api_rmm_backup_job_update(job_id):
    """Agent posts incremental progress (files_copied, bytes, etc.)."""
    agent_id = request.args.get('agent_id', '').strip()
    token = request.args.get('token', '').strip()
    if not _verify_token(agent_id, token):
        return jsonify(ok=False, error='Unauthorized'), 401

    job = RmmBackupJob.query.get_or_404(job_id)
    if job.agent_id != agent_id:
        return jsonify(ok=False, error='Forbidden'), 403

    d = request.get_json(force=True) or {}
    if 'files_copied' in d:
        job.files_copied = int(d['files_copied'])
    if 'files_skipped' in d:
        job.files_skipped = int(d['files_skipped'])
    if 'files_failed' in d:
        job.files_failed = int(d['files_failed'])
    if 'bytes_transferred' in d:
        job.bytes_transferred = int(d['bytes_transferred'])
    if 'snapshot_path' in d:
        job.snapshot_path = d['snapshot_path']

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True)


# ─── Agent API: job complete ──────────────────────────────────────────────────

@bp.route('/api/rmm/backup-complete/<int:job_id>', methods=['POST'])
def api_rmm_backup_complete(job_id):
    """Agent posts final result when backup finishes (success, partial, or failed)."""
    agent_id = request.args.get('agent_id', '').strip()
    token = request.args.get('token', '').strip()
    if not _verify_token(agent_id, token):
        return jsonify(ok=False, error='Unauthorized'), 401

    job = RmmBackupJob.query.get_or_404(job_id)
    if job.agent_id != agent_id:
        return jsonify(ok=False, error='Forbidden'), 403

    d = request.get_json(force=True) or {}
    job.status = d.get('status', 'success')
    job.completed_at = datetime.utcnow()
    job.files_copied = int(d.get('files_copied', job.files_copied))
    job.files_skipped = int(d.get('files_skipped', job.files_skipped))
    job.files_failed = int(d.get('files_failed', job.files_failed))
    job.bytes_transferred = int(d.get('bytes_transferred', job.bytes_transferred))
    if 'snapshot_path' in d:
        job.snapshot_path = d['snapshot_path']
    errors = d.get('errors')
    if errors is not None:
        job.errors_json = json.dumps(errors) if not isinstance(errors, str) else errors

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True)


# ─── UI API: job history for asset tab ───────────────────────────────────────

@bp.route('/api/rmm/backup-jobs/<agent_id>')
@login_required
def api_rmm_backup_jobs(agent_id):
    """Return the last 50 backup jobs for an agent (for the asset Backup tab)."""
    rows = db.session.execute(text("""
        SELECT id, job_type, status, started_at, completed_at,
               files_copied, files_skipped, files_failed,
               bytes_transferred, snapshot_path, triggered_by
        FROM rmm_backup_job
        WHERE agent_id = :aid
        ORDER BY started_at DESC
        LIMIT 50
    """), {'aid': agent_id}).mappings().fetchall()

    jobs = []
    for r in rows:
        duration_s = None
        if r['completed_at'] and r['started_at']:
            sa = r['started_at']
            ca = r['completed_at']
            # Both may be naive UTC from psycopg2
            if getattr(sa, 'tzinfo', None) is None:
                sa = sa.replace(tzinfo=timezone.utc)
            if getattr(ca, 'tzinfo', None) is None:
                ca = ca.replace(tzinfo=timezone.utc)
            duration_s = int((ca - sa).total_seconds())

        jobs.append({
            'id': r['id'],
            'job_type': r['job_type'],
            'status': r['status'],
            'started_at': _dt_iso(r['started_at']),
            'completed_at': _dt_iso(r['completed_at']),
            'duration_s': duration_s,
            'files_copied': r['files_copied'],
            'files_skipped': r['files_skipped'],
            'files_failed': r['files_failed'],
            'bytes_transferred': r['bytes_transferred'],
            'bytes_human': _fmt_bytes(r['bytes_transferred'] or 0),
            'snapshot_path': r['snapshot_path'],
            'triggered_by': r['triggered_by'],
        })

    # Also return the assignment info for the asset tab UI
    assignment = db.session.execute(text("""
        SELECT abp.enabled, abp.policy_id, p.name AS policy_name,
               p.nas_unc_path, p.full_backup_interval_days, p.retention_days
        FROM rmm_agent_backup_policy abp
        LEFT JOIN rmm_backup_policy p ON p.id = abp.policy_id
        WHERE abp.agent_id = :aid
    """), {'aid': agent_id}).mappings().fetchone()

    return jsonify(ok=True, jobs=jobs, assignment=dict(assignment) if assignment else None)


# ─── UI: manual trigger via gateway WebSocket ────────────────────────────────

@bp.route('/api/rmm/backup-trigger/<agent_id>', methods=['POST'])
@login_required
@admin_required
def api_rmm_backup_trigger(agent_id):
    """Push a backup_run message to the agent through the RMM gateway WebSocket."""
    import os as _os, urllib.request as _req, urllib.error as _err
    d = request.get_json(force=True) or {}
    job_type = d.get('job_type', 'full')
    if job_type not in ('full', 'incremental'):
        return jsonify(ok=False, error='job_type must be full or incremental'), 400

    gw = _os.environ.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')
    payload = json.dumps({
        'type': 'backup_run',
        'job_type': job_type,
        'triggered_by': 'manual',
    }).encode()
    try:
        req = _req.Request(
            f"{gw}/send-msg/{agent_id}",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with _req.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if not result.get('ok'):
            return jsonify(ok=False, error=result.get('error', 'Gateway error')), 502
    except _err.HTTPError as e:
        try:
            msg = json.loads(e.read()).get('error') or str(e)
        except Exception:
            msg = str(e)
        return jsonify(ok=False, error=msg), 502
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 502

    return jsonify(ok=True, queued=job_type)
