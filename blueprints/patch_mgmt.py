"""Fleet-wide Patch Management blueprint."""
import json
import logging
import urllib.request as _ur
import urllib.error as _ue
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text
from extensions import db
from utils import manager_required

logger = logging.getLogger(__name__)

bp = Blueprint('patch_mgmt', __name__)

# Same gateway URL pattern used everywhere in rmm.py
def _gw():
    from app import app
    return app.config.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')

# ─────────────────────────────────────────────────────────────
#  Page
# ─────────────────────────────────────────────────────────────
@bp.route('/patches')
@login_required
def patch_dashboard():
    return render_template('patch_dashboard.html')


# ─────────────────────────────────────────────────────────────
#  API: fleet pending updates summary
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/pending')
@login_required
def api_patches_pending():
    """Return all pending updates grouped by update_id with affected agents."""
    try:
        rows = db.session.execute(text("""
            SELECT
                pu.update_id,
                pu.title,
                pu.severity,
                pu.category,
                pu.size_mb,
                bool_or(pu.reboot_required)              AS reboot_required,
                MIN(pu.recorded_at)                      AS first_seen,
                COUNT(DISTINCT pu.agent_id)              AS device_count,
                ARRAY_AGG(DISTINCT pu.agent_id)          AS agents,
                ARRAY_AGG(DISTINCT COALESCE(a.name, pu.agent_id)) AS asset_names
            FROM rmm_pending_update pu
            LEFT JOIN rmm_agent ra ON ra.agent_id = pu.agent_id AND ra.enabled = true
            LEFT JOIN asset a       ON a.id = ra.asset_id
            GROUP BY pu.update_id, pu.title, pu.severity, pu.category, pu.size_mb
            ORDER BY
                CASE pu.severity
                    WHEN 'Critical'  THEN 1 WHEN 'Important' THEN 2
                    WHEN 'Moderate'  THEN 3 WHEN 'Low'       THEN 4 ELSE 5
                END,
                pu.title
        """)).fetchall()

        # Fetch latest job status per agent
        job_rows = db.session.execute(text("""
            SELECT agent_id, status FROM rmm_patch_job
            WHERE id IN (SELECT MAX(id) FROM rmm_patch_job GROUP BY agent_id)
        """)).fetchall()
        agent_job_status = {r[0]: r[1] for r in job_rows}

        updates = []
        for r in rows:
            agents = list(r[8]) if r[8] else []
            names  = list(r[9]) if r[9] else []
            # Count job statuses across agents for this update
            job_counts = {'completed': 0, 'failed': 0, 'deploying': 0, 'queued': 0}
            for a in agents:
                s = agent_job_status.get(a)
                if s in job_counts:
                    job_counts[s] += 1
            updates.append({
                'update_id':       r[0],
                'title':           r[1],
                'severity':        r[2] or 'Unspecified',
                'category':        r[3] or 'Other',
                'size_mb':         float(r[4]) if r[4] else 0,
                'reboot_required': bool(r[5]),
                'first_seen':      r[6].isoformat() if r[6] else None,
                'device_count':    r[7],
                'agents':          agents,
                'asset_names':     names,
                'job_counts':      job_counts,
            })
        return jsonify({'ok': True, 'updates': updates})
    except Exception as exc:
        logger.exception('api_patches_pending error')
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(exc)}), 500


# ─────────────────────────────────────────────────────────────
#  API: per-device pending updates
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/by-device')
@login_required
def api_patches_by_device():
    """Return all pending updates grouped by agent/device."""
    try:
        rows = db.session.execute(text("""
            SELECT
                pu.agent_id,
                COALESCE(a.name, pu.agent_id)   AS asset_name,
                a.id                             AS asset_id,
                COUNT(*)                         AS pending_count,
                SUM(CASE WHEN pu.reboot_required THEN 1 ELSE 0 END) AS reboot_count,
                SUM(CASE WHEN pu.severity='Critical'  THEN 1 ELSE 0 END) AS critical,
                SUM(CASE WHEN pu.severity='Important' THEN 1 ELSE 0 END) AS important,
                ra.last_seen_at
            FROM rmm_pending_update pu
            JOIN rmm_agent ra ON ra.agent_id = pu.agent_id AND ra.enabled = true
            LEFT JOIN asset a ON a.id = ra.asset_id
            GROUP BY pu.agent_id, a.name, a.id, ra.last_seen_at
            ORDER BY (SUM(CASE WHEN pu.severity='Critical' THEN 1 ELSE 0 END) +
                      SUM(CASE WHEN pu.severity='Important' THEN 1 ELSE 0 END)) DESC, a.name
        """)).fetchall()

        # Re-use job status map
        job_rows2 = db.session.execute(text("""
            SELECT j.agent_id, j.status, j.updated_at
            FROM rmm_patch_job j
            WHERE j.id IN (SELECT MAX(id) FROM rmm_patch_job GROUP BY agent_id)
        """)).fetchall()
        agent_last_job = {r[0]: {'status': r[1], 'updated_at': r[2].isoformat() if r[2] else None}
                          for r in job_rows2}

        devices = []
        for r in rows:
            aj = agent_last_job.get(r[0])
            devices.append({
                'agent_id':     r[0],
                'asset_name':   r[1],
                'asset_id':     r[2],
                'pending_count':r[3],
                'reboot_count': int(r[4]) if r[4] else 0,
                'critical':     int(r[5]) if r[5] else 0,
                'important':    int(r[6]) if r[6] else 0,
                'last_seen':    r[7].isoformat() if r[7] else None,
                'last_job':     aj,
            })
        return jsonify({'ok': True, 'devices': devices})
    except Exception as exc:
        logger.exception('api_patches_by_device error')
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(exc)}), 500


# ─────────────────────────────────────────────────────────────
#  API: auto-approve rules
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/auto-approve-rules')
@login_required
def api_auto_approve_rules():
    row = db.session.execute(
        text("SELECT value FROM setting WHERE key='patch_auto_approve_rules'")
    ).fetchone()
    rules = json.loads(row[0]) if row and row[0] else _default_rules()
    return jsonify({'ok': True, 'rules': rules})


@bp.route('/api/patches/auto-approve-rules', methods=['POST'])
@login_required
@manager_required
def api_save_auto_approve_rules():
    data = request.get_json() or {}
    rules = data.get('rules', [])
    db.session.execute(text("""
        INSERT INTO setting (key, value) VALUES ('patch_auto_approve_rules', :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """), {'v': json.dumps(rules)})
    db.session.commit()
    return jsonify({'ok': True})


def _default_rules():
    return [
        {'id': 'defender', 'label': 'Microsoft Defender Antivirus (signature updates)',
         'match_title': 'Security Intelligence Update for Microsoft Defender Antivirus',
         'enabled': True, 'auto_deploy': True},
        {'id': 'msrt', 'label': 'Malicious Software Removal Tool',
         'match_title': 'Windows Malicious Software Removal Tool',
         'enabled': True, 'auto_deploy': True},
        {'id': 'wu_security', 'label': 'Windows Security platform updates (KB5007651)',
         'match_title': 'Update for Windows Security platform',
         'enabled': True, 'auto_deploy': True},
        {'id': 'critical', 'label': 'All Critical severity updates',
         'match_severity': 'Critical',
         'enabled': False, 'auto_deploy': False},
        {'id': 'important', 'label': 'All Important severity updates',
         'match_severity': 'Important',
         'enabled': False, 'auto_deploy': False},
        {'id': 'drivers', 'label': 'Driver updates',
         'match_category': 'Drivers',
         'enabled': False, 'auto_deploy': False},
    ]


# ─────────────────────────────────────────────────────────────
#  API: deploy patches to selected agents
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/deploy', methods=['POST'])
@login_required
@manager_required
def api_patches_deploy():
    """
    Deploy a set of update_ids to a list of agents.
    Body: { "agent_ids": [...], "update_ids": [...], "deploy_now": true }
    Or: { "agent_ids": [...], "all_pending": true }  — deploy everything pending
    """
    data       = request.get_json() or {}
    agent_ids  = data.get('agent_ids') or []
    update_ids = data.get('update_ids') or []
    all_pending = bool(data.get('all_pending'))
    deploy_now  = bool(data.get('deploy_now', True))

    if not agent_ids:
        return jsonify({'ok': False, 'error': 'agent_ids required'}), 400

    # Batch-fetch asset names for all agents
    asset_name_rows = db.session.execute(
        text("""SELECT ra.agent_id, a.name
                FROM rmm_agent ra
                JOIN asset a ON a.id = ra.asset_id
                WHERE ra.agent_id = ANY(:aids) AND ra.enabled = true"""),
        {'aids': agent_ids}
    ).fetchall()
    asset_name_map = {r[0]: r[1] for r in asset_name_rows}

    results = {'deployed': 0, 'queued': 0, 'offline': 0, 'no_updates': 0, 'errors': [], 'detail': []}

    for agent_id in agent_ids:
        # Determine which updates to send
        if all_pending:
            upd_rows = db.session.execute(
                text("SELECT update_id, title, kb_ids FROM rmm_pending_update WHERE agent_id = :aid"),
                {'aid': agent_id}
            ).fetchall()
        else:
            upd_rows = db.session.execute(
                text("SELECT update_id, title, kb_ids FROM rmm_pending_update WHERE agent_id = :aid AND update_id = ANY(:uids)"),
                {'aid': agent_id, 'uids': update_ids}
            ).fetchall()

        asset_name = asset_name_map.get(agent_id, agent_id)

        if not upd_rows:
            results['no_updates'] += 1
            results['detail'].append({'agent_id': agent_id, 'asset_name': asset_name, 'status': 'no_updates'})
            continue

        uids   = [r[0] for r in upd_rows]
        titles = [r[1] for r in upd_rows]
        kbids  = []

        # Create job record
        res = db.session.execute(text("""
            INSERT INTO rmm_patch_job
                (agent_id, update_ids, kb_ids, titles, status, approved_by, approved_at)
            VALUES (:aid, :uids, :kbids, :titles, 'queued', :uid, NOW())
            RETURNING id
        """), {
            'aid':    agent_id,
            'uids':   json.dumps(uids),
            'kbids':  json.dumps(kbids),
            'titles': json.dumps(titles),
            'uid':    current_user.id if hasattr(current_user, 'id') else None,
        })
        db.session.commit()
        job_id = res.scalar()

        if not deploy_now:
            results['queued'] += 1
            results['detail'].append({'agent_id': agent_id, 'asset_name': asset_name, 'status': 'queued'})
            continue

        # Fire to gateway
        payload = json.dumps({
            'type':       'install_patches',
            'job_id':     job_id,
            'update_ids': uids,
            'kb_ids':     kbids,
            'titles':     titles,
        }).encode()
        try:
            req = _ur.Request(
                f"{_gw()}/send-msg/{agent_id}",
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with _ur.urlopen(req, timeout=8) as resp:
                result = json.loads(resp.read())
            if result.get('ok'):
                db.session.execute(
                    text("UPDATE rmm_patch_job SET status='deploying', deployed_at=NOW(), updated_at=NOW() WHERE id=:jid"),
                    {'jid': job_id}
                )
                db.session.commit()
                results['deployed'] += 1
                results['detail'].append({'agent_id': agent_id, 'asset_name': asset_name, 'status': 'deploying'})
            else:
                results['offline'] += 1
                results['detail'].append({'agent_id': agent_id, 'asset_name': asset_name, 'status': 'offline'})
        except Exception as e:
            results['offline'] += 1
            results['errors'].append(f"{agent_id}: {e}")
            results['detail'].append({'agent_id': agent_id, 'asset_name': asset_name, 'status': 'offline'})

    return jsonify({'ok': True, 'results': results})


# ─────────────────────────────────────────────────────────────
#  API: recent job status (per-agent and per-update_id)
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/job-status')
@login_required
def api_patches_job_status():
    """Return recent patch job status keyed by agent_id and update_id."""
    try:
        rows = db.session.execute(text("""
            SELECT agent_id, update_ids, status, updated_at, id
            FROM rmm_patch_job
            ORDER BY id DESC
        """)).fetchall()

        by_agent  = {}
        by_update = {}

        for agent_id, update_ids_json, status, updated_at, job_id in rows:
            # Latest job per agent (first row = newest due to ORDER BY DESC)
            if agent_id not in by_agent:
                by_agent[agent_id] = {
                    'status':     status,
                    'updated_at': updated_at.isoformat() if updated_at else None,
                    'job_id':     job_id,
                }

            # Per update_id: accumulate status counts
            try:
                uids = json.loads(update_ids_json) if update_ids_json else []
            except Exception:
                uids = []

            for uid in uids:
                if uid not in by_update:
                    by_update[uid] = {
                        'completed': 0, 'failed': 0,
                        'deploying': 0, 'queued': 0,
                        'latest_at': updated_at.isoformat() if updated_at else None,
                    }
                s = status if status in ('completed', 'failed', 'deploying', 'queued') else 'queued'
                by_update[uid][s] = by_update[uid].get(s, 0) + 1

        return jsonify({'ok': True, 'by_agent': by_agent, 'by_update': by_update})
    except Exception as exc:
        db.session.rollback()
        logger.exception('job-status error')
        return jsonify({'ok': False, 'error': str(exc)}), 500


# ─────────────────────────────────────────────────────────────
#  API: run auto-approve now (manual trigger)
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/run-auto-approve', methods=['POST'])
@login_required
@manager_required
def api_run_auto_approve():
    deployed, skipped = _run_auto_approve()
    return jsonify({'ok': True, 'deployed': deployed, 'skipped': skipped})


def _run_auto_approve():
    """Find pending updates matching auto-approve rules and deploy them."""
    row = db.session.execute(
        text("SELECT value FROM setting WHERE key='patch_auto_approve_rules'")
    ).fetchone()
    rules = json.loads(row[0]) if row and row[0] else _default_rules()
    active_rules = [r for r in rules if r.get('enabled') and r.get('auto_deploy')]
    if not active_rules:
        return 0, 0

    # Build SQL conditions for matching updates
    all_pending = db.session.execute(text("""
        SELECT pu.agent_id, pu.update_id, pu.title, pu.severity, pu.category
        FROM rmm_pending_update pu
        JOIN rmm_agent ra ON ra.agent_id = pu.agent_id AND ra.enabled = true
    """)).fetchall()

    # Group matches by agent
    agent_updates = {}  # agent_id -> list of (update_id, title)
    for pu in all_pending:
        agent_id  = pu[0]
        update_id = pu[1]
        title     = pu[2] or ''
        severity  = pu[3] or ''
        category  = pu[4] or ''

        matched = False
        for rule in active_rules:
            if 'match_title' in rule and rule['match_title'].lower() in title.lower():
                matched = True; break
            if 'match_severity' in rule and rule['match_severity'].lower() == severity.lower():
                matched = True; break
            if 'match_category' in rule and rule['match_category'].lower() == category.lower():
                matched = True; break

        if matched:
            agent_updates.setdefault(agent_id, []).append((update_id, title))

    if not agent_updates:
        return 0, 0

    deployed = 0
    skipped  = 0
    for agent_id, updates in agent_updates.items():
        uids   = [u[0] for u in updates]
        titles = [u[1] for u in updates]

        res = db.session.execute(text("""
            INSERT INTO rmm_patch_job
                (agent_id, update_ids, kb_ids, titles, status, approved_by, approved_at)
            VALUES (:aid, :uids, '[]', :titles, 'queued', NULL, NOW())
            RETURNING id
        """), {'aid': agent_id, 'uids': json.dumps(uids), 'titles': json.dumps(titles)})
        db.session.commit()
        job_id = res.scalar()

        payload = json.dumps({
            'type':       'install_patches',
            'job_id':     job_id,
            'update_ids': uids,
            'kb_ids':     [],
            'titles':     titles,
        }).encode()
        try:
            req = _ur.Request(
                f"{_gw()}/send-msg/{agent_id}",
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with _ur.urlopen(req, timeout=8) as resp:
                result = json.loads(resp.read())
            if result.get('ok'):
                db.session.execute(
                    text("UPDATE rmm_patch_job SET status='deploying', deployed_at=NOW(), updated_at=NOW() WHERE id=:jid"),
                    {'jid': job_id}
                )
                db.session.commit()
                deployed += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1

    return deployed, skipped
