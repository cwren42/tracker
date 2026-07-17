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
    AuditTrail, Asset, AssetHistory, CustomReport, DashboardWidget,
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
@admin_required
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


# ─────────────────────────────────────────────────────────────────────────────
# RECONCILIATION — trace Tracker vuln numbers back to Defender (the external,
# independently-verifiable source). The Defender-side half of the waterfall
# (raw distinct pairs by severity, mapping/filter subtractions, coverage lists)
# is computed during the Defender sync and PERSISTED as a JSON snapshot in the
# `setting` table (key=vuln_reconciliation_snapshot) by alert_service. The page
# GET reads ONLY that stored snapshot + the live device_vulnerability DB counts
# (fast SQL) — it NEVER pulls Defender live, so it renders in <1s. The snapshot's
# fetched_at is shown so freshness is honest. "Refresh from Defender" triggers a
# non-blocking background thread that re-pulls + rewrites the snapshot.
# Read-only except the snapshot write.
# ─────────────────────────────────────────────────────────────────────────────

# Cross-worker (process-wide is not enough under gunicorn) refresh status, kept
# in a DB setting so the page's poller sees a consistent state across workers.
_RECON_REFRESH_FLAG_KEY = 'vuln_reconciliation_refreshing'


def _severity_norm(s):
    s = (s or '').strip().lower()
    if s in ('critical',):    return 'Critical'
    if s in ('high',):        return 'High'
    if s in ('medium', 'med'): return 'Medium'
    if s in ('low',):         return 'Low'
    return 'Other'


def _fmt_mst(raw):
    """Format a UTC datetime / ISO string / DB timestamp into MST display."""
    if not raw:
        return None
    _MST = timezone(timedelta(hours=-7))
    try:
        if isinstance(raw, datetime):
            _dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        else:
            _dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            if _dt.tzinfo is None:
                _dt = _dt.replace(tzinfo=timezone.utc)
        return _dt.astimezone(_MST).strftime('%Y-%m-%d %I:%M:%S %p') + ' MST'
    except Exception:
        return str(raw)[:16]


def _load_recon_snapshot(con):
    """Read the stored reconciliation snapshot JSON from the `setting` row, or
    None if no sync has captured one yet (cold start)."""
    row = con.execute(
        "SELECT value FROM setting WHERE key=%s",
        (_alert_svc.RECON_SNAPSHOT_SETTING_KEY,)
    ).fetchone()
    if not row or not row['value']:
        return None
    try:
        return json.loads(row['value'])
    except Exception:
        logger.warning('Stored reconciliation snapshot is not valid JSON')
        return None


def _live_tracker_counts(con):
    """The authoritative Tracker bottom line straight from device_vulnerability
    (status=Open). Fast SQL — safe to run on every GET."""
    tracker_open_pairs = con.execute(
        "SELECT COUNT(*) AS c FROM device_vulnerability WHERE status='Open'"
    ).fetchone()['c']
    tracker_machines = con.execute(
        "SELECT COUNT(DISTINCT asset_id) AS c FROM device_vulnerability WHERE status='Open'"
    ).fetchone()['c']
    tracker_sev_rows = con.execute(
        """SELECT severity, COUNT(DISTINCT cve_id) AS c
           FROM device_vulnerability WHERE status='Open' GROUP BY severity"""
    ).fetchall()
    tracker_cves_by_sev = {'Critical': 0, 'High': 0, 'Medium': 0, 'Low': 0, 'Other': 0}
    for r in tracker_sev_rows:
        tracker_cves_by_sev[_severity_norm(r['severity'])] = r['c']
    tracker_distinct_cves = con.execute(
        "SELECT COUNT(DISTINCT cve_id) AS c FROM device_vulnerability WHERE status='Open'"
    ).fetchone()['c']
    last_sync_raw = con.execute(
        "SELECT MAX(synced_at) FROM device_vulnerability"
    ).fetchone()[0]
    return {
        'open_pairs':    tracker_open_pairs,
        'machines':      tracker_machines,
        'cves_by_sev':   tracker_cves_by_sev,
        'distinct_cves': tracker_distinct_cves,
        'last_sync_raw': last_sync_raw,
    }


def _build_reconciliation_view():
    """Assemble the page's template context from the STORED Defender snapshot +
    the LIVE device_vulnerability counts. No Defender API call. Returns
    (data, snapshot_present). When no snapshot exists yet (cold start), data
    carries only the live Tracker numbers and snapshot_present is False so the
    page can render the 'not yet captured' state instead of blocking."""
    con = _alert_svc._get_db()
    try:
        snap = _load_recon_snapshot(con)
        tracker = _live_tracker_counts(con)
        refreshing = (con.execute(
            "SELECT value FROM setting WHERE key=%s", (_RECON_REFRESH_FLAG_KEY,)
        ).fetchone() or {}).get('value') == '1'
    finally:
        con.close()

    tracker_metrics = {
        'machines':      tracker['machines'],
        'pairs':         tracker['open_pairs'],
        'cves_by_sev':   tracker['cves_by_sev'],
        'distinct_cves': tracker['distinct_cves'],
    }
    last_sync_mst = _fmt_mst(tracker['last_sync_raw'])

    if not snap:
        # Cold start: no Defender snapshot captured yet. Render the page with
        # only the Tracker-side numbers + a clear prompt. Never block.
        return {
            'snapshot_present': False,
            'refreshing':       refreshing,
            'last_sync_mst':    last_sync_mst,
            'metrics':          {'tracker': tracker_metrics},
        }, False

    wf = dict(snap['waterfall'])
    # Overlay the live DB bottom line + drift onto the frozen Defender waterfall.
    wf['tracker_open'] = tracker['open_pairs']
    wf['drift']        = wf['computed_open'] - tracker['open_pairs']
    wf['ties_out']     = wf['drift'] == 0

    data = {
        'snapshot_present':  True,
        'refreshing':        refreshing,
        'waterfall':         wf,
        'metrics': {
            'defender':   snap['defender_metrics'],
            'reconciled': snap['reconciled_metrics'],
            'tracker':    tracker_metrics,
        },
        'coverage':          snap['coverage'],
        'defender_pull_mst': _fmt_mst(snap.get('fetched_at')),
        'last_sync_mst':     last_sync_mst,
    }
    return data, True


@bp.route('/vulnerabilities/reconciliation')
@login_required
@admin_required
def vulnerability_reconciliation():
    """Renders INSTANTLY from the stored Defender snapshot + live DB counts.
    NEVER pulls Defender live on GET. ?refresh=1 kicks the async background
    re-pull (non-blocking) and redirects back to the clean URL."""
    if request.args.get('refresh') == '1':
        _start_recon_refresh()
        return redirect(url_for('vulnerabilities.vulnerability_reconciliation'))
    error = None
    data = None
    try:
        data, _present = _build_reconciliation_view()
    except Exception as e:
        logger.exception('Vulnerability reconciliation view failed')
        error = str(e)
    return render_template('vulnerability_reconciliation.html', data=data, error=error)


# ── Async "Refresh from Defender" ──────────────────────────────────────────────
# Same daemon-thread pattern as /api/vulnerabilities/sync: the request returns
# immediately while a background thread does the 11.5s live pull, recomputes the
# waterfall, and rewrites the snapshot. The request thread NEVER blocks on the API.

def _start_recon_refresh():
    """Spawn a daemon thread that re-pulls Defender and rewrites the snapshot.
    Idempotent: if a refresh is already in flight (DB flag set), do nothing."""
    _app = current_app._get_current_object()

    con = _alert_svc._get_db()
    try:
        already = (con.execute(
            "SELECT value FROM setting WHERE key=%s", (_RECON_REFRESH_FLAG_KEY,)
        ).fetchone() or {}).get('value') == '1'
        if already:
            con.close()
            return False
        con.execute(
            """INSERT INTO setting (key, value) VALUES (%s, '1')
               ON CONFLICT (key) DO UPDATE SET value='1'""",
            (_RECON_REFRESH_FLAG_KEY,)
        )
        con.commit()
    finally:
        try:
            con.close()
        except Exception:
            pass

    def _bg():
        with _app.app_context():
            con2 = _alert_svc._get_db()
            try:
                from defender_service import DefenderService
                svc = DefenderService()
                machines = svc.get_machines()
                machine_vulns = svc.get_all_machine_vulnerabilities()
                _alert_svc.compute_and_store_reconciliation_snapshot(
                    con2, machines, machine_vulns, datetime.now(timezone.utc)
                )
                con2.commit()
                logger.info('Reconciliation snapshot refreshed from Defender (async)')
            except Exception as e:
                logger.error(f'Async reconciliation refresh failed: {e}', exc_info=True)
            finally:
                # Always clear the in-flight flag, even on failure.
                try:
                    con2.execute(
                        """INSERT INTO setting (key, value) VALUES (%s, '0')
                           ON CONFLICT (key) DO UPDATE SET value='0'""",
                        (_RECON_REFRESH_FLAG_KEY,)
                    )
                    con2.commit()
                except Exception:
                    pass
                try:
                    con2.close()
                except Exception:
                    pass

    threading.Thread(target=_bg, daemon=True, name='recon-refresh').start()
    return True


@bp.route('/api/vulnerabilities/reconciliation/refresh', methods=['POST'])
@login_required
@admin_required
def api_recon_refresh():
    started = _start_recon_refresh()
    return jsonify(ok=True, started=started,
                   message='Refresh started' if started else 'Refresh already in progress')


@bp.route('/api/vulnerabilities/reconciliation/status')
@login_required
@admin_required
def api_recon_status():
    """Tiny status endpoint the page polls after triggering a refresh. Reports
    whether a refresh is in flight + the snapshot's current fetched_at."""
    con = _alert_svc._get_db()
    try:
        snap = _load_recon_snapshot(con)
        refreshing = (con.execute(
            "SELECT value FROM setting WHERE key=%s", (_RECON_REFRESH_FLAG_KEY,)
        ).fetchone() or {}).get('value') == '1'
    finally:
        con.close()
    return jsonify(ok=True, refreshing=refreshing,
                   has_snapshot=snap is not None,
                   fetched_at=(snap or {}).get('fetched_at'))


@bp.route('/api/vulnerabilities/sync', methods=['POST'])
@login_required
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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


# ════════════════════════════════════════════════════════════════════════════════
# BROWSER REMEDIATION (winget-via-remediation-queue)
# ════════════════════════════════════════════════════════════════════════════════
# Queues a least-disruptive `winget upgrade` run_script for the relevant browser(s)
# on a target agent. Persists to rmm_remediation_queue via the gateway's
# /remediation/<agent>/enqueue endpoint, which dispatches immediately if the agent
# is live on the WS or leaves it queued for the reconnect flush (roaming laptops are
# offline most of the time). The reconnect engine correlates the script_result back
# by negative session_id and flips the row to completed/failed. winget upgrade is a
# no-op if the package is already current, so re-queueing is harmless; the browser
# CVE clears on the next Defender re-scan once the box reports the updated version.

# Direct vendor-installer remediation (NOT winget). winget upgrade fails when the
# agent runs as SYSTEM (session 0): its source-index download errors (0x8A150011),
# the same class of HTTP breakage that the TeamViewer tv_x64.dll causes for .NET/
# WinHTTP. So we fetch the vendor's always-latest installer with curl.exe (which is
# unaffected) and install it silently — the proven pattern from the agent installer
# + RustDesk fixes. Always-latest URLs so this is self-currenting; reinstalling the
# current version is a harmless no-op that preserves user profiles. Windows-only;
# a chrome_for_mac asset is excluded by the selection query.
#   kind: 'msi' -> msiexec /i /qn ;  'exe' -> run with `silent` args ;
#   'edge_update' -> trigger Edge's own updater (Edge is OS/auto-managed; no MSI).
_BROWSER_INSTALLER = {
    'chrome':  {'kind': 'msi',
                'url': 'https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi'},
    'firefox': {'kind': 'exe', 'silent': '/S',
                'url': 'https://download.mozilla.org/?product=firefox-latest-ssl&os=win64&lang=en-US'},
    'edge':    {'kind': 'edge_update'},
}

# Least-disruptive: stages the update, no --force / no force-close, no reboot.
# Idempotent (winget no-ops if already current). Note: the agent caps run_script
# timeout at 300s, so a 600s request is clamped to 300 by the agent — browser
# upgrades finish well within that.
_BROWSER_REMEDIATION_TIMEOUT = 600


def _browser_from_product(product_name: str):
    """Return the browser family key ('chrome'|'edge'|'firefox') for a
    device_vulnerability.product_name, or None (e.g. chrome_for_mac -> None)."""
    p = (product_name or '').lower()
    if 'mac' in p:
        return None
    if 'chrome' in p:
        return 'chrome'
    if 'edge' in p:
        return 'edge'
    if 'firefox' in p:
        return 'firefox'
    return None


def _browser_install_code(browser: str) -> str:
    r"""PowerShell that updates one browser via its VENDOR installer (no winget).
    Downloads the always-latest installer with curl.exe (unaffected by the SYSTEM
    session-0 / tv_x64.dll HTTP breakage that kills winget's source download) and
    installs it silently. The leading marker comment lets the enqueue dedup match a
    pending job for the same browser."""
    spec = _BROWSER_INSTALLER[browser]
    marker = f"# rmm-remediate:{browser}\n"
    if spec['kind'] == 'edge_update':
        # Edge has no standalone MSU here; trigger its own updater (always present).
        return marker + (
            "$u=\"${env:ProgramFiles(x86)}\\Microsoft\\EdgeUpdate\\MicrosoftEdgeUpdate.exe\"; "
            "if(-not (Test-Path $u)){Write-Error 'EdgeUpdate not found'; exit 9}; "
            "& $u /silent /ua; exit $LASTEXITCODE"
        )
    url = spec['url']
    if spec['kind'] == 'msi':
        return marker + (
            "$ErrorActionPreference='Stop'; $f=\"$env:TEMP\\rmm_%s.msi\"; "
            "& curl.exe -L -s -o $f --max-time 200 \"%s\"; "
            "if(-not (Test-Path $f) -or (Get-Item $f).Length -lt 1000000){Write-Error 'download failed'; exit 9}; "
            "$p=Start-Process msiexec.exe -ArgumentList \"/i `\"$f`\" /qn /norestart\" -Wait -PassThru; "
            "Remove-Item $f -EA SilentlyContinue; exit $p.ExitCode"
        ) % (browser, url)
    # exe installer
    return marker + (
        "$ErrorActionPreference='Stop'; $f=\"$env:TEMP\\rmm_%s.exe\"; "
        "& curl.exe -L -s -o $f --max-time 200 \"%s\"; "
        "if(-not (Test-Path $f) -or (Get-Item $f).Length -lt 1000000){Write-Error 'download failed'; exit 9}; "
        "$p=Start-Process $f -ArgumentList \"%s\" -Wait -PassThru; "
        "Remove-Item $f -EA SilentlyContinue; exit $p.ExitCode"
    ) % (browser, url, spec.get('silent', '/S'))


def _browser_remediation_payload(browser: str) -> dict:
    """Build the run_script message body that updates one browser via its vendor
    installer."""
    return {
        'type':    'run_script',
        'shell':   'powershell',
        'code':    _browser_install_code(browser),
        'timeout': _BROWSER_REMEDIATION_TIMEOUT,
    }


def _enqueue_browser_remediation(agent_id, asset_id, browser, created_by):
    """Enqueue a single browser-update run_script for one agent via the gateway.

    Idempotent: if a queued/deploying remediation row already targets the same
    winget package for this agent, skip rather than double-queue. Returns a dict
    describing the outcome (status one of: skipped, queued, deploying, error)."""
    if browser not in _BROWSER_INSTALLER:
        return {'agent_id': agent_id, 'browser': browser, 'status': 'error',
                'error': f'unknown browser {browser!r}'}

    # Dedup: a pending (not-yet-terminal) row whose payload targets this browser
    # (matched on the `# rmm-remediate:<browser>` marker the install code emits).
    con = _alert_svc._get_db()
    try:
        existing = con.execute(
            """SELECT id, status FROM rmm_remediation_queue
               WHERE asset_id = %s
                 AND action_type = 'run_script'
                 AND status IN ('queued', 'deploying')
                 AND payload LIKE %s
               ORDER BY id DESC LIMIT 1""",
            (asset_id, f'%rmm-remediate:{browser}%')
        ).fetchone()
    finally:
        con.close()
    if existing:
        return {'agent_id': agent_id, 'browser': browser,
                'status': 'skipped', 'reason': 'already pending',
                'existing_id': existing['id'], 'existing_status': existing['status']}

    body = _browser_remediation_payload(browser)
    enqueue = {
        'action_type': 'run_script',
        'payload':     body,
        'asset_id':    asset_id,
        'created_by':  created_by,
    }
    try:
        import urllib.request as _req
        req = _req.Request(
            f"{RMM_GATEWAY_INTERNAL}/remediation/{agent_id}/enqueue",
            data=json.dumps(enqueue).encode(),
            headers={'Content-Type': 'application/json'}, method='POST'
        )
        with _req.urlopen(req, timeout=10) as resp:
            gw = json.loads(resp.read())
    except Exception as e:
        return {'agent_id': agent_id, 'browser': browser,
                'status': 'error', 'error': str(e)}
    if not gw.get('ok'):
        return {'agent_id': agent_id, 'browser': browser,
                'status': 'error', 'error': gw.get('error', 'gateway error')}
    # gw['status'] is 'deploying' (dispatched to a live WS now) or 'queued'
    # (offline -> waits for the reconnect flush). gw['delivered'] reflects that.
    return {'agent_id': agent_id, 'browser': browser,
            'status': gw.get('status', 'queued'),
            'delivered': gw.get('delivered', False),
            'row_id': gw.get('id')}


@bp.route('/api/vulnerabilities/browser-remediate', methods=['POST'])
@login_required
@admin_required
def api_browser_remediate():
    """Queue least-disruptive winget browser updates for the machines that have an
    Open/Critical browser CVE, delivered via the reconnect-triggered remediation
    engine. Idempotent (dedups pending same-package rows; winget upgrade no-ops if
    current).

    Body JSON (one of):
      { "asset_id": <int> }                 -> just that asset's affected browser(s)
      { "agent_id": "HOST", "browsers": ["chrome"] }  -> explicit single target
      { "batch": true }                     -> all browser-affected Windows agents

    Selection join: device_vulnerability.asset_id -> asset.id -> rmm_agent.asset_id;
    rmm_agent.agent_id is the WS hostname. Only enabled agents; mac excluded."""
    data       = request.get_json(force=True) or {}
    asset_id   = data.get('asset_id')
    agent_id   = data.get('agent_id')
    browsers   = data.get('browsers')
    batch      = bool(data.get('batch'))
    created_by = current_user.id if current_user.is_authenticated else None

    # ── Explicit single-agent target (used by validation) ──────────────────────
    if agent_id and browsers:
        # Resolve asset_id for the queue row if not supplied.
        if not asset_id:
            con = _alert_svc._get_db()
            try:
                r = con.execute(
                    "SELECT asset_id FROM rmm_agent WHERE agent_id=%s AND enabled=true LIMIT 1",
                    (agent_id,)
                ).fetchone()
            finally:
                con.close()
            asset_id = r['asset_id'] if r else None
        results = [_enqueue_browser_remediation(agent_id, asset_id, b, created_by)
                   for b in browsers if b in _BROWSER_INSTALLER]
        return jsonify(ok=True, mode='explicit', agent_id=agent_id, results=results,
                       enqueued=sum(1 for x in results if x['status'] in ('queued', 'deploying')))

    # ── Asset / batch selection from the Open-Critical browser CVE set ─────────
    con = _alert_svc._get_db()
    try:
        params = []
        where_asset = ''
        if asset_id and not batch:
            where_asset = 'AND dv.asset_id = %s'
            params.append(asset_id)
        elif not batch:
            return jsonify(ok=False, error='provide asset_id, (agent_id+browsers), or batch=true'), 400
        rows = con.execute(
            f"""SELECT DISTINCT ra.agent_id, dv.asset_id, dv.product_name
                FROM device_vulnerability dv
                JOIN asset a       ON a.id = dv.asset_id
                JOIN rmm_agent ra  ON ra.asset_id = a.id AND ra.enabled = true
                WHERE dv.severity = 'Critical'
                  AND dv.status NOT IN ('Closed','Remediated','Exception')
                  AND ( (lower(dv.product_name) LIKE '%%chrome%%' AND lower(dv.product_name) NOT LIKE '%%mac%%')
                        OR lower(dv.product_name) LIKE '%%edge%%'
                        OR lower(dv.product_name) LIKE '%%firefox%%' )
                  {where_asset}""",
            tuple(params)
        ).fetchall()
    finally:
        con.close()

    # Collapse to one (agent, browser) per pair so a machine with multiple CVE rows
    # for the same browser is enqueued once.
    seen = set()
    targets = []
    for r in rows:
        b = _browser_from_product(r['product_name'])
        if not b:
            continue
        key = (r['agent_id'], b)
        if key in seen:
            continue
        seen.add(key)
        targets.append((r['agent_id'], r['asset_id'], b))

    results = [_enqueue_browser_remediation(aid, asid, b, created_by)
               for (aid, asid, b) in targets]
    dispatched = [x for x in results if x['status'] == 'deploying']
    queued     = [x for x in results if x['status'] == 'queued']
    skipped    = [x for x in results if x['status'] == 'skipped']
    errors     = [x for x in results if x['status'] == 'error']
    agents = sorted({x['agent_id'] for x in results})
    return jsonify(ok=True, mode='batch' if batch else 'asset',
                   agents=len(agents), pairs=len(results),
                   dispatched=len(dispatched), queued=len(queued),
                   skipped=len(skipped), errors=len(errors),
                   results=results)


@bp.route('/api/vulnerabilities/cve-patch-jobs')
@login_required
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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