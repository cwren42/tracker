import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone

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
    RMM_GATEWAY_INTERNAL,
)
logger = logging.getLogger(__name__)


bp = Blueprint('vulnerabilities', __name__)


# ─────────────────────────────────────────────────────────────────────────────
# VULNERABILITY DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────


# ════════════════════════════════════════════════════════════════════════════════
# WORKFLOW ROUTES
# ════════════════════════════════════════════════════════════════════════════════



@bp.route('/vulnerabilities')
@login_required
def vulnerability_dashboard():
    con = _alert_svc._get_db()
    try:
        counts = {s: 0 for s in ('Critical', 'High', 'Medium', 'Low')}
        for row in con.execute("""
            SELECT vc.severity, COUNT(DISTINCT vc.cve_id) as c
            FROM vulnerability_cache vc
            INNER JOIN device_vulnerability dv ON dv.cve_id = vc.cve_id AND dv.status='Open'
            GROUP BY vc.severity
        """).fetchall():
            sev = row['severity']
            if sev in counts:
                counts[sev] = row['c']
        last_sync_raw = con.execute("SELECT MAX(synced_at) FROM vulnerability_cache").fetchone()[0]
        device_count = con.execute("SELECT COUNT(DISTINCT asset_id) FROM device_vulnerability WHERE status='Open'").fetchone()[0]
        open_count   = con.execute("SELECT COUNT(*) FROM device_vulnerability WHERE status='Open'").fetchone()[0]
    finally:
        con.close()
    last_sync = None
    if last_sync_raw:
        try:
            _MST = timezone(timedelta(hours=-7))
            if isinstance(last_sync_raw, datetime):
                _dt = last_sync_raw if last_sync_raw.tzinfo else last_sync_raw.replace(tzinfo=timezone.utc)
            else:
                _dt = datetime.fromisoformat(str(last_sync_raw).replace('Z', '+00:00'))
                if _dt.tzinfo is None:
                    _dt = _dt.replace(tzinfo=timezone.utc)
            last_sync = _dt.astimezone(_MST).strftime('%Y-%m-%d %H:%M') + ' MST'
        except Exception:
            last_sync = str(last_sync_raw)[:16]
    return render_template('vulnerability_dashboard.html',
                           counts=counts, last_sync=last_sync,
                           device_count=device_count, open_count=open_count)


@bp.route('/api/vulnerabilities/sync', methods=['POST'])
@login_required
def api_vuln_sync():
    _app = app
    def _bg():
        with _app.app_context():
            vc, dc, err = _alert_svc.sync_defender_vulnerabilities()
            if err:
                logger.error(f'Background Defender sync error: {err}')
            else:
                logger.info(f'Background Defender sync complete: {vc} CVEs, {dc} device exposures')
    threading.Thread(target=_bg, daemon=True, name='defender-sync').start()
    return jsonify(ok=True, message='Sync started')


@bp.route('/api/vulnerabilities/stats')
@login_required
def api_vuln_stats():
    con = _alert_svc._get_db()
    try:
        counts = {}
        for row in con.execute("""
            SELECT vc.severity, COUNT(DISTINCT vc.cve_id) as c
            FROM vulnerability_cache vc
            INNER JOIN device_vulnerability dv ON dv.cve_id = vc.cve_id AND dv.status='Open'
            GROUP BY vc.severity
        """).fetchall():
            counts[row['severity']] = row['c']
        devices = con.execute("SELECT COUNT(DISTINCT asset_id) FROM device_vulnerability WHERE status='Open'").fetchone()[0]
        open_exp = con.execute("SELECT COUNT(*) FROM device_vulnerability WHERE status='Open'").fetchone()[0]
        last_sync_raw = con.execute("SELECT MAX(synced_at) FROM vulnerability_cache").fetchone()[0]
        last_sync_mst = None
        if last_sync_raw:
            try:
                _MST = timezone(timedelta(hours=-7))
                if isinstance(last_sync_raw, datetime):
                    _dt = last_sync_raw if last_sync_raw.tzinfo else last_sync_raw.replace(tzinfo=timezone.utc)
                else:
                    _dt = datetime.fromisoformat(str(last_sync_raw).replace('Z', '+00:00'))
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=timezone.utc)
                last_sync_mst = _dt.astimezone(_MST).strftime('%Y-%m-%d %H:%M') + ' MST'
            except Exception:
                last_sync_mst = str(last_sync_raw)[:16]
        return jsonify(Critical=counts.get('Critical', 0), High=counts.get('High', 0),
                       Medium=counts.get('Medium', 0), Low=counts.get('Low', 0),
                       devices=devices, open_exposures=open_exp, last_sync=last_sync_mst)
    finally:
        con.close()


@bp.route('/api/vulnerabilities')
@login_required
def api_vulnerabilities():
    con = _alert_svc._get_db()
    try:
        sev   = request.args.get('severity')
        limit = int(request.args.get('limit', 500))
        if sev:
            rows = con.execute(
                """SELECT vc.*, dc.device_count
                   FROM vulnerability_cache vc
                   JOIN (SELECT cve_id, COUNT(DISTINCT asset_id) AS device_count
                         FROM device_vulnerability WHERE status='Open' GROUP BY cve_id) dc
                     ON dc.cve_id = vc.cve_id
                   WHERE vc.severity=?
                   ORDER BY vc.cvss DESC LIMIT ?""",
                (sev, limit)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT vc.*, dc.device_count
                   FROM vulnerability_cache vc
                   JOIN (SELECT cve_id, COUNT(DISTINCT asset_id) AS device_count
                         FROM device_vulnerability WHERE status='Open' GROUP BY cve_id) dc
                     ON dc.cve_id = vc.cve_id
                   ORDER BY CASE vc.severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                             WHEN 'Medium' THEN 3 ELSE 4 END, vc.cvss DESC LIMIT ?""",
                (limit,)
            ).fetchall()
        return jsonify(ok=True, vulnerabilities=[dict(r) for r in rows])
    finally:
        con.close()


@bp.route('/api/vulnerabilities/devices')
@login_required
def api_vuln_devices():
    con = _alert_svc._get_db()
    try:
        cve_id   = request.args.get('cve_id')
        asset_id = request.args.get('asset_id')
        if cve_id:
            rows = con.execute(
                """SELECT dv.*, a.name as asset_name,
                          COALESCE(a.hostname, a.name) as display_name
                   FROM device_vulnerability dv
                   LEFT JOIN asset a ON a.id = dv.asset_id
                   WHERE dv.cve_id=? ORDER BY dv.severity""",
                (cve_id,)
            ).fetchall()
        elif asset_id:
            rows = con.execute(
                """SELECT dv.*, vc.name as vuln_name, vc.description
                   FROM device_vulnerability dv
                   LEFT JOIN vulnerability_cache vc ON vc.cve_id = dv.cve_id
                   WHERE dv.asset_id=? ORDER BY CASE dv.severity
                   WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END""",
                (asset_id,)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT dv.*, a.name as asset_name, a.hostname
                   FROM device_vulnerability dv
                   LEFT JOIN asset a ON a.id = dv.asset_id
                   WHERE dv.status='Open'
                   ORDER BY CASE dv.severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                            WHEN 'Medium' THEN 3 ELSE 4 END
                   LIMIT 500"""
            ).fetchall()
        return jsonify(ok=True, devices=[dict(r) for r in rows])
    finally:
        con.close()


@bp.route('/api/vulnerabilities/<cve_id>/status', methods=['PUT'])
@login_required
def api_vuln_status(cve_id):
    d        = request.get_json(force=True)
    con      = _alert_svc._get_db()
    username = current_user.username if current_user.is_authenticated else 'system'
    now_str  = now_mst().strftime('%Y-%m-%d %H:%M')
    try:
        asset_id = d.get('asset_id')
        status   = d.get('status', 'Open')
        note     = d.get('remediation_note', '')
        plan     = d.get('plan_date')
        if asset_id:
            con.execute(
                """UPDATE device_vulnerability
                   SET status=?, remediation_note=?, plan_date=?, updated_at=?, updated_by=?
                   WHERE cve_id=? AND asset_id=?""",
                (status, note, plan, now_str, username, cve_id, asset_id)
            )
            con.commit()
        else:
            con.execute(
                """UPDATE device_vulnerability
                   SET status=?, remediation_note=?, plan_date=?, updated_at=?, updated_by=?
                   WHERE cve_id=?""",
                (status, note, plan, now_str, username, cve_id)
            )
            con.commit()
        return jsonify(ok=True)
    finally:
        con.close()


@bp.route('/api/vulnerabilities/<cve_id>/deploy', methods=['POST'])
@login_required
def api_vuln_deploy(cve_id):
    data     = request.get_json(force=True) or {}
    asset_id = data.get('asset_id')
    username = current_user.username if current_user.is_authenticated else 'system'
    con      = _alert_svc._get_db()
    now_str  = now_mst().strftime('%Y-%m-%d %H:%M')
    try:
        if asset_id:
            rows = con.execute(
                """SELECT dv.asset_id, ra.agent_id
                   FROM device_vulnerability dv
                   JOIN rmm_agent ra ON ra.asset_id = dv.asset_id AND ra.enabled = true
                   WHERE dv.cve_id = ? AND dv.asset_id = ? LIMIT 1""",
                (cve_id, asset_id)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT DISTINCT dv.asset_id, ra.agent_id
                   FROM device_vulnerability dv
                   JOIN rmm_agent ra ON ra.asset_id = dv.asset_id AND ra.enabled = true
                   WHERE dv.cve_id = ? AND dv.status = 'Open'""",
                (cve_id,)
            ).fetchall()
    finally:
        con.close()
    if not rows:
        return jsonify(ok=False, error='No connected agents found for this CVE'), 404
    dispatched = []
    errors     = []
    for (aid, agent_id) in rows:
        try:
            result = db.session.execute(
                text("""INSERT INTO cve_patch_job
                        (asset_id, agent_id, cve_id, status, deployed_by, deployed_at, updated_at, created_at)
                        VALUES (:aid, :agent, :cve, 'queued', :who, :now, :now, :now)
                        RETURNING id"""),
                {'aid': aid, 'agent': agent_id, 'cve': cve_id, 'who': username, 'now': now_str}
            )
            db.session.commit()
            job_id = result.scalar()
        except Exception as e:
            errors.append({'agent_id': agent_id, 'error': f'DB error: {e}'})
            continue
        payload = json.dumps({'job_id': job_id, 'cve_ids': [cve_id]}).encode()
        try:
            import urllib.request as _req
            req = _req.Request(
                f"{RMM_GATEWAY_INTERNAL}/deploy-cve-patches/{agent_id}",
                data=payload, headers={'Content-Type': 'application/json'}, method='POST'
            )
            with _req.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            if result.get('ok'):
                dispatched.append({'asset_id': aid, 'agent_id': agent_id, 'job_id': job_id})
                db.session.execute(
                    text("UPDATE cve_patch_job SET status='deploying', updated_at=:now WHERE id=:jid"),
                    {'now': now_str, 'jid': job_id}
                )
                db.session.commit()
            else:
                errors.append({'agent_id': agent_id, 'error': result.get('error', 'gateway error')})
        except Exception as e:
            errors.append({'agent_id': agent_id, 'error': str(e)})
    return jsonify(ok=True, dispatched=dispatched, errors=errors, total=len(rows), sent=len(dispatched))


@bp.route('/api/vulnerabilities/cve-patch-jobs')
@login_required
def api_cve_patch_jobs():
    cve_id   = request.args.get('cve_id')
    asset_id = request.args.get('asset_id')
    if not cve_id:
        return jsonify(ok=False, error='cve_id required'), 400
    con = _alert_svc._get_db()
    try:
        if asset_id:
            rows = con.execute(
                """SELECT j.*, a.name as asset_name FROM cve_patch_job j
                   LEFT JOIN asset a ON a.id = j.asset_id
                   WHERE j.cve_id = ? AND j.asset_id = ? ORDER BY j.id DESC LIMIT 1""",
                (cve_id, asset_id)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT j.*, a.name as asset_name FROM cve_patch_job j
                   LEFT JOIN asset a ON a.id = j.asset_id
                   WHERE j.cve_id = ? ORDER BY j.id DESC LIMIT 50""",
                (cve_id,)
            ).fetchall()
        return jsonify(ok=True, jobs=[dict(r) for r in rows])
    finally:
        con.close()


@bp.route('/api/vulnerabilities/by-app')
@login_required
def api_vuln_by_app():
    """Return CVEs grouped by product_name with device counts — for one-click patch-all."""
    con = _alert_svc._get_db()
    try:
        rows = con.execute("""
            SELECT
                dv.product_name,
                vc.severity,
                COUNT(DISTINCT dv.cve_id)   AS cve_count,
                COUNT(DISTINCT dv.asset_id) AS device_count,
                MAX(vc.cvss)                AS max_cvss,
                STRING_AGG(dv.cve_id, ',') AS cve_ids
            FROM device_vulnerability dv
            LEFT JOIN vulnerability_cache vc ON vc.cve_id = dv.cve_id
            WHERE dv.status = 'Open' AND dv.product_name IS NOT NULL AND dv.product_name != ''
            GROUP BY dv.product_name, vc.severity
            ORDER BY max_cvss DESC, cve_count DESC
            LIMIT 200
        """).fetchall()
        result = {}
        for r in rows:
            pname = r['product_name']
            if pname not in result:
                result[pname] = {'product_name': pname, 'severities': {}, 'total_cves': 0, 'total_devices': 0, 'max_cvss': 0, 'all_cve_ids': []}
            result[pname]['severities'][r['severity']] = r['cve_count']
            result[pname]['total_cves'] += r['cve_count']
            result[pname]['total_devices'] = max(result[pname]['total_devices'], r['device_count'])
            result[pname]['max_cvss'] = max(result[pname]['max_cvss'], r['max_cvss'] or 0)
            result[pname]['all_cve_ids'] += (r['cve_ids'] or '').split(',')
        apps = sorted(result.values(), key=lambda x: (-x['max_cvss'], -x['total_cves']))
        return jsonify(ok=True, apps=apps)
    finally:
        con.close()


@bp.route('/api/vulnerabilities/patch-all-by-app', methods=['POST'])
@login_required
@manager_required
def api_patch_all_by_app():
    """Queue CVE patch jobs for all open CVEs of a given product_name across all affected devices."""
    data = request.get_json(force=True) or {}
    product_name = (data.get('product_name') or '').strip()
    if not product_name:
        return jsonify(ok=False, error='product_name required'), 400
    con = _alert_svc._get_db()
    try:
        rows = con.execute("""
            SELECT DISTINCT dv.cve_id, dv.asset_id, ra.agent_id
            FROM device_vulnerability dv
            JOIN rmm_agent ra ON ra.asset_id = dv.asset_id AND ra.enabled = true
            WHERE dv.product_name = ? AND dv.status = 'Open'
        """, (product_name,)).fetchall()
    finally:
        con.close()
    if not rows:
        return jsonify(ok=False, error='No connected agents for this product'), 404
    username = current_user.username
    now_str  = now_mst().strftime('%Y-%m-%d %H:%M')
    dispatched, errors = [], []
    for r in rows:
        cve_id, asset_id, agent_id = r['cve_id'], r['asset_id'], r['agent_id']
        try:
            db.session.execute(
                text("""INSERT INTO cve_patch_job
                        (asset_id, agent_id, cve_id, status, deployed_by, deployed_at, updated_at, created_at)
                        VALUES (:aid, :agt, :cve, 'queued', :who, :now, :now, :now)
                        ON CONFLICT DO NOTHING"""),
                {'aid': asset_id, 'agt': agent_id, 'cve': cve_id, 'who': username, 'now': now_str}
            )
            dispatched.append({'asset_id': asset_id, 'cve_id': cve_id})
        except Exception as e:
            errors.append({'cve_id': cve_id, 'asset_id': asset_id, 'error': str(e)})
    db.session.commit()
    return jsonify(ok=True, product_name=product_name, queued=len(dispatched), errors=errors)