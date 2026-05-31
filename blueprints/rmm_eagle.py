"""Eagle Eyes (employee activity monitoring) routes for the RMM blueprint.

Split out of the oversized blueprints/rmm.py. Routes register on the same
'rmm' blueprint, so URLs and endpoint names are unchanged. The shared date
helpers stay in rmm.py and are imported here.
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
    AuditTrail, Asset, AssetHistory, Control, CustomReport, DashboardWidget,
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


from blueprints.rmm import bp, _dt_iso, _agent_tz_offset_minutes, _eagle_date_params, _EAGLE_SYSTEM_EXCL


@bp.route('/api/rmm/eagle-eyes/<agent_id>', methods=['GET', 'POST'])
@login_required
def api_rmm_eagle_eyes(agent_id):
    """GET: return current Eagle Eyes config.  POST: enable/disable and push to agent."""
    import json as _json
    if request.method == 'GET':
        row = db.session.execute(
            text("SELECT enabled, screenshot_interval_min, screenshots_enabled FROM rmm_eagle_config WHERE agent_id = :aid"),
            {'aid': agent_id}
        ).fetchone()
        if row:
            return jsonify({'ok': True, 'enabled': bool(row[0]), 'screenshot_interval_min': row[1], 'screenshots_enabled': bool(row[2])})
        return jsonify({'ok': True, 'enabled': False, 'screenshot_interval_min': 30, 'screenshots_enabled': True})

    # POST — update config and push to gateway
    data = request.get_json(force=True) or {}
    enabled             = bool(data.get('enabled', False))
    interval            = int(data.get('screenshot_interval_min', 30))
    screenshots_enabled = bool(data.get('screenshots_enabled', True))
    db.session.execute(
        text("""INSERT INTO rmm_eagle_config (agent_id, enabled, screenshot_interval_min, screenshots_enabled, updated_at)
                VALUES (:aid, :en, :iv, :se, NOW() - INTERVAL '7 hours')
                ON CONFLICT(agent_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    screenshot_interval_min = excluded.screenshot_interval_min,
                    screenshots_enabled = excluded.screenshots_enabled,
                    updated_at = excluded.updated_at"""),
        {'aid': agent_id, 'en': enabled, 'iv': interval, 'se': screenshots_enabled}
    )
    db.session.commit()
    # Push config to connected agent via gateway
    try:
        import urllib.request as _ur
        payload = _json.dumps({
            'enabled': enabled,
            'screenshot_interval_min': interval,
            'screenshots_enabled': screenshots_enabled,
        }).encode()
        req = _ur.Request(
            f"{RMM_GATEWAY_INTERNAL}/eagle-eyes/{agent_id}/push",
            data=payload, headers={'Content-Type': 'application/json'}, method='POST',
        )
        with _ur.urlopen(req, timeout=4) as r:
            _json.loads(r.read())
    except Exception:
        pass  # agent may not be connected; config is persisted so it applies on next connect
    return jsonify({'ok': True, 'enabled': enabled, 'screenshot_interval_min': interval, 'screenshots_enabled': screenshots_enabled})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/events')
@login_required
def api_rmm_eagle_events(agent_id):
    """Return Eagle Eyes window events. Query params: days/from_date/to_date, limit."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    limit = int(request.args.get('limit', 500))
    rows = db.session.execute(
        text(f"""SELECT captured_at, process_name, window_title, duration_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                {_EAGLE_SYSTEM_EXCL}
                ORDER BY captured_at DESC
                LIMIT :lim"""),
        {'aid': agent_id, 'lim': limit, **date_params}
    ).fetchall()
    events = [{'captured_at': _dt_iso(r[0]), 'process_name': r[1], 'window_title': r[2], 'duration_s': r[3]} for r in rows]
    return jsonify({'ok': True, 'events': events})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/app-summary')
@login_required
def api_rmm_eagle_app_summary(agent_id):
    """Return total time per process for the requested day range."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    wh_clause = "AND EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Denver') BETWEEN 8 AND 18" if request.args.get('work_hours') == '1' else ''
    # Use LATERAL joins so that for browser events, a window_title_pattern match
    # overrides the process-level classification (e.g. YouTube → unproductive
    # even though chrome → neutral).
    BROWSERS = "('msedge','chrome','firefox','brave','opera','iexplore','safari')"
    rows = db.session.execute(
        text(f"""WITH classified AS (
                SELECT
                    e.process_name,
                    e.duration_s,
                    COALESCE(site_cls.label, e.process_name) AS display_name,
                    COALESCE(site_cls.productivity, proc_cls.productivity) AS productivity
                FROM rmm_eagle_event e
                LEFT JOIN LATERAL (
                    SELECT sc.label, sc.productivity
                    FROM rmm_eagle_app_class sc
                    WHERE sc.window_title_pattern IS NOT NULL
                      AND (sc.agent_id IS NULL OR sc.agent_id = :aid)
                      AND LOWER(e.process_name) IN {BROWSERS}
                      AND LOWER(COALESCE(e.window_title,'')) LIKE '%' || LOWER(sc.window_title_pattern) || '%'
                    ORDER BY sc.agent_id NULLS LAST
                    LIMIT 1
                ) site_cls ON true
                LEFT JOIN LATERAL (
                    SELECT pc.productivity
                    FROM rmm_eagle_app_class pc
                    WHERE pc.window_title_pattern IS NULL
                      AND LOWER(e.process_name) LIKE LOWER(pc.process_pattern)
                    LIMIT 1
                ) proc_cls ON true
                WHERE e.agent_id = :aid AND {date_clause}
                {_EAGLE_SYSTEM_EXCL}
                {wh_clause}
            )
            SELECT display_name AS process_name,
                   COUNT(*) AS events,
                   SUM(duration_s) AS total_s,
                   productivity
            FROM classified
            GROUP BY display_name, productivity
            ORDER BY total_s DESC
            LIMIT 30"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    summary = [{'process_name': r[0], 'events': r[1], 'total_s': int(r[2] or 0), 'productivity': r[3]} for r in rows]
    return jsonify({'ok': True, 'summary': summary})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/hourly')
@login_required
def api_rmm_eagle_hourly(agent_id):
    """Return total active seconds per hour-of-day (0-23) grouped in server local time."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    wh_clause = "AND EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Denver') BETWEEN 8 AND 18" if request.args.get('work_hours') == '1' else ''
    rows = db.session.execute(
        text(f"""SELECT CAST(EXTRACT(HOUR FROM (captured_at AT TIME ZONE 'America/Denver')) AS INTEGER) as hr,
                       SUM(COALESCE(duration_s, 0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                {_EAGLE_SYSTEM_EXCL}
                {wh_clause}
                GROUP BY hr ORDER BY hr"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    by_hour = {r[0]: int(r[1] or 0) for r in rows}
    result = [{'hour': h, 'total_s': by_hour.get(h, 0)} for h in range(24)]
    return jsonify({'ok': True, 'hourly': result})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/daily')
@login_required
def api_rmm_eagle_daily(agent_id):
    """Return total active seconds per calendar day grouped in server local time.
    Always returns the full date series for the requested period (zeros for empty days)."""
    from_date_arg = request.args.get('from_date', '').strip()
    to_date_arg   = request.args.get('to_date', '').strip()
    date_clause, date_params = _eagle_date_params(default_days=30)
    wh_clause = "AND EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Denver') BETWEEN 8 AND 18" if request.args.get('work_hours') == '1' else ''

    # Determine the full calendar range (America/Denver, DST-aware)
    import zoneinfo as _zi
    today_mt = datetime.now(_zi.ZoneInfo('America/Denver')).date()
    if from_date_arg and to_date_arg:
        try:
            range_start = datetime.strptime(from_date_arg, '%Y-%m-%d').date()
            range_end   = datetime.strptime(to_date_arg,   '%Y-%m-%d').date()
        except ValueError:
            range_start = range_end = today_mt
    else:
        days = int(request.args.get('days', 30))
        range_start = today_mt - timedelta(days=days - 1)
        range_end   = today_mt

    rows = db.session.execute(
        text(f"""SELECT CAST(captured_at AT TIME ZONE 'America/Denver' AS DATE) as day,
                       SUM(COALESCE(duration_s, 0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                {_EAGLE_SYSTEM_EXCL}
                {wh_clause}
                GROUP BY day ORDER BY day"""),
        {'aid': agent_id, **date_params}
    ).fetchall()

    # Build lookup from DB results
    db_map = {str(r[0]): int(r[1] or 0) for r in rows}

    # Generate full series so the frontend always gets every day in the period
    result = []
    cur_day = range_start
    while cur_day <= range_end:
        day_str = str(cur_day)
        result.append({'day': day_str, 'total_s': db_map.get(day_str, 0)})
        cur_day += timedelta(days=1)

    return jsonify({'ok': True, 'daily': result})


@bp.route('/api/rmm/eagle-eyes/<agent_id>/top-sites')
@login_required
def api_rmm_eagle_top_sites(agent_id):
    """Return top browser sites derived from window titles."""
    import re as _re_site
    date_clause, date_params = _eagle_date_params(default_days=7)
    wh_clause = "AND EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Denver') BETWEEN 8 AND 18" if request.args.get('work_hours') == '1' else ''
    rows = db.session.execute(
        text(f"""SELECT window_title, SUM(COALESCE(duration_s,0)) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                  {wh_clause}
                  AND LOWER(process_name) IN ('msedge','chrome','firefox','brave','opera','iexplore','safari')
                  AND window_title IS NOT NULL AND window_title != ''
                GROUP BY window_title ORDER BY total_s DESC LIMIT 200"""),
        {'aid': agent_id, **date_params}
    ).fetchall()
    # Strip browser name suffixes then take last " - " segment as site name
    strip_re = _re_site.compile(
        r'\s*[-\u2013|]\s*(Google Chrome|Microsoft\u200b?\s*Edge|Mozilla Firefox'
        r'|Brave|Opera|Work\s*[-\u2013]\s*Microsoft\u200b?\s*Edge).*$'
        r'|( and \d+ more pages.*$)', _re_site.IGNORECASE
    )
    agg: dict = {}
    for title, total_s in rows:
        t = strip_re.sub('', title or '').strip()
        parts = _re_site.split(r'\s+[-\u2013|]\s+', t)
        site = (parts[-1] if len(parts) >= 2 else parts[0]).strip()
        if not site or site.lower() in ('new tab', 'about:blank', ''):
            continue
        agg[site] = agg.get(site, 0) + int(total_s or 0)
    result = sorted([{'site': k, 'total_s': v} for k, v in agg.items()], key=lambda x: -x['total_s'])[:15]
    return jsonify({'ok': True, 'sites': result})


@bp.route('/api/rmm/eagle-eyes/fleet-app-suggestions')
@login_required
def api_eagle_fleet_app_suggestions():
    """Return top unclassified process names seen across all agents in the last 7 days."""
    rows = db.session.execute(
        text(f"""
            SELECT LOWER(e.process_name) AS process_name,
                   COUNT(DISTINCT e.agent_id) AS agent_count,
                   SUM(e.duration_s) AS total_s
            FROM rmm_eagle_event e
            WHERE e.captured_at > NOW() - INTERVAL '7 days'
              AND e.process_name IS NOT NULL AND e.process_name != ''
              {_EAGLE_SYSTEM_EXCL}
              AND LOWER(e.process_name) NOT IN (
                  SELECT LOWER(process_pattern)
                  FROM rmm_eagle_app_class
                  WHERE process_pattern IS NOT NULL
              )
            GROUP BY LOWER(e.process_name)
            ORDER BY agent_count DESC, total_s DESC
            LIMIT 40
        """)
    ).fetchall()
    result = [{'process_name': r[0], 'agent_count': int(r[1] or 0), 'total_s': int(r[2] or 0)} for r in rows]
    return jsonify(ok=True, suggestions=result)


@bp.route('/api/rmm/eagle-eyes/<agent_id>/screenshots')
@login_required
def api_rmm_eagle_screenshots(agent_id):
    """Return Eagle Eyes screenshots metadata (no image data) for the gallery."""
    date_clause, date_params = _eagle_date_params(default_days=7)
    limit = int(request.args.get('limit', 200))
    rows = db.session.execute(
        text(f"""SELECT id, captured_at, width, height, image_format
                FROM rmm_screenshot
                WHERE agent_id = :aid AND source = 'eagle' AND {date_clause}
                ORDER BY id DESC LIMIT :lim"""),
        {'aid': agent_id, 'lim': limit, **date_params}
    ).fetchall()
    shots = [{'id': r[0], 'time': _dt_iso(r[1]), 'width': r[2], 'height': r[3], 'format': r[4]} for r in rows]
    return jsonify({'ok': True, 'screenshots': shots})


@bp.route('/api/rmm/eagle-eyes/screenshot/<int:shot_id>')
@login_required
def api_rmm_eagle_screenshot_image(shot_id):
    """Return a single Eagle Eyes screenshot including the base64 image."""
    import base64 as _b64, os as _os
    row = db.session.execute(
        text("SELECT agent_id, image_b64, image_format, width, height, captured_at, file_path FROM rmm_screenshot WHERE id = :id"),
        {'id': shot_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    b64 = row[1]
    if not b64 and row[6] and _os.path.exists(row[6]):
        with open(row[6], 'rb') as fh:
            b64 = _b64.b64encode(fh.read()).decode()
    return jsonify({'ok': True, 'screenshot': {
        'id': shot_id, 'agent_id': row[0], 'data': b64,
        'format': row[2], 'width': row[3], 'height': row[4], 'time': _dt_iso(row[5]),
    }})


@bp.route('/api/rmm/eagle-eyes/screenshot/<int:shot_id>/download')
@login_required
def api_rmm_eagle_screenshot_download(shot_id):
    """Download a screenshot as an image file attachment."""
    import base64 as _b64, io, os as _os
    from flask import send_file
    row = db.session.execute(
        text("SELECT agent_id, image_b64, image_format, captured_at, file_path FROM rmm_screenshot WHERE id = :id"),
        {'id': shot_id}
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    agent_id, b64_data, fmt, captured_at, file_path = row
    ts = (captured_at or 'unknown').replace(':', '').replace(' ', '_').replace('T', '_')
    fname = f"{agent_id}_{ts}.{fmt or 'jpeg'}"
    if file_path and _os.path.exists(file_path):
        return send_file(file_path, mimetype=f'image/{fmt or "jpeg"}',
                         as_attachment=True, download_name=fname)
    if b64_data:
        buf = io.BytesIO(_b64.b64decode(b64_data))
        return send_file(buf, mimetype=f'image/{fmt or "jpeg"}',
                         as_attachment=True, download_name=fname)
    return jsonify({'ok': False, 'error': 'Image data not available'}), 404


@bp.route('/rmm/eagle-eyes/<agent_id>')
@login_required
@eagle_eyes_required
def rmm_eagle_eyes_dashboard(agent_id):
    """Eagle Eyes dashboard page for a specific agent."""
    row = db.session.execute(
        text("""SELECT ra.asset_id, COALESCE(a.name, ra.agent_id)
                FROM rmm_agent ra
                LEFT JOIN asset a ON ra.asset_id = a.id
                WHERE ra.agent_id ILIKE :aid"""),
        {'aid': agent_id}
    ).fetchone()
    hostname     = row[1] if row else agent_id
    asset_id_num = row[0] if row else None
    # Get timezone offset from the most recent event's stored UTC offset.
    # This is the only reliable source — no telemetry string parsing needed.
    tz_offset_h = -6.0  # MDT default (server timezone)
    recent_ev = db.session.execute(
        text("SELECT captured_at FROM rmm_eagle_event WHERE agent_id = :aid ORDER BY captured_at DESC LIMIT 1"),
        {'aid': agent_id}
    ).fetchone()
    if recent_ev and recent_ev[0] and recent_ev[0].utcoffset() is not None:
        tz_offset_h = recent_ev[0].utcoffset().total_seconds() / 3600
    return render_template('eagle_eyes.html', agent_id=agent_id, hostname=hostname,
                           asset_id_num=asset_id_num,
                           tz_offset_h=tz_offset_h)


@bp.route('/api/rmm/eagle-eyes/<agent_id>/current')
@login_required
def api_eagle_current(agent_id):
    try:
        row = db.session.execute(
            text("SELECT process_name, window_title, idle_s, is_idle, captured_at FROM rmm_eagle_current WHERE agent_id = :aid"),
            {"aid": agent_id}
        ).mappings().fetchone()
        if row:
            c = dict(row)
            dt = c.get('captured_at')
            c['captured_at'] = _dt_iso(dt)
            # Pass DST-aware Mountain Time offset so JS keeps agentTzOffsetH correct
            try:
                tz_h = db.session.execute(
                    text("SELECT EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'America/Denver' - NOW() AT TIME ZONE 'UTC'))/3600")
                ).scalar()
                c['tz_offset_h'] = float(tz_h) if tz_h is not None else -6.0
            except Exception:
                c['tz_offset_h'] = -6.0
            return jsonify(ok=True, current=c)
        return jsonify(ok=True, current=None)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/<agent_id>/focus-sessions')
@login_required
def api_eagle_focus_sessions(agent_id):
    date_clause, date_params = _eagle_date_params(default_days=7)
    try:
        rows = db.session.execute(
            text(f"""
                SELECT process_name, window_title, duration_s, captured_at
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND {date_clause}
                ORDER BY captured_at
            """),
            {"aid": agent_id, **date_params}
        ).mappings().fetchall()
        # Group consecutive events on the same process into focus sessions
        sessions = []
        FOCUS_MIN_S = 600   # only surface sessions ≥ 10 min
        BREAK_S     = 120   # gap ≥ 2 min breaks the session
        if rows:
            cur_proc  = rows[0]['process_name']
            cur_title = rows[0]['window_title']
            cur_start = rows[0]['captured_at']
            cur_dur   = rows[0]['duration_s'] or 0
            for r in rows[1:]:
                if r['process_name'] == cur_proc:
                    cur_dur += r['duration_s'] or 0
                else:
                    if cur_dur >= FOCUS_MIN_S:
                        sessions.append({'process_name': cur_proc, 'window_title': cur_title,
                                         'started_at': _dt_iso(cur_start), 'duration_s': cur_dur})
                    cur_proc  = r['process_name']
                    cur_title = r['window_title']
                    cur_start = r['captured_at']
                    cur_dur   = r['duration_s'] or 0
            if cur_dur >= FOCUS_MIN_S:
                sessions.append({'process_name': cur_proc, 'window_title': cur_title,
                                 'started_at': _dt_iso(cur_start), 'duration_s': cur_dur})
        sessions.sort(key=lambda s: s['duration_s'], reverse=True)
        return jsonify(ok=True, sessions=sessions[:50])
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/app-classifications', methods=['GET', 'POST'])
@login_required
def api_eagle_app_classifications():
    if request.method == 'GET':
        try:
            agent_id_filter = (request.args.get('agent_id') or '').strip() or None
            if agent_id_filter:
                # Return global app rules + this agent's site rules + global site rules
                rows = db.session.execute(
                    text("""
                        SELECT id, process_pattern, label, productivity, created_at,
                               window_title_pattern, agent_id
                        FROM rmm_eagle_app_class
                        WHERE window_title_pattern IS NULL
                           OR (window_title_pattern IS NOT NULL AND agent_id = :aid)
                           OR (window_title_pattern IS NOT NULL AND agent_id IS NULL)
                        ORDER BY COALESCE(window_title_pattern, process_pattern)
                    """),
                    {'aid': agent_id_filter}
                ).mappings().fetchall()
            else:
                rows = db.session.execute(
                    text("SELECT id, process_pattern, label, productivity, created_at, window_title_pattern, agent_id FROM rmm_eagle_app_class ORDER BY COALESCE(window_title_pattern, process_pattern)")
                ).mappings().fetchall()
            return jsonify(ok=True, classifications=[dict(r) for r in rows])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    # POST — add or update
    data = request.get_json() or {}
    pattern    = (data.get('process_pattern') or '').strip().lower() or None
    label      = (data.get('label') or '').strip()
    prod       = (data.get('productivity') or 'neutral').strip()
    window_pat = (data.get('window_title_pattern') or '').strip().lower() or None
    agent_id   = (data.get('agent_id') or '').strip() or None
    if not pattern and not window_pat:
        return jsonify(ok=False, error='process_pattern or window_title_pattern required')
    if prod not in ('productive', 'unproductive', 'neutral'):
        return jsonify(ok=False, error='Invalid productivity value')
    try:
        if window_pat:
            if agent_id:
                # Per-agent site rule: delete existing then insert
                db.session.execute(text("""
                    DELETE FROM rmm_eagle_app_class
                    WHERE window_title_pattern = :wp AND agent_id = :aid
                """), {'wp': window_pat, 'aid': agent_id})
                db.session.execute(text("""
                    INSERT INTO rmm_eagle_app_class
                           (process_pattern, label, productivity, created_at, window_title_pattern, agent_id)
                    VALUES (:p, :l, :pr, :ca, :wp, :aid)
                """), {'p': pattern, 'l': label, 'pr': prod, 'ca': datetime.utcnow().isoformat(),
                       'wp': window_pat, 'aid': agent_id})
            else:
                # Global site rule — unique key is window_title_pattern where agent_id IS NULL
                db.session.execute(text("""
                    INSERT INTO rmm_eagle_app_class
                           (process_pattern, label, productivity, created_at, window_title_pattern)
                    VALUES (:p, :l, :pr, :ca, :wp)
                    ON CONFLICT(window_title_pattern)
                    WHERE window_title_pattern IS NOT NULL AND agent_id IS NULL
                    DO UPDATE SET label=excluded.label, productivity=excluded.productivity,
                                  process_pattern=excluded.process_pattern
                """), {'p': pattern, 'l': label, 'pr': prod, 'ca': datetime.utcnow().isoformat(), 'wp': window_pat})
        else:
            # Process rule — unique key is process_pattern
            db.session.execute(text("""
                INSERT INTO rmm_eagle_app_class (process_pattern, label, productivity, created_at, window_title_pattern)
                VALUES (:p, :l, :pr, :ca, NULL)
                ON CONFLICT(process_pattern) DO UPDATE SET label=excluded.label, productivity=excluded.productivity
            """), {'p': pattern, 'l': label, 'pr': prod, 'ca': datetime.utcnow().isoformat()})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/app-classifications/<int:cid>', methods=['DELETE'])
@login_required
def api_eagle_app_class_delete(cid):
    try:
        db.session.execute(text("DELETE FROM rmm_eagle_app_class WHERE id = :id"), {'id': cid})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/alerts', methods=['GET', 'POST'])
@login_required
def api_eagle_alerts():
    if request.method == 'GET':
        try:
            rows = db.session.execute(
                text("SELECT id, agent_id, alert_type, threshold, process_pattern, email_notify, enabled, last_fired_at, created_at FROM rmm_eagle_alert_rule ORDER BY id DESC")
            ).mappings().fetchall()
            logs = db.session.execute(
                text("SELECT rule_id, agent_id, message, fired_at FROM rmm_eagle_alert_log ORDER BY fired_at DESC LIMIT 50")
            ).mappings().fetchall()
            return jsonify(ok=True, rules=[dict(r) for r in rows], log=[dict(l) for l in logs])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    alert_type = (data.get('alert_type') or '').strip()
    if alert_type not in ('productivity_below','app_used','idle_over','unproductive_app'):
        return jsonify(ok=False, error='Invalid alert_type')
    try:
        db.session.execute(text("""
            INSERT INTO rmm_eagle_alert_rule (agent_id, alert_type, threshold, process_pattern, email_notify, enabled, created_at)
            VALUES (:aid, :at, :th, :pp, :en, 1, :ca)
        """), {
            'aid': data.get('agent_id') or None,
            'at':  alert_type,
            'th':  data.get('threshold') or None,
            'pp':  (data.get('process_pattern') or '').strip().lower() or None,
            'en':  1 if data.get('email_notify', True) else 0,
            'ca':  datetime.utcnow().isoformat()
        })
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/alerts/<int:rid>', methods=['PUT', 'DELETE'])
@login_required
def api_eagle_alert_rule(rid):
    if request.method == 'DELETE':
        try:
            db.session.execute(text("DELETE FROM rmm_eagle_alert_rule WHERE id = :id"), {'id': rid})
            db.session.commit()
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    try:
        db.session.execute(text("""
            UPDATE rmm_eagle_alert_rule SET
              enabled=:en, threshold=:th, email_notify=:email
            WHERE id=:id
        """), {'en': 1 if data.get('enabled',True) else 0, 'th': data.get('threshold'), 'email': 1 if data.get('email_notify',True) else 0, 'id': rid})
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/api/rmm/eagle-eyes/report-schedules', methods=['GET', 'POST', 'DELETE'])
@login_required
def api_eagle_report_schedules():
    if request.method == 'GET':
        try:
            rows = db.session.execute(
                text("SELECT id, agent_id, frequency, day_of_week, send_time, email_to, last_sent_at, enabled, created_at FROM rmm_eagle_report_schedule ORDER BY id DESC")
            ).mappings().fetchall()
            return jsonify(ok=True, schedules=[dict(r) for r in rows])
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    if request.method == 'DELETE':
        sid = request.args.get('id')
        try:
            db.session.execute(text("DELETE FROM rmm_eagle_report_schedule WHERE id = :id"), {'id': sid})
            db.session.commit()
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    data = request.get_json() or {}
    try:
        db.session.execute(text("""
            INSERT INTO rmm_eagle_report_schedule (agent_id, frequency, day_of_week, send_time, email_to, enabled, created_at)
            VALUES (:aid, :freq, :dow, :st, :email, 1, :ca)
        """), {
            'aid':   data.get('agent_id') or None,
            'freq':  data.get('frequency','weekly'),
            'dow':   data.get('day_of_week', 1),
            'st':    data.get('send_time','08:00'),
            'email': data.get('email_to',''),
            'ca':    datetime.utcnow().isoformat()
        })
        db.session.commit()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/rmm/eagle-eyes')
@login_required
@eagle_eyes_required
def rmm_eagle_eyes_fleet():
    """Fleet-wide Eagle Eyes dashboard — all monitored devices."""
    return render_template('eagle_eyes_fleet.html')


@bp.route('/api/rmm/eagle-eyes/fleet')
@login_required
@eagle_eyes_required
def api_eagle_fleet():
    """Return all eagle-eyes-enabled agents with live + daily stats."""
    try:
        # All enabled agents with telemetry + current app
        agents_q = db.session.execute(text("""
            SELECT
                ec.agent_id,
                COALESCE(t.hostname, ec.agent_id)       AS hostname,
                COALESCE(t.logged_in_user, '')           AS logged_in_user,
                cur.process_name                         AS current_app,
                cur.captured_at                          AS last_event,
                ra.last_seen_at,
                ec.screenshots_enabled
            FROM rmm_eagle_config ec
            LEFT JOIN rmm_telemetry t   ON t.agent_id  = ec.agent_id
            LEFT JOIN rmm_eagle_current cur ON cur.agent_id = ec.agent_id
            LEFT JOIN rmm_agent ra      ON ra.agent_id = ec.agent_id
            WHERE ec.enabled = true
            ORDER BY COALESCE(t.logged_in_user, ec.agent_id)
        """)).mappings().fetchall()

        # Filter out excluded agents for non-admins
        if current_user.role != 'admin':
            excluded_ids = {r['agent_id'] for r in db.session.execute(text(
                "SELECT agent_id FROM eagle_eyes_exclusions"
            )).mappings().fetchall()}
            agents_q = [r for r in agents_q if r['agent_id'] not in excluded_ids]

        # Today's active seconds per agent (Mountain Time day)
        today_q = db.session.execute(text(f"""
            SELECT agent_id, SUM(duration_s) AS today_s
            FROM rmm_eagle_event
            WHERE CAST(captured_at AT TIME ZONE 'America/Denver' AS DATE)
                  = CAST(NOW() AT TIME ZONE 'America/Denver' AS DATE)
            {_EAGLE_SYSTEM_EXCL}
            GROUP BY agent_id
        """)).mappings().fetchall()
        today_map = {r['agent_id']: int(r['today_s'] or 0) for r in today_q}

        # Top app today per agent
        top_q = db.session.execute(text(f"""
            SELECT DISTINCT ON (agent_id)
                agent_id, process_name, SUM(duration_s) AS total_s
            FROM rmm_eagle_event
            WHERE CAST(captured_at AT TIME ZONE 'America/Denver' AS DATE)
                  = CAST(NOW() AT TIME ZONE 'America/Denver' AS DATE)
            {_EAGLE_SYSTEM_EXCL}
            GROUP BY agent_id, process_name
            ORDER BY agent_id, total_s DESC
        """)).mappings().fetchall()
        top_map = {r['agent_id']: r['process_name'] for r in top_q}

        now_utc = datetime.utcnow()
        result = []
        for a in agents_q:
            aid = a['agent_id']
            last_seen = a['last_seen_at']
            last_event = a['last_event']
            online = False
            if last_seen:
                if hasattr(last_seen, 'tzinfo') and last_seen.tzinfo:
                    from datetime import timezone as _tz
                    diff = (datetime.now(_tz.utc) - last_seen).total_seconds()
                else:
                    diff = (now_utc - last_seen).total_seconds()
                online = diff < 300
            result.append({
                'agent_id':     aid,
                'hostname':     a['hostname'],
                'user':         a['logged_in_user'],
                'current_app':  a['current_app'] or '',
                'last_event':   _dt_iso(last_event),
                'last_seen':    _dt_iso(last_seen),
                'online':              online,
                'today_s':             today_map.get(aid, 0),
                'top_app':             top_map.get(aid, ''),
                'screenshots_enabled': bool(a['screenshots_enabled']),
            })
        total_today_s = sum(r['today_s'] for r in result)
        online_count  = sum(1 for r in result if r['online'])
        return jsonify(ok=True, agents=result,
                       total=len(result), online=online_count,
                       total_today_s=total_today_s)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@bp.route('/rmm/eagle-eyes/compare')
@login_required
@eagle_eyes_required
def rmm_eagle_compare():
    agents = db.session.execute(
        text("""
            SELECT ec.agent_id, COALESCE(NULLIF(t.hostname,''), ec.agent_id) as hostname
                  FROM rmm_eagle_config ec
            LEFT JOIN rmm_telemetry t ON t.agent_id = ec.agent_id
            WHERE ec.enabled = true
              AND ec.agent_id NOT IN (SELECT agent_id FROM eagle_eyes_exclusions)
        """)
    ).mappings().fetchall()
    return render_template('compare_agents.html', agents=[dict(a) for a in agents])


@bp.route('/api/rmm/eagle-eyes/compare-data')
@login_required
@eagle_eyes_required
def api_eagle_compare_data():
    agent_ids = request.args.get('agents','').split(',')
    agent_ids = [a.strip() for a in agent_ids if a.strip()]
    days      = int(request.args.get('days', 7))
    if not agent_ids:
        return jsonify(ok=False, error='No agents specified')
    # Strip out any excluded agents (defence-in-depth)
    excluded = {r[0] for r in db.session.execute(text('SELECT agent_id FROM eagle_eyes_exclusions')).fetchall()}
    agent_ids = [a for a in agent_ids if a not in excluded]
    if not agent_ids:
        return jsonify(ok=False, error='No agents specified')
    results = {}
    for aid in agent_ids:
        try:
            summary = db.session.execute(text(f"""
                SELECT process_name, SUM(duration_s) as total_s, COUNT(*) as events
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND captured_at >= NOW() - INTERVAL '{days} days'
                {_EAGLE_SYSTEM_EXCL}
                GROUP BY process_name ORDER BY total_s DESC LIMIT 10
            """), {'aid': aid}).mappings().fetchall()
            daily = db.session.execute(text(f"""
                SELECT CAST(captured_at AS DATE) as day, SUM(duration_s) as total_s
                FROM rmm_eagle_event
                WHERE agent_id = :aid AND captured_at >= NOW() - INTERVAL '{days} days'
                {_EAGLE_SYSTEM_EXCL}
                GROUP BY day ORDER BY day
            """), {'aid': aid}).mappings().fetchall()
            hostname = db.session.execute(
                text("SELECT hostname FROM rmm_telemetry WHERE agent_id = :aid LIMIT 1"), {'aid': aid}
            ).scalar()
            hostname = hostname if hostname else aid  # NULLIF-style: empty string falls through
            results[aid] = {
                'hostname': hostname,
                'summary':  [{'process_name': r['process_name'], 'total_s': int(r['total_s'] or 0), 'events': r['events']} for r in summary],
                'daily':    [{'day': str(r['day']), 'total_s': int(r['total_s'] or 0)} for r in daily],
                'total_s':  sum(int(r['total_s'] or 0) for r in summary),
            }
        except Exception as e:
            results[aid] = {'hostname': aid, 'error': str(e)}
    return jsonify(ok=True, results=results, days=days)


@bp.route('/api/rmm/eagle-eyes/<agent_id>/gantt')
@login_required
def api_eagle_gantt(agent_id):
    """Return events for a specific day as a gantt-ready list."""
    day = request.args.get('day')  # YYYY-MM-DD
    if not day:
        from datetime import date
        day = date.today().isoformat()
    try:
        # Compute DST-aware offset for Mountain Time so returned timestamps carry correct offset
        tz_h_row = db.session.execute(
            text("SELECT EXTRACT(EPOCH FROM (NOW() AT TIME ZONE 'America/Denver' - NOW() AT TIME ZONE 'UTC'))/3600")
        ).scalar()
        tz_offset_h = float(tz_h_row) if tz_h_row is not None else -6.0
        tz_suffix = f'-{abs(int(tz_offset_h)):02d}:00' if tz_offset_h < 0 else f'+{int(tz_offset_h):02d}:00'
        rows = db.session.execute(text("""
            SELECT process_name, window_title, duration_s, idle_s,
                   to_char(captured_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD"T"HH24:MI:SS') AS local_ts
            FROM rmm_eagle_event
            WHERE agent_id = :aid
              AND CAST(captured_at AT TIME ZONE 'America/Denver' AS DATE) = CAST(:day AS DATE)
            ORDER BY captured_at
        """), {'aid': agent_id, 'day': day}).mappings().fetchall()
        events = [{'process_name': r['process_name'], 'window_title': r['window_title'],
                   'duration_s': r['duration_s'], 'idle_s': r['idle_s'],
                   'captured_at': r['local_ts'] + tz_suffix} for r in rows]
        return jsonify(ok=True, day=day, events=events, tz_offset_h=tz_offset_h)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


