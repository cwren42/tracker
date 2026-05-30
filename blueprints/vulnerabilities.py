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
            last_sync = _dt.astimezone(_MST).strftime('%Y-%m-%d %I:%M %p') + ' MST'
        except Exception:
            last_sync = str(last_sync_raw)[:16]
    return render_template('vulnerability_dashboard.html',
                           counts=counts, last_sync=last_sync,
                           device_count=device_count, open_count=open_count)


@bp.route('/api/vulnerabilities/sync', methods=['POST'])
@login_required
def api_vuln_sync():
    from flask import current_app as _current_app
    _app = _current_app._get_current_object()
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
            INNER JOIN device_vulnerability dv ON dv.cve_id = vc.cve_id
                AND dv.status NOT IN ('Remediated','Closed')
            GROUP BY vc.severity
        """).fetchall():
            counts[row['severity']] = row['c']
        devices = con.execute("SELECT COUNT(DISTINCT asset_id) FROM device_vulnerability WHERE status NOT IN ('Remediated','Closed')").fetchone()[0]
        open_exp = con.execute("SELECT COUNT(*) FROM device_vulnerability WHERE status NOT IN ('Remediated','Closed')").fetchone()[0]
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
                last_sync_mst = _dt.astimezone(_MST).strftime('%Y-%m-%d %I:%M %p') + ' MST'
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
                         FROM device_vulnerability
                         WHERE status NOT IN ('Remediated','Closed') GROUP BY cve_id) dc
                     ON dc.cve_id = vc.cve_id
                   WHERE vc.severity=%s
                   ORDER BY vc.cvss DESC LIMIT %s""",
                (sev, limit)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT vc.*, dc.device_count
                   FROM vulnerability_cache vc
                   JOIN (SELECT cve_id, COUNT(DISTINCT asset_id) AS device_count
                         FROM device_vulnerability
                         WHERE status NOT IN ('Remediated','Closed') GROUP BY cve_id) dc
                     ON dc.cve_id = vc.cve_id
                   ORDER BY CASE vc.severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                             WHEN 'Medium' THEN 3 ELSE 4 END, vc.cvss DESC LIMIT %s""",
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
                """SELECT dv.*, a.name as asset_name, a.name as display_name
                   FROM device_vulnerability dv
                   LEFT JOIN asset a ON a.id = dv.asset_id
                   WHERE dv.cve_id=%s
                   ORDER BY CASE dv.severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                             WHEN 'Medium' THEN 3 ELSE 4 END""",
                (cve_id,)
            ).fetchall()
        elif asset_id:
            rows = con.execute(
                """SELECT dv.*, vc.name as vuln_name, vc.description
                   FROM device_vulnerability dv
                   LEFT JOIN vulnerability_cache vc ON vc.cve_id = dv.cve_id
                   WHERE dv.asset_id=%s AND dv.status NOT IN ('Closed','Remediated','Exception')
                   ORDER BY CASE dv.severity
                   WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END""",
                (asset_id,)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT dv.*, a.name as asset_name, a.name as display_name
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
                   SET status=%s, remediation_note=%s, plan_date=%s, updated_at=%s, updated_by=%s
                   WHERE cve_id=%s AND asset_id=%s""",
                (status, note, plan, now_str, username, cve_id, asset_id)
            )
            con.commit()
        else:
            con.execute(
                """UPDATE device_vulnerability
                   SET status=%s, remediation_note=%s, plan_date=%s, updated_at=%s, updated_by=%s
                   WHERE cve_id=%s""",
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
                """SELECT dv.asset_id, ra.agent_id, dv.product_name
                   FROM device_vulnerability dv
                   JOIN rmm_agent ra ON ra.asset_id = dv.asset_id AND ra.enabled = true
                   WHERE dv.cve_id = %s AND dv.asset_id = %s LIMIT 1""",
                (cve_id, asset_id)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT DISTINCT dv.asset_id, ra.agent_id, dv.product_name
                   FROM device_vulnerability dv
                   JOIN rmm_agent ra ON ra.asset_id = dv.asset_id AND ra.enabled = true
                   WHERE dv.cve_id = %s AND dv.status NOT IN ('Closed','Remediated','Exception')""",
                (cve_id,)
            ).fetchall()
    finally:
        con.close()
    if not rows:
        return jsonify(ok=False, error='No connected agents found for this CVE'), 404
    dispatched = []
    errors     = []
    for row in rows:
        aid, agent_id, product_name = row['asset_id'], row['agent_id'], (row['product_name'] or '')
        try:
            result = db.session.execute(
                text("""INSERT INTO cve_patch_job
                        (asset_id, agent_id, cve_id, status, deployed_by, deployed_at, updated_at, created_at)
                        VALUES (:aid, :agent, :cve, 'queued', :who, :now, :now, :now)
                        RETURNING id"""),
                {'aid': aid, 'agent': agent_id, 'cve': cve_id, 'who': username, 'now': now_str}
            )
            job_id = result.scalar()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            errors.append({'agent_id': agent_id, 'error': f'DB error: {e}'})
            continue
        payload = json.dumps({'type': 'install_cve_patches', 'job_id': job_id, 'cve_ids': [cve_id], 'product_name': product_name}).encode()
        try:
            import urllib.request as _req
            req = _req.Request(
                f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
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
                gw_err = result.get('error', 'gateway error')
                errors.append({'agent_id': agent_id, 'error': gw_err})
                db.session.execute(
                    text("UPDATE cve_patch_job SET status='failed', updated_at=:now WHERE id=:jid"),
                    {'now': now_str, 'jid': job_id}
                )
                db.session.commit()
        except Exception as e:
            errors.append({'agent_id': agent_id, 'error': str(e)})
            try:
                db.session.execute(
                    text("UPDATE cve_patch_job SET status='failed', updated_at=:now WHERE id=:jid"),
                    {'now': now_str, 'jid': job_id}
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
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
                   WHERE j.cve_id = %s AND j.asset_id = %s ORDER BY j.id DESC LIMIT 1""",
                (cve_id, asset_id)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT j.*, a.name as asset_name FROM cve_patch_job j
                   LEFT JOIN asset a ON a.id = j.asset_id
                   WHERE j.cve_id = %s ORDER BY j.id DESC LIMIT 50""",
                (cve_id,)
            ).fetchall()
        return jsonify(ok=True, jobs=[dict(r) for r in rows])
    finally:
        con.close()


@bp.route('/api/vulnerabilities/bulk-status', methods=['PUT'])
@login_required
def api_vuln_bulk_status():
    d        = request.get_json(force=True) or {}
    asset_id = d.get('asset_id')
    cve_ids  = d.get('cve_ids') or []
    status   = d.get('status', 'Open')
    if not asset_id or not cve_ids:
        return jsonify(ok=False, error='asset_id and cve_ids required'), 400
    username = current_user.username if current_user.is_authenticated else 'system'
    now_str  = now_mst().strftime('%Y-%m-%d %H:%M')
    con = _alert_svc._get_db()
    try:
        for cve_id in cve_ids:
            con.execute(
                """UPDATE device_vulnerability
                   SET status=%s, updated_at=%s, updated_by=%s
                   WHERE cve_id=%s AND asset_id=%s""",
                (status, now_str, username, cve_id, asset_id)
            )
        con.commit()
        return jsonify(ok=True, updated=len(cve_ids))
    finally:
        con.close()


@bp.route('/api/vulnerabilities/patch-history')
@login_required
def api_vuln_patch_history():
    asset_id = request.args.get('asset_id')
    if not asset_id:
        return jsonify(ok=False, error='asset_id required'), 400
    con = _alert_svc._get_db()
    try:
        rows = con.execute(
            """SELECT j.id, j.cve_id, j.status, j.deployed_by, j.deployed_at,
                      j.completed_at, j.result_json, j.reboot_required, j.updates_found
               FROM cve_patch_job j
               WHERE j.asset_id = %s
               ORDER BY j.id DESC
               LIMIT 200""",
            (asset_id,)
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
                MAX(vc.cvss)                AS max_cvss
            FROM device_vulnerability dv
            LEFT JOIN vulnerability_cache vc ON vc.cve_id = dv.cve_id
            WHERE dv.status NOT IN ('Remediated','Closed') AND dv.product_name IS NOT NULL AND dv.product_name != ''
            GROUP BY dv.product_name, vc.severity
            LIMIT 400
        """).fetchall()
        result = {}
        for r in rows:
            pname = r['product_name']
            if pname not in result:
                result[pname] = {'product_name': pname, 'severities': {}, 'total_cves': 0, 'total_devices': 0, 'max_cvss': 0}
            result[pname]['severities'][r['severity'] or 'Unknown'] = r['cve_count']
            result[pname]['total_cves'] += r['cve_count']
            result[pname]['total_devices'] = max(result[pname]['total_devices'], r['device_count'])
            result[pname]['max_cvss'] = max(result[pname]['max_cvss'], r['max_cvss'] or 0)
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
            WHERE dv.product_name = %s AND dv.status = 'Open'
        """, (product_name,)).fetchall()
    finally:
        con.close()
    if not rows:
        return jsonify(ok=False, error='No connected agents for this product'), 404
    username = current_user.username
    now_str  = now_mst().strftime('%Y-%m-%d %H:%M')
    dispatched, errors = [], []
    # Group CVEs by agent so we send one message per agent with all its CVEs
    agent_map = {}
    for r in rows:
        key = (r['asset_id'], r['agent_id'])
        if key not in agent_map:
            agent_map[key] = []
        agent_map[key].append(r['cve_id'])
    for (asset_id, agent_id), cve_ids in agent_map.items():
        job_id = None
        try:
            result = db.session.execute(
                text("""INSERT INTO cve_patch_job
                        (asset_id, agent_id, cve_id, status, deployed_by, deployed_at, updated_at, created_at)
                        VALUES (:aid, :agt, :cve, 'queued', :who, :now, :now, :now)
                        RETURNING id"""),
                {'aid': asset_id, 'agt': agent_id, 'cve': cve_ids[0], 'who': username, 'now': now_str}
            )
            job_id = result.scalar()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            errors.append({'asset_id': asset_id, 'agent_id': agent_id, 'error': f'DB error: {e}'})
            continue
        payload = json.dumps({'type': 'install_cve_patches', 'job_id': job_id, 'cve_ids': cve_ids, 'product_name': product_name}).encode()
        try:
            import urllib.request as _req
            req = _req.Request(
                f"{RMM_GATEWAY_INTERNAL}/send-msg/{agent_id}",
                data=payload, headers={'Content-Type': 'application/json'}, method='POST'
            )
            with _req.urlopen(req, timeout=10) as resp:
                gw_result = json.loads(resp.read())
            if gw_result.get('ok'):
                dispatched.append({'asset_id': asset_id, 'agent_id': agent_id, 'job_id': job_id, 'cve_count': len(cve_ids)})
                db.session.execute(
                    text("UPDATE cve_patch_job SET status='deploying', updated_at=:now WHERE id=:jid"),
                    {'now': now_str, 'jid': job_id}
                )
                db.session.commit()
            else:
                gw_err = gw_result.get('error', 'gateway error')
                errors.append({'asset_id': asset_id, 'agent_id': agent_id, 'error': gw_err})
                db.session.execute(
                    text("UPDATE cve_patch_job SET status='failed', updated_at=:now WHERE id=:jid"),
                    {'now': now_str, 'jid': job_id}
                )
                db.session.commit()
        except Exception as e:
            errors.append({'asset_id': asset_id, 'agent_id': agent_id, 'error': str(e)})
            try:
                db.session.execute(
                    text("UPDATE cve_patch_job SET status='failed', updated_at=:now WHERE id=:jid"),
                    {'now': now_str, 'jid': job_id}
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
    return jsonify(ok=True, product_name=product_name, queued=len(dispatched), errors=errors)


# ─────────────────────────────────────────────────────────────────────────────
# REPORT ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────


@bp.route('/api/vulnerabilities/report/summary')
@login_required
def api_vuln_report_summary():
    """Overall stats: severity counts, exposure totals, patch job pipeline."""
    con = _alert_svc._get_db()
    try:
        job_rows = con.execute(
            "SELECT status, COUNT(*) AS c FROM cve_patch_job GROUP BY status"
        ).fetchall()
        jobs = {r['status']: r['c'] for r in job_rows}

        dv_rows = con.execute(
            "SELECT status, COUNT(*) AS c FROM device_vulnerability GROUP BY status"
        ).fetchall()
        dv_status = {r['status']: r['c'] for r in dv_rows}

        sev_rows = con.execute("""
            SELECT vc.severity, COUNT(DISTINCT vc.cve_id) AS c
            FROM vulnerability_cache vc
            INNER JOIN device_vulnerability dv ON dv.cve_id = vc.cve_id AND dv.status = 'Open'
            GROUP BY vc.severity
        """).fetchall()
        sev = {r['severity']: r['c'] for r in sev_rows}

        return jsonify(
            ok=True,
            patch_jobs=jobs,
            exposures=dv_status,
            severity=sev,
        )
    finally:
        con.close()


@bp.route('/api/vulnerabilities/report/top-assets')
@login_required
def api_vuln_report_top_assets():
    """Top 20 assets ordered by critical → high → total open CVEs."""
    con = _alert_svc._get_db()
    try:
        rows = con.execute("""
            SELECT
                a.name             AS asset_name,
                dv.asset_id,
                COUNT(*)           AS open_count,
                COUNT(*) FILTER (WHERE vc.severity = 'Critical') AS critical_count,
                COUNT(*) FILTER (WHERE vc.severity = 'High')     AS high_count,
                COUNT(*) FILTER (WHERE vc.severity = 'Medium')   AS medium_count,
                COUNT(*) FILTER (WHERE vc.severity = 'Low')      AS low_count
            FROM device_vulnerability dv
            LEFT JOIN asset a ON a.id = dv.asset_id
            LEFT JOIN vulnerability_cache vc ON vc.cve_id = dv.cve_id
            WHERE dv.status = 'Open'
            GROUP BY dv.asset_id, a.name
            ORDER BY critical_count DESC, high_count DESC, open_count DESC
            LIMIT 20
        """).fetchall()
        return jsonify(ok=True, assets=[dict(r) for r in rows])
    finally:
        con.close()


@bp.route('/api/vulnerabilities/report/trend')
@login_required
def api_vuln_report_trend():
    """Daily count of CVEs remediated over the past 30 days."""
    con = _alert_svc._get_db()
    try:
        rows = con.execute("""
            SELECT
                DATE(updated_at) AS day,
                COUNT(*)         AS count
            FROM device_vulnerability
            WHERE status = 'Remediated'
              AND updated_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(updated_at)
            ORDER BY day
        """).fetchall()
        return jsonify(ok=True, trend=[{'day': str(r['day']), 'count': r['count']} for r in rows])
    finally:
        con.close()