"""HR Work-Hours report — the CONSUMER side of the always-on work-hours meter.

The data (table rmm_work_hours_daily) and the gateway ingest are owned by the RMM
subsystem. This module only READS that table and renders a per-employee, per-day
report gated by the work-hours access model (allowlist + manager map) defined in
utils.py. Routes register on the shared 'rmm' blueprint (imported at the bottom of
blueprints/rmm.py), so endpoint names are 'rmm.*'.
"""
from datetime import datetime, date, timedelta

from flask import render_template, request
from flask_login import current_user, login_required
from sqlalchemy import text

from extensions import db
from models import _log_audit
from utils import (
    workhours_access_required,
    workhours_scope_employee_ids,
    workhours_excluded_regex,
)

from blueprints.rmm import bp


def _fmt_hm(seconds):
    """Seconds -> 'H:MM' (e.g. 32400 -> '9:00'). None/negative -> '0:00'."""
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        s = 0
    if s < 0:
        s = 0
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}"


def _parse_range():
    """Resolve the from/to date filter. Default = last 7 days (inclusive)."""
    today = date.today()
    default_from = today - timedelta(days=6)

    def _pd(val, fallback):
        if not val:
            return fallback
        try:
            return datetime.strptime(val.strip(), '%Y-%m-%d').date()
        except (ValueError, AttributeError):
            return fallback

    d_from = _pd(request.args.get('from'), default_from)
    d_to = _pd(request.args.get('to'), today)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return d_from, d_to


@bp.route('/rmm/workhours')  # no-hyphen alias (404-guard); canonical is /rmm/work-hours below
@bp.route('/rmm/work-hours')
@login_required
@workhours_access_required
def rmm_work_hours():
    """Per-employee, per-day PC on-time + active-time report (HR compliance)."""
    d_from, d_to = _parse_range()

    # Row scope: None = all employees; [] or [ids] = restricted (manager map).
    scope_ids = workhours_scope_employee_ids()

    rows = []
    summary = {'employees': 0, 'days': 0, 'on_seconds': 0, 'active_seconds': 0}
    error = None

    # A mapped manager with an empty employee list sees nothing — skip the query.
    scoped_empty = (scope_ids is not None and len(scope_ids) == 0)

    if not scoped_empty:
        # employee_id may be null on some rows -> fall back to the linked asset's owner.
        # Resolve a single effective employee_id and name via COALESCE, then join person.
        params = {'d_from': d_from, 'd_to': d_to}
        scope_clause = ""
        if scope_ids is not None:
            # Restrict on the *effective* employee id (row's own or the asset's owner).
            scope_clause = "AND COALESCE(w.employee_id, a.employee_id) = ANY(:scope_ids)"
            params['scope_ids'] = list(scope_ids)

        # Device-name exclusion (lab/build/test/kiosk/prod boxes, not employee
        # workstations). Whole-string, case-insensitive glob match against the device
        # name (asset.name else agent_id). Applied INSIDE the single detail query so the
        # Python-side summary aggregation (built from these rows) stays consistent.
        excl_clause = ""
        excl_regex = workhours_excluded_regex()
        if excl_regex:
            # COALESCE(..., '') so a NULL device name never yields NULL (which would be
            # dropped by the AND) — mirrors the server-class filter's NULL-safe guard. An
            # empty string never matches an anchored ^(...)$ glob, so a NULL-named row is
            # KEPT, not silently excluded.
            excl_clause = "AND COALESCE(a.name, w.agent_id, '') !~* :excl_regex"
            params['excl_regex'] = excl_regex

        sql = text(f"""
            SELECT
                COALESCE(w.employee_id, a.employee_id)                 AS emp_id,
                COALESCE(e.name, a.name, w.agent_id)                   AS emp_name,
                COALESCE(a.name, w.agent_id)                           AS device_name,
                w.local_date                                            AS local_date,
                w.on_seconds                                            AS on_seconds,
                w.active_seconds                                        AS active_seconds
            FROM rmm_work_hours_daily w
            LEFT JOIN asset a    ON a.id = w.asset_id
            LEFT JOIN employee e ON e.id = COALESCE(w.employee_id, a.employee_id)
            WHERE w.local_date >= :d_from AND w.local_date <= :d_to
            -- Work-hours is workstations-only: exclude server-class assets.
            -- Same criteria Eagle Eyes uses to hide servers (_server_class_agent_ids):
            -- asset.device_type ILIKE '%server%' OR asset.category = 'Server'.
            -- COALESCE keeps LEFT-JOIN rows with no/unmatched asset (NULLs) visible —
            -- only positively-identified servers are dropped.
            AND NOT (COALESCE(a.device_type ILIKE '%server%', FALSE)
                     OR COALESCE(a.category = 'Server', FALSE))
            {excl_clause}
            {scope_clause}
            ORDER BY emp_name ASC, device_name ASC, w.local_date ASC
        """)
        try:
            result = db.session.execute(sql, params).mappings().fetchall()
            seen_emps = set()
            for r in result:
                rows.append({
                    'emp_id':      r['emp_id'],
                    'emp_name':    r['emp_name'] or '(unknown)',
                    'device_name': r['device_name'] or '(unknown)',
                    'date':        r['local_date'].isoformat() if r['local_date'] else '',
                    'on_seconds':  int(r['on_seconds'] or 0),
                    'active_seconds': int(r['active_seconds'] or 0),
                    'on_hm':       _fmt_hm(r['on_seconds']),
                    'active_hm':   _fmt_hm(r['active_seconds']),
                })
                if r['emp_id'] is not None:
                    seen_emps.add(r['emp_id'])
                else:
                    seen_emps.add(('name', r['emp_name']))
                summary['on_seconds'] += int(r['on_seconds'] or 0)
                summary['active_seconds'] += int(r['active_seconds'] or 0)
            summary['employees'] = len(seen_emps)
            summary['days'] = len(rows)
        except Exception as e:
            error = str(e)

    # Audit: viewing compliance data is logged (who + range + scope).
    try:
        _log_audit('workhours_view', 0, 'workhours.report_view', {
            'from': d_from.isoformat(),
            'to': d_to.isoformat(),
            'scoped': scope_ids is not None,
            'rows': len(rows),
        })
        db.session.commit()
    except Exception:
        db.session.rollback()

    return render_template(
        'work_hours.html',
        rows=rows,
        summary={
            'employees': summary['employees'],
            'days': summary['days'],
            'on_hm': _fmt_hm(summary['on_seconds']),
            'active_hm': _fmt_hm(summary['active_seconds']),
        },
        d_from=d_from.isoformat(),
        d_to=d_to.isoformat(),
        scoped=(scope_ids is not None),
        error=error,
    )
