"""Fleet-wide Patch Management blueprint."""
import json
import logging
import urllib.request as _ur
import urllib.error as _ue
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text
from extensions import db
from models import now_mst
from utils import manager_required, admin_required

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
@admin_required
def patch_dashboard():
    return render_template('patch_dashboard.html')


# ─────────────────────────────────────────────────────────────
#  Update classification — separate real security patches from the
#  driver / Defender-definition / EU-browser noise that otherwise inflates
#  the "pending" count ~12x and makes the dashboard look wrong.
# ─────────────────────────────────────────────────────────────
def _is_security_update(severity, category):
    sev = (severity or '').strip()
    cat = (category or '').strip().lower()
    if any(x in cat for x in ('driver', 'definition', 'browser choice', 'feature pack', 'language')):
        return False
    if sev in ('Critical', 'Important'):
        return True
    return any(x in cat for x in ('security', 'critical', '.net', 'operating system',
                                  'cumulative', 'servicing stack'))


# ─────────────────────────────────────────────────────────────
#  API: fleet pending updates summary
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/pending')
@login_required
@admin_required
def api_patches_pending():
    """Return all pending updates grouped by update_id with affected agents.

    Only counts pending rows tied to a LIVE enabled agent (inner join) so retired/
    ghost boxes don't inflate the list, and tags each update is_security so the UI can
    default to real patches and hide driver/definition/EU-browser noise."""
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
            JOIN rmm_agent ra ON ra.agent_id = pu.agent_id AND ra.enabled = true
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
            job_counts = {'completed': 0, 'failed': 0, 'deploying': 0, 'queued': 0, 'no_op': 0}
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
                'is_security':     _is_security_update(r[2], r[3]),
            })
        summary = {
            'total':    len(updates),
            'security': sum(1 for u in updates if u['is_security']),
            'other':    sum(1 for u in updates if not u['is_security']),
        }
        return jsonify({'ok': True, 'updates': updates, 'summary': summary})
    except Exception as exc:
        logger.exception('api_patches_pending error')
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(exc)}), 500


# ─────────────────────────────────────────────────────────────
#  API: per-device pending updates
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/by-device')
@login_required
@admin_required
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
                SUM(CASE
                      WHEN lower(coalesce(pu.category,'')) ~ 'driver|definition|browser choice|feature pack|language' THEN 0
                      WHEN coalesce(pu.severity,'') IN ('Critical','Important') THEN 1
                      WHEN lower(coalesce(pu.category,'')) ~ 'security|critical|[.]net|operating system|cumulative|servicing stack' THEN 1
                      ELSE 0 END)                AS security_count,
                MAX(pu.recorded_at)              AS last_scan,
                ra.last_seen_at
            FROM rmm_pending_update pu
            JOIN rmm_agent ra ON ra.agent_id = pu.agent_id AND ra.enabled = true
            LEFT JOIN asset a ON a.id = ra.asset_id
            GROUP BY pu.agent_id, a.name, a.id, ra.last_seen_at
            ORDER BY (SUM(CASE
                      WHEN lower(coalesce(pu.category,'')) ~ 'driver|definition|browser choice|feature pack|language' THEN 0
                      WHEN coalesce(pu.severity,'') IN ('Critical','Important') THEN 1
                      WHEN lower(coalesce(pu.category,'')) ~ 'security|critical|[.]net|operating system|cumulative|servicing stack' THEN 1
                      ELSE 0 END)) DESC, a.name
        """)).fetchall()

        # Re-use job status map
        job_rows2 = db.session.execute(text("""
            SELECT j.agent_id, j.status, j.updated_at
            FROM rmm_patch_job j
            WHERE j.id IN (SELECT MAX(id) FROM rmm_patch_job GROUP BY agent_id)
        """)).fetchall()
        agent_last_job = {r[0]: {'status': r[1], 'updated_at': r[2].isoformat() if r[2] else None}
                          for r in job_rows2}

        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        stale_cutoff = _dt.now(_tz.utc) - _td(days=7)
        devices = []
        for r in rows:
            aj = agent_last_job.get(r[0])
            last_scan = r[8]
            devices.append({
                'agent_id':      r[0],
                'asset_name':    r[1],
                'asset_id':      r[2],
                'pending_count': r[3],
                'reboot_count':  int(r[4]) if r[4] else 0,
                'critical':      int(r[5]) if r[5] else 0,
                'important':     int(r[6]) if r[6] else 0,
                'security_count':int(r[7]) if r[7] else 0,
                'last_scan':     last_scan.isoformat() if last_scan else None,
                'scan_stale':    bool(last_scan and last_scan < stale_cutoff),
                'last_seen':     r[9].isoformat() if r[9] else None,
                'last_job':      aj,
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
@admin_required
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
                # Agent not live — job row stays 'queued' (deploying is only set on a
                # confirmed send above). The gateway reconnect flush will deliver it.
                results['offline'] += 1
                results['detail'].append({'agent_id': agent_id, 'asset_name': asset_name, 'status': 'queued (reconnect)'})
        except Exception as e:
            results['offline'] += 1
            results['errors'].append(f"{agent_id}: {e}")
            results['detail'].append({'agent_id': agent_id, 'asset_name': asset_name, 'status': 'queued (reconnect)'})

    return jsonify({'ok': True, 'results': results})


# ─────────────────────────────────────────────────────────────
#  API: recent job status (per-agent and per-update_id)
# ─────────────────────────────────────────────────────────────
@bp.route('/api/patches/job-status')
@login_required
@admin_required
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
                        'deploying': 0, 'queued': 0, 'no_op': 0,
                        'latest_at': updated_at.isoformat() if updated_at else None,
                    }
                # 'no_op' = agent found nothing to install (terminal, NOT a failure).
                s = status if status in ('completed', 'failed', 'deploying', 'queued', 'no_op') else 'queued'
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


def _patch_setting(key, default=None):
    """Fail-safe read of a setting value."""
    try:
        row = db.session.execute(text("SELECT value FROM setting WHERE key=:k"), {'k': key}).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default


def _truthy(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


# Agents quiet longer than this are considered offline and are NOT targeted for
# auto-deploy (otherwise their jobs sit 'queued' forever — the old 8,941 backlog).
PATCH_ONLINE_CUTOFF_MIN = 15


def _within_maintenance_window():
    """True if now (server-local) is inside the patch maintenance window. Setting
    patch_maintenance_window = 'HH:MM-HH:MM' (server local); default 02:00-06:00.
    Supports windows that wrap midnight."""
    win = (_patch_setting('patch_maintenance_window', '02:00-06:00') or '02:00-06:00').strip()
    try:
        s, e = win.split('-')
        sh, sm = (int(x) for x in s.split(':'))
        eh, em = (int(x) for x in e.split(':'))
    except Exception:
        sh, sm, eh, em = 2, 0, 6, 0
    now = now_mst()
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    return (start <= cur <= end) if start <= end else (cur >= start or cur <= end)


def _cleanup_patch_jobs():
    """Keep rmm_patch_job bounded: fail stuck 'deploying' jobs that never returned a
    result, and purge terminal rows older than 30 days. Runs daily before auto-approve
    so the queue can't balloon back into the old 8,941-row backlog."""
    try:
        stuck = db.session.execute(text("""
            UPDATE rmm_patch_job
               SET status='failed', notes=COALESCE(notes,'') || ' [stale deploy — no result]', updated_at=NOW()
             WHERE status='deploying' AND completed_at IS NULL
               AND (updated_at IS NULL OR updated_at < NOW() - interval '6 hours')""")).rowcount
        purged = db.session.execute(text("""
            DELETE FROM rmm_patch_job
             WHERE status IN ('completed','failed','no_op')
               AND COALESCE(completed_at, updated_at, created_at) < NOW() - interval '30 days'""")).rowcount
        db.session.commit()
        return stuck, purged
    except Exception:
        db.session.rollback()
        return 0, 0


def _run_auto_approve():
    """Auto-deploy pending updates matching the rules — WITH SAFETY RAILS:
      * master kill-switch (patch_auto_deploy_enabled, default on)
      * only inside the maintenance window
      * only to ONLINE agents (no permanent-queued backlog)
      * deduped against agents that already have an open job (no nightly pileup)
      * reboot gated by policy (allow_reboot flag, default off) — honored by the agent
      * non-delivered jobs are left 'queued' (NOT marked failed) — the gateway's
        reconnect flush delivers them when the roaming agent next comes online
    """
    if not _truthy(_patch_setting('patch_auto_deploy_enabled', 'true'), default=True):
        logger.info('patch auto-approve: disabled by setting')
        return 0, 0
    if not _within_maintenance_window():
        logger.info('patch auto-approve: outside maintenance window — skipping')
        return 0, 0

    row = db.session.execute(
        text("SELECT value FROM setting WHERE key='patch_auto_approve_rules'")
    ).fetchone()
    rules = json.loads(row[0]) if row and row[0] else _default_rules()
    active_rules = [r for r in rules if r.get('enabled') and r.get('auto_deploy')]
    if not active_rules:
        return 0, 0

    allow_reboot = _truthy(_patch_setting('patch_allow_reboot', 'false'), default=False)

    # Only ONLINE agents — skip offline so jobs don't pile up queued forever.
    all_pending = db.session.execute(text("""
        SELECT pu.agent_id, pu.update_id, pu.title, pu.severity, pu.category
        FROM rmm_pending_update pu
        JOIN rmm_agent ra ON ra.agent_id = pu.agent_id AND ra.enabled = true
        WHERE ra.last_seen_at IS NOT NULL
          AND ra.last_seen_at > NOW() - (:cutoff || ' minutes')::interval
    """), {'cutoff': str(PATCH_ONLINE_CUTOFF_MIN)}).fetchall()

    # Dedup: skip any agent that already has an open (queued/deploying) job.
    open_agents = {r[0] for r in db.session.execute(
        text("SELECT DISTINCT agent_id FROM rmm_patch_job WHERE status IN ('queued','deploying')")
    ).fetchall()}

    agent_updates = {}  # agent_id -> [(update_id, title)]
    for pu in all_pending:
        agent_id  = pu[0]
        if agent_id in open_agents:
            continue
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
            'type':         'install_patches',
            'job_id':       job_id,
            'update_ids':   uids,
            'kb_ids':       [],
            'titles':       titles,
            'allow_reboot': allow_reboot,   # agent honors this (forced reboot only when true)
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
                # Not delivered (agent not live on the gateway right now). LEAVE IT
                # QUEUED — the gateway reconnect flush will deliver it next time the
                # agent connects. Previously this was marked 'failed', which dropped
                # work for roaming laptops that are rarely online in the window.
                db.session.execute(text(
                    "UPDATE rmm_patch_job SET notes='queued for reconnect delivery (agent not live)', updated_at=NOW() WHERE id=:jid"),
                    {'jid': job_id})
                db.session.commit()
                skipped += 1
        except Exception:
            # Gateway/transport error — also leave queued for the reconnect flush
            # rather than failing the job into the void.
            db.session.execute(text(
                "UPDATE rmm_patch_job SET notes='queued for reconnect delivery (dispatch error)', updated_at=NOW() WHERE id=:jid"),
                {'jid': job_id})
            db.session.commit()
            skipped += 1

    return deployed, skipped
