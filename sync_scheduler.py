"""Background sync scheduler.

Runs periodic sync jobs (e.g., Intune asset sync) in-process.

NOTE: In a Gunicorn multi-worker deployment this module may be imported
per worker. Jobs are therefore guarded by a cross-process file lock to
avoid duplicate work.
"""

import fcntl
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


SYNC_LOCK_PATH = os.environ.get('TRACKER_SYNC_LOCK_PATH', '/tmp/tracker_sync_jobs.lock')
INTUNE_INTERVAL_MINUTES = int(os.environ.get('INTUNE_ASSET_SYNC_INTERVAL_MINUTES', '15'))
DISABLE_INTUNE_ASSET_SYNC = os.environ.get('DISABLE_INTUNE_ASSET_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

M365_PHOTO_LOCK_PATH = os.environ.get('TRACKER_M365_PHOTO_LOCK_PATH', '/tmp/tracker_m365_employee_photos.lock')
M365_PHOTO_REFRESH_INTERVAL_HOURS = int(os.environ.get('M365_EMPLOYEE_PHOTO_REFRESH_INTERVAL_HOURS', '24'))
DISABLE_M365_EMPLOYEE_PHOTO_REFRESH = os.environ.get('DISABLE_M365_EMPLOYEE_PHOTO_REFRESH', '').strip() in ('1', 'true', 'yes', 'on')

UNIFI_SYNC_LOCK_PATH = os.environ.get('TRACKER_UNIFI_SYNC_LOCK_PATH', '/tmp/tracker_unifi_sync.lock')
UNIFI_SYNC_INTERVAL_MINUTES = int(os.environ.get('UNIFI_SYNC_INTERVAL_MINUTES', '15'))
DISABLE_UNIFI_SYNC = os.environ.get('DISABLE_UNIFI_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

# On-prem AD computer sync (AD = source of truth for assets). Daily.
AD_ASSET_SYNC_LOCK_PATH = os.environ.get('TRACKER_AD_ASSET_SYNC_LOCK_PATH', '/tmp/tracker_ad_asset_sync.lock')
AD_ASSET_SYNC_INTERVAL_HOURS = int(os.environ.get('AD_ASSET_SYNC_INTERVAL_HOURS', '24'))
DISABLE_AD_ASSET_SYNC = os.environ.get('DISABLE_AD_ASSET_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

# Employee offboard sweep: park an offboard (1-click review) for disabled-but-visible users. Daily.
OFFBOARD_SWEEP_LOCK_PATH = os.environ.get('TRACKER_OFFBOARD_SWEEP_LOCK_PATH', '/tmp/tracker_offboard_sweep.lock')
OFFBOARD_SWEEP_INTERVAL_HOURS = int(os.environ.get('OFFBOARD_SWEEP_INTERVAL_HOURS', '24'))
DISABLE_OFFBOARD_SWEEP = os.environ.get('DISABLE_OFFBOARD_SWEEP', '').strip() in ('1', 'true', 'yes', 'on')

# AD-deletion sweep: after a retention window (default 30 days) past offboarded_at, PARK a
# permanent AD-delete for 1-click human confirm. Parks only — never deletes directly. Daily.
AD_DELETE_SWEEP_LOCK_PATH = os.environ.get('TRACKER_AD_DELETE_SWEEP_LOCK_PATH', '/tmp/tracker_ad_delete_sweep.lock')
DISABLE_AD_DELETE_SWEEP = os.environ.get('DISABLE_AD_DELETE_SWEEP', '').strip() in ('1', 'true', 'yes', 'on')

# AD/M365 employee sync (refresh ad_enabled etc.) — daily, so disables are detected. Then parks.
AD_EMPLOYEE_SYNC_LOCK_PATH = os.environ.get('TRACKER_AD_EMPLOYEE_SYNC_LOCK_PATH', '/tmp/tracker_ad_employee_sync.lock')
AD_EMPLOYEE_SYNC_INTERVAL_HOURS = int(os.environ.get('AD_EMPLOYEE_SYNC_INTERVAL_HOURS', '24'))
DISABLE_AD_EMPLOYEE_SYNC = os.environ.get('DISABLE_AD_EMPLOYEE_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

# Daily M365 employee status reconcile — refresh the authoritative M365User table from Graph,
# link new rows, then reconcile employee.m365_account_enabled / m365_validated_at off it. This
# is what keeps the employee page's M365 status from going stale org-wide (incl. offboarded
# users who are no longer in AD, whom the AD-driven sync never touches).
M365_EMPLOYEE_RECONCILE_LOCK_PATH = os.environ.get('TRACKER_M365_EMPLOYEE_RECONCILE_LOCK_PATH', '/tmp/tracker_m365_employee_reconcile.lock')
M365_EMPLOYEE_RECONCILE_INTERVAL_HOURS = int(os.environ.get('M365_EMPLOYEE_RECONCILE_INTERVAL_HOURS', '24'))
DISABLE_M365_EMPLOYEE_RECONCILE = os.environ.get('DISABLE_M365_EMPLOYEE_RECONCILE', '').strip() in ('1', 'true', 'yes', 'on')

DEFENDER_SYNC_LOCK_PATH = os.environ.get('TRACKER_DEFENDER_SYNC_LOCK_PATH', '/tmp/tracker_defender_vuln_sync.lock')
DEFENDER_SYNC_HOUR = int(os.environ.get('DEFENDER_SYNC_HOUR', '2'))  # 2 AM local time
DISABLE_DEFENDER_SYNC = os.environ.get('DISABLE_DEFENDER_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

VULN_EMAIL_LOCK_PATH = os.environ.get('TRACKER_VULN_EMAIL_LOCK_PATH', '/tmp/tracker_vuln_email.lock')
PATCH_REBOOT_SWEEP_LOCK_PATH = os.environ.get('TRACKER_PATCH_REBOOT_SWEEP_LOCK_PATH', '/tmp/tracker_patch_reboot_sweep.lock')
VULN_EMAIL_HOUR = int(os.environ.get('VULN_EMAIL_HOUR', '7'))  # 7 AM local time (after 2 AM Defender sync)
DISABLE_VULN_EMAIL = os.environ.get('DISABLE_VULN_EMAIL', '').strip() in ('1', 'true', 'yes', 'on')

PROXMOX_SYNC_LOCK_PATH = os.environ.get('TRACKER_PROXMOX_SYNC_LOCK_PATH', '/tmp/tracker_proxmox_sync.lock')
PROXMOX_SYNC_INTERVAL_MINUTES = int(os.environ.get('PROXMOX_SYNC_INTERVAL_MINUTES', '15'))
DISABLE_PROXMOX_SYNC = os.environ.get('DISABLE_PROXMOX_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

# Agent online-state reconcile: make asset.online_state a reliable mirror of the
# real live signal (rmm_agent.last_seen_at within 5 min = Online, else Offline),
# so WS flaps can't false-Offline a live box AND genuinely-stale boxes still go
# Offline. Agent-backed assets ONLY — never touches UniFi/Intune-only assets.
AGENT_ONLINE_RECONCILE_LOCK_PATH = os.environ.get('TRACKER_AGENT_ONLINE_RECONCILE_LOCK_PATH', '/tmp/tracker_agent_online_reconcile.lock')
AGENT_ONLINE_RECONCILE_INTERVAL_SECONDS = int(os.environ.get('AGENT_ONLINE_RECONCILE_INTERVAL_SECONDS', '60'))
DISABLE_AGENT_ONLINE_RECONCILE = os.environ.get('DISABLE_AGENT_ONLINE_RECONCILE', '').strip() in ('1', 'true', 'yes', 'on')

BACKUP_SCHEDULER_LOCK_PATH = os.environ.get('TRACKER_BACKUP_SCHEDULER_LOCK_PATH', '/tmp/tracker_backup_scheduler.lock')
BACKUP_SCHEDULER_INTERVAL_MINUTES = int(os.environ.get('BACKUP_SCHEDULER_INTERVAL_MINUTES', '60'))
BACKUP_INCREMENTAL_INTERVAL_HOURS = int(os.environ.get('BACKUP_INCREMENTAL_INTERVAL_HOURS', '24'))
DISABLE_BACKUP_SCHEDULER = os.environ.get('DISABLE_BACKUP_SCHEDULER', '').strip() in ('1', 'true', 'yes', 'on')

QUARANTINE_SYNC_LOCK_PATH = os.environ.get('TRACKER_QUARANTINE_SYNC_LOCK_PATH', '/tmp/tracker_quarantine_sync.lock')
METRICS_SNAPSHOT_LOCK_PATH = os.environ.get('TRACKER_METRICS_SNAPSHOT_LOCK_PATH', '/tmp/tracker_metrics_snapshot.lock')
INCIDENT_SCAN_LOCK_PATH = os.environ.get('TRACKER_INCIDENT_SCAN_LOCK_PATH', '/tmp/tracker_incident_scan.lock')
METRICS_HISTORY_RETENTION_DAYS = int(os.environ.get('METRICS_HISTORY_RETENTION_DAYS', '90'))
QUARANTINE_SYNC_INTERVAL_MINUTES = int(os.environ.get('QUARANTINE_SYNC_INTERVAL_MINUTES', '15'))
DISABLE_QUARANTINE_SYNC = os.environ.get('DISABLE_QUARANTINE_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

# Throttle: max new *initial* (never-backed-up) full jobs triggered per scheduler cycle.
# Prevents a fresh bulk policy assignment from flooding the NAS.
BACKUP_MAX_INITIAL_PER_CYCLE = int(os.environ.get('BACKUP_MAX_INITIAL_PER_CYCLE', '3'))
# Throttle: max full backup jobs (initial OR scheduled) allowed to be in 'running'
# state at any one time before the scheduler pauses new triggers.
BACKUP_MAX_CONCURRENT_FULL = int(os.environ.get('BACKUP_MAX_CONCURRENT_FULL', '5'))
# Seconds to wait between successive backup triggers within a single scheduler cycle.
BACKUP_TRIGGER_STAGGER_SECONDS = int(os.environ.get('BACKUP_TRIGGER_STAGGER_SECONDS', '30'))

_scheduler = None


@contextmanager
def _file_lock(path: str):
    """Non-blocking exclusive lock; yields True if acquired else False."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield True
        except BlockingIOError:
            yield False
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


def start_sync_scheduler(flask_app):
    """Start background sync scheduler (idempotent per-process)."""
    global _scheduler

    if _scheduler is not None:
        logger.info('Sync scheduler already running in this process')
        return _scheduler

    _scheduler = BackgroundScheduler()

    if not DISABLE_INTUNE_ASSET_SYNC:
        _scheduler.add_job(
            func=lambda: run_intune_asset_sync_job(flask_app),
            trigger='interval',
            minutes=max(INTUNE_INTERVAL_MINUTES, 1),
            id='intune_asset_sync',
            name='Periodic Intune asset sync',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

    if not DISABLE_M365_EMPLOYEE_PHOTO_REFRESH:
        _scheduler.add_job(
            func=lambda: run_m365_employee_photo_refresh_job(flask_app),
            trigger='interval',
            hours=max(M365_PHOTO_REFRESH_INTERVAL_HOURS, 1),
            id='m365_employee_photo_refresh',
            name='Periodic M365 employee photo refresh',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    if not DISABLE_UNIFI_SYNC:
        _scheduler.add_job(
            func=lambda: run_unifi_sync_job(flask_app),
            trigger='interval',
            minutes=max(UNIFI_SYNC_INTERVAL_MINUTES, 1),
            id='unifi_sync',
            name='Periodic UniFi device sync',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

    if not DISABLE_AD_ASSET_SYNC:
        _scheduler.add_job(
            func=lambda: run_ad_asset_sync_job(flask_app),
            trigger='interval',
            hours=max(AD_ASSET_SYNC_INTERVAL_HOURS, 1),
            id='ad_asset_sync',
            name='Daily on-prem AD computer sync (AD = source of truth)',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    if not DISABLE_AD_EMPLOYEE_SYNC:
        _scheduler.add_job(
            func=lambda: run_ad_employee_sync_job(flask_app),
            trigger='interval',
            hours=max(AD_EMPLOYEE_SYNC_INTERVAL_HOURS, 1),
            id='ad_employee_sync',
            name='Daily AD/M365 employee sync (+park newly-disabled offboards)',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    if not DISABLE_M365_EMPLOYEE_RECONCILE:
        _scheduler.add_job(
            func=lambda: run_m365_employee_reconcile_job(flask_app),
            trigger='interval',
            hours=max(M365_EMPLOYEE_RECONCILE_INTERVAL_HOURS, 1),
            id='m365_employee_reconcile',
            name='Daily M365 employee status reconcile (refresh m365_account_enabled/validated_at)',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    if not DISABLE_OFFBOARD_SWEEP:
        _scheduler.add_job(
            func=lambda: run_employee_offboard_sweep_job(flask_app),
            trigger='interval',
            hours=max(OFFBOARD_SWEEP_INTERVAL_HOURS, 1),
            id='employee_offboard_sweep',
            name='Daily disabled-employee offboard sweep (parks 1-click offboards)',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    if not DISABLE_AD_DELETE_SWEEP:
        _scheduler.add_job(
            func=lambda: run_ad_delete_sweep_job(flask_app),
            trigger='cron',
            hour=4,
            minute=30,
            id='ad_delete_sweep',
            name='Daily AD-deletion sweep (30-day offboard retention)',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )

    _scheduler.add_job(
        func=lambda: run_patch_reboot_force_sweep_job(flask_app),
        trigger='interval',
        hours=1,
        id='patch_reboot_force_sweep',
        name='Hourly patch reboot-force sweep (user-controlled grace, then force)',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    if not DISABLE_DEFENDER_SYNC:
        _scheduler.add_job(
            func=lambda: run_defender_vuln_sync_job(flask_app),
            trigger='cron',
            hour=DEFENDER_SYNC_HOUR,
            minute=0,
            id='defender_vuln_sync',
            name='Daily Defender vulnerability sync',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )

    if not DISABLE_VULN_EMAIL:
        _scheduler.add_job(
            func=lambda: run_daily_vuln_email_job(flask_app),
            trigger='cron',
            hour=VULN_EMAIL_HOUR,
            minute=0,
            id='daily_vuln_email',
            name='Daily vulnerability email digest',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )

    if not DISABLE_PROXMOX_SYNC:
        _scheduler.add_job(
            func=lambda: run_proxmox_sync_job(flask_app),
            trigger='interval',
            minutes=max(PROXMOX_SYNC_INTERVAL_MINUTES, 1),
            id='proxmox_sync',
            name='Periodic Proxmox backup/ZFS sync',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

    if not DISABLE_BACKUP_SCHEDULER:
        _scheduler.add_job(
            func=lambda: run_backup_scheduler_job(flask_app),
            trigger='interval',
            minutes=max(BACKUP_SCHEDULER_INTERVAL_MINUTES, 5),
            id='backup_scheduler',
            name='Periodic RMM backup scheduler',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

    if not DISABLE_QUARANTINE_SYNC:
        _scheduler.add_job(
            func=lambda: run_quarantine_sync_job(flask_app),
            trigger='interval',
            minutes=max(QUARANTINE_SYNC_INTERVAL_MINUTES, 1),
            id='quarantine_sync',
            name='Periodic Exchange quarantine sync',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

    _scheduler.add_job(
        func=lambda: run_auto_approve_patches_job(flask_app),
        trigger='cron',
        hour=3,
        minute=30,
        id='patch_auto_approve',
        name='Daily auto-approve patch deployment',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    _scheduler.add_job(
        func=lambda: run_metrics_snapshot_job(flask_app),
        trigger='interval',
        minutes=15,
        id='metrics_snapshot',
        name='RMM metrics time-series snapshot',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    if not DISABLE_AGENT_ONLINE_RECONCILE:
        _scheduler.add_job(
            func=lambda: run_agent_online_reconcile_job(flask_app),
            trigger='interval',
            seconds=max(AGENT_ONLINE_RECONCILE_INTERVAL_SECONDS, 15),
            id='agent_online_reconcile',
            name='Reconcile asset.online_state from rmm_agent.last_seen_at (agent-backed)',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )

    # Proactive AI Remediation: detect -> diagnose -> propose/auto-handle ->
    # verify. Fully fail-safe (incident_service.scan swallows its own errors), so
    # a bad pass can never wedge the scheduler. 10-min cadence (telemetry is
    # 5-min pull-based; no need to scan faster).
    if os.environ.get('DISABLE_INCIDENT_SCAN', '').lower() not in ('1', 'true', 'yes'):
        _scheduler.add_job(
            func=lambda: run_incident_scan_job(flask_app),
            trigger='interval',
            minutes=int(os.environ.get('INCIDENT_SCAN_INTERVAL_MINUTES', '10')),
            id='incident_scan',
            name='Proactive AI Remediation incident scan',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

    _scheduler.start()
    logger.info('Started sync scheduler')

    return _scheduler


def run_incident_scan_job(flask_app):
    """Scheduler wrapper for the Proactive AI Remediation detector/orchestrator.

    SINGLE-INSTANCE (Bug-1 primary fix): gunicorn runs multiple worker processes
    and each starts its own BackgroundScheduler, so without a cross-process lock
    every worker fires this same scan on the same tick — 5 workers => 5 concurrent
    scans, each racing the detect-or-insert and committing a duplicate open
    incident per (asset, signal). The non-blocking file lock makes exactly ONE
    worker run the pass per tick; the rest no-op. (Same pattern as every other
    job in this module.) incident_service.scan is itself fully fail-safe."""
    with _file_lock(INCIDENT_SCAN_LOCK_PATH) as acquired:
        if not acquired:
            return
        try:
            import incident_service
            incident_service.run_incident_scan_job(flask_app)
        except Exception:
            logger.exception('incident scan job failed to launch')


def run_metrics_snapshot_job(flask_app):
    """Downsample the current per-agent telemetry snapshot (rmm_telemetry) into the
    rmm_metrics_history time-series every 15 min, and prune old rows. This revives
    the metrics-history charts (the table had no writer) and builds the time-series
    that trend/anomaly detection needs — without touching the hot ingest path."""
    with _file_lock(METRICS_SNAPSHOT_LOCK_PATH) as acquired:
        if not acquired:
            return
        with flask_app.app_context():
            from extensions import db
            from sqlalchemy import text
            try:
                inserted = db.session.execute(text("""
                    INSERT INTO rmm_metrics_history (agent_id, cpu_percent, ram_percent, disk_percent, captured_at)
                    SELECT agent_id, cpu_percent, ram_percent,
                           NULLIF(disk_json::jsonb -> 0 ->> 'percent', '')::real,
                           NOW()
                    FROM rmm_telemetry
                    WHERE last_seen > NOW() - interval '20 minutes'
                      AND (cpu_percent IS NOT NULL OR ram_percent IS NOT NULL)
                """)).rowcount
                db.session.execute(
                    text("DELETE FROM rmm_metrics_history WHERE captured_at < NOW() - make_interval(days => :d)"),
                    {'d': METRICS_HISTORY_RETENTION_DAYS})
                db.session.commit()
                logger.info(f'Metrics snapshot: +{inserted} agent rows; pruned >{METRICS_HISTORY_RETENTION_DAYS}d')
            except Exception as exc:
                db.session.rollback()
                logger.error(f'Metrics snapshot error: {exc}')


def run_agent_online_reconcile_job(flask_app):
    """Self-correct asset.online_state for AGENT-BACKED assets off the real live
    signal (rmm_agent.last_seen_at, the same 5-min window the fleet view uses).

    Why this exists: offline-marking used to be purely event-driven on WebSocket
    disconnect (rmm_gateway.db.mark_agent_offline), so a flappy WS on a live,
    still-heartbeating box left online_state stuck 'Offline'. Guarding that path
    stops the false-Offline, but then a genuinely-dead box needs *something* to
    flip it Offline once its heartbeat ages out — this periodic pass is that
    something. As boxes come online / go offline the column self-corrects.

    Scope: agent-backed assets only (assets joined to an enabled rmm_agent). It
    NEVER touches UniFi/Intune-only assets, and only ever writes the connectivity
    values 'Online'/'Offline' — respecting the online_state-vs-compliance rule.
    """
    with _file_lock(AGENT_ONLINE_RECONCILE_LOCK_PATH) as acquired:
        if not acquired:
            return
        with flask_app.app_context():
            from extensions import db
            from sqlalchemy import text
            try:
                # Live: enabled agent seen within 5 min -> Online
                up = db.session.execute(text("""
                    UPDATE asset a SET online_state = 'Online'
                    FROM rmm_agent ag
                    WHERE ag.asset_id = a.id
                      AND ag.enabled = TRUE
                      AND ag.last_seen_at > NOW() - interval '5 minutes'
                      AND a.online_state IS DISTINCT FROM 'Online'
                """)).rowcount
                # Stale: enabled agent not seen for 5 min -> Offline. Scoped to
                # agent-backed assets (the FROM join), so UniFi/Intune-only assets
                # are untouched even if they were never agent-managed.
                down = db.session.execute(text("""
                    UPDATE asset a SET online_state = 'Offline'
                    FROM rmm_agent ag
                    WHERE ag.asset_id = a.id
                      AND ag.enabled = TRUE
                      AND (ag.last_seen_at IS NULL
                           OR ag.last_seen_at <= NOW() - interval '5 minutes')
                      AND a.online_state IS DISTINCT FROM 'Offline'
                """)).rowcount
                db.session.commit()
                if up or down:
                    logger.info(f'Agent online-state reconcile: +{up} Online, +{down} Offline')
            except Exception as exc:
                db.session.rollback()
                logger.error(f'Agent online-state reconcile error: {exc}')


def run_auto_approve_patches_job(flask_app):
    """Run auto-approve patch deployment for matching pending updates."""
    logger.info('Starting scheduled auto-approve patch deployment')
    with flask_app.app_context():
        try:
            from blueprints.patch_mgmt import _run_auto_approve, _cleanup_patch_jobs
            stuck, purged = _cleanup_patch_jobs()
            if stuck or purged:
                logger.info(f'Patch-job cleanup: failed-stuck={stuck} purged-old={purged}')
            deployed, skipped = _run_auto_approve()
            logger.info(f'Auto-approve patches: deployed={deployed} skipped/offline={skipped}')
        except Exception as exc:
            logger.error(f'Auto-approve patches error: {exc}')


def run_patch_reboot_force_sweep_job(flask_app):
    """Enforce the patch reboot policy: install + notify -> user-controlled grace -> force.
    Force-reboots ONLINE boxes whose reboot-bearing patch completed more than the grace
    window ago (Setting patch_reboot_grace_hours, default 24) AND that have NOT rebooted
    since (boot time still older than the patch). Sends force_reboot once per job."""
    import urllib.request as _req
    import json as _json
    from sqlalchemy import text as _text
    with _file_lock(PATCH_REBOOT_SWEEP_LOCK_PATH) as acquired:
      if not acquired:
        return  # another gunicorn worker holds the sweep lock this tick
      with flask_app.app_context():
        from app import db, Setting
        # INERT until explicitly activated: only patches that completed AFTER this
        # timestamp were installed under the notify-first policy (the user actually saw
        # the 24h notice). Without it we'd force-reboot pre-existing un-rebooted boxes
        # that never got a grace window. Unset => do nothing.
        since_row = Setting.query.filter_by(key='patch_reboot_policy_since').first()
        if not since_row or not (since_row.value or '').strip():
            return
        since = since_row.value.strip()
        grow = Setting.query.filter_by(key='patch_reboot_grace_hours').first()
        grace = int(grow.value) if grow and str(grow.value).strip().isdigit() else 24
        try:
            # DISTINCT ON (agent_id): one decision per box. Exclude servers/DCs by both
            # asset.device_type AND live telemetry os_name (DCs run Server OS).
            cands = db.session.execute(_text("""
                SELECT DISTINCT ON (pj.agent_id) pj.agent_id
                FROM rmm_patch_job pj
                JOIN rmm_agent ra ON ra.agent_id = pj.agent_id
                JOIN asset a ON a.id = ra.asset_id
                JOIN LATERAL (
                    SELECT uptime_seconds, last_seen, os_name FROM rmm_telemetry
                    WHERE agent_id = pj.agent_id
                    ORDER BY last_seen DESC NULLS LAST LIMIT 1
                ) t ON true
                WHERE pj.status='completed' AND pj.reboot_required = true
                  AND pj.completed_at >= CAST(:since AS timestamptz)
                  AND pj.completed_at < now() - make_interval(hours => :grace)
                  AND COALESCE(pj.notes,'') NOT LIKE '%%force_reboot_sent%%'
                  AND t.last_seen > now() - interval '15 minutes'
                  AND (t.last_seen - (t.uptime_seconds * interval '1 second')) < pj.completed_at
                  AND COALESCE(a.device_type,'') NOT ILIKE '%%server%%'
                  AND COALESCE(t.os_name,'')   NOT ILIKE '%%server%%'
                  AND COALESCE(a.name,'')      NOT ILIKE '%%server%%'
                  AND COALESCE(a.ad_dn,'')     NOT ILIKE '%%Domain Controllers%%'
                ORDER BY pj.agent_id, pj.completed_at DESC
            """), {'since': since, 'grace': grace}).fetchall()
        except Exception as exc:
            logger.error(f'Patch reboot-force sweep query error: {exc}')
            return
        if not cands:
            return
        gw = os.environ.get('RMM_GATEWAY_INTERNAL', 'http://127.0.0.1:8765')
        forced = 0
        for (aid,) in cands:
            try:
                req = _req.Request(
                    f"{gw}/send-msg/{aid}",
                    data=_json.dumps({'type': 'force_reboot'}).encode(),
                    headers={'Content-Type': 'application/json'}, method='POST')
                with _req.urlopen(req, timeout=10) as resp:
                    ok = _json.loads(resp.read()).get('ok')
                if ok:
                    # Mark ALL of this box's qualifying jobs so siblings don't re-trigger.
                    db.session.execute(_text(
                        "UPDATE rmm_patch_job SET notes = COALESCE(notes,'') || "
                        "' [force_reboot_sent ' || now()::text || ']', updated_at=NOW() "
                        "WHERE agent_id=:aid AND status='completed' AND reboot_required=true "
                        "AND COALESCE(notes,'') NOT LIKE '%%force_reboot_sent%%'"),
                        {'aid': aid})
                    db.session.commit()
                    forced += 1
                    logger.info(f'Patch reboot-force: force_reboot -> {aid} ({grace}h grace elapsed)')
            except Exception as exc:
                logger.warning(f'Patch reboot-force: failed for {aid}: {exc}')
        if forced:
            logger.info(f'Patch reboot-force sweep: forced {forced} box(es) after {grace}h grace')


def run_quarantine_sync_job(flask_app):
    """Run the Exchange quarantine sync with a cross-process lock."""
    with _file_lock(QUARANTINE_SYNC_LOCK_PATH) as acquired:
        if not acquired:
            logger.info('Quarantine sync skipped (lock held by another process)')
            return

        logger.info('Starting scheduled quarantine sync')
        with flask_app.app_context():
            try:
                from blueprints.quarantine import perform_quarantine_sync

                result = perform_quarantine_sync(flask_app)
                logger.info(
                    'Scheduled quarantine sync complete: added=%s updated=%s message=%s',
                    result.get('added', 0),
                    result.get('updated', 0),
                    result.get('message', ''),
                )
            except Exception:
                logger.exception('Scheduled quarantine sync crashed')


def run_intune_asset_sync_job(flask_app):
    """Run the Intune asset sync with a cross-process lock."""
    with _file_lock(SYNC_LOCK_PATH) as acquired:
        if not acquired:
            logger.info('Intune sync skipped (lock held by another process)')
            return

        started_at = datetime.now(timezone.utc)
        logger.info('Starting scheduled Intune asset sync')

        with flask_app.app_context():
            from app import db, Setting
            from blueprints.assets import perform_intune_asset_sync

            def set_setting(key: str, value: str):
                row = Setting.query.filter_by(key=key).first()
                if row is None:
                    row = Setting(key=key, value=value)
                    db.session.add(row)
                else:
                    row.value = value

            try:
                set_setting('intune_asset_sync_last_started', started_at.isoformat())
                set_setting('intune_asset_sync_last_status', 'running')
                db.session.commit()

                result = perform_intune_asset_sync()

                finished_at = datetime.now(timezone.utc)
                set_setting('intune_asset_sync_last_finished', finished_at.isoformat())

                if result.get('success'):
                    set_setting('intune_asset_sync_last_status', 'success')
                    msg = f"synced={result.get('synced_count', 0)} updated={result.get('updated_count', 0)} skipped={result.get('skipped_count', 0)} errors={len(result.get('errors') or [])}"
                    set_setting('intune_asset_sync_last_message', msg)
                    db.session.commit()
                    logger.info('Scheduled Intune asset sync complete: %s', msg)
                else:
                    set_setting('intune_asset_sync_last_status', 'error')
                    set_setting('intune_asset_sync_last_message', result.get('error') or 'Unknown error')
                    db.session.commit()
                    logger.error('Scheduled Intune asset sync failed: %s', result.get('error'))

            except Exception as e:
                try:
                    set_setting('intune_asset_sync_last_status', 'error')
                    set_setting('intune_asset_sync_last_message', str(e))
                    set_setting('intune_asset_sync_last_finished', datetime.now(timezone.utc).isoformat())
                    db.session.commit()
                except Exception:
                    pass

                logger.exception('Scheduled Intune asset sync crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_m365_employee_photo_refresh_job(flask_app):
    """Refresh M365 profile photos for existing employees (downloads to static/uploads)."""
    with _file_lock(M365_PHOTO_LOCK_PATH) as acquired:
        if not acquired:
            logger.info('M365 employee photo refresh skipped (lock held by another process)')
            return

        started_at = datetime.now(timezone.utc)
        logger.info('Starting scheduled M365 employee photo refresh')

        with flask_app.app_context():
            from app import db, Setting, Employee, app as flask_app_instance
            from m365_service import M365Service

            def set_setting(key: str, value: str):
                row = Setting.query.filter_by(key=key).first()
                if row is None:
                    row = Setting(key=key, value=value)
                    db.session.add(row)
                else:
                    row.value = value

            try:
                set_setting('m365_employee_photo_refresh_last_started', started_at.isoformat())
                set_setting('m365_employee_photo_refresh_last_status', 'running')
                db.session.commit()

                from m365_config import get_m365_credentials
                tenant, client_id, client_secret = get_m365_credentials()

                if not (tenant and client_id and client_secret):
                    set_setting('m365_employee_photo_refresh_last_status', 'skipped')
                    set_setting('m365_employee_photo_refresh_last_message', 'M365 credentials not configured')
                    set_setting('m365_employee_photo_refresh_last_finished', datetime.now(timezone.utc).isoformat())
                    db.session.commit()
                    logger.warning('M365 employee photo refresh skipped (credentials not configured)')
                    return

                m365 = M365Service(tenant, client_id, client_secret)
                photo_dir = os.path.join(flask_app_instance.config['UPLOAD_FOLDER'], 'employee_photos')
                os.makedirs(photo_dir, exist_ok=True)

                refreshed = 0
                missing = 0
                skipped = 0

                employees = Employee.query.filter(Employee.email.isnot(None), Employee.email != '').all()
                for emp in employees:
                    email = (emp.email or '').strip()
                    if not email:
                        skipped += 1
                        continue

                    # Avoid churn: if file exists and is recent, skip
                    if emp.photo:
                        abs_existing = os.path.join(flask_app_instance.config['UPLOAD_FOLDER'], emp.photo)
                        try:
                            if os.path.exists(abs_existing):
                                age_seconds = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(abs_existing))
                                if age_seconds < 7 * 24 * 3600:
                                    skipped += 1
                                    continue
                        except Exception:
                            pass

                    photo_bytes = m365.get_user_photo_bytes(email)
                    if not photo_bytes:
                        missing += 1
                        continue

                    rel = f"employee_photos/employee_{emp.id}.jpg"
                    abs_path = os.path.join(flask_app_instance.config['UPLOAD_FOLDER'], rel)
                    try:
                        with open(abs_path, 'wb') as f:
                            f.write(photo_bytes)
                        emp.photo = rel
                        refreshed += 1
                    except Exception:
                        skipped += 1

                db.session.commit()

                finished_at = datetime.now(timezone.utc)
                msg = f"refreshed={refreshed} missing={missing} skipped={skipped}"
                set_setting('m365_employee_photo_refresh_last_finished', finished_at.isoformat())
                set_setting('m365_employee_photo_refresh_last_status', 'success')
                set_setting('m365_employee_photo_refresh_last_message', msg)
                db.session.commit()
                logger.info('Scheduled M365 employee photo refresh complete: %s', msg)

            except Exception as e:
                try:
                    set_setting('m365_employee_photo_refresh_last_status', 'error')
                    set_setting('m365_employee_photo_refresh_last_message', str(e))
                    set_setting('m365_employee_photo_refresh_last_finished', datetime.now(timezone.utc).isoformat())
                    db.session.commit()
                except Exception:
                    pass

                logger.exception('Scheduled M365 employee photo refresh crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_ad_employee_sync_job(flask_app_instance):
    """Daily: refresh employees from AD/M365 (so disables are detected), then park an
    offboard (1-click review) for anyone now disabled-but-visible."""
    with _file_lock(AD_EMPLOYEE_SYNC_LOCK_PATH) as acquired:
        if not acquired:
            logger.debug('AD employee sync already running in another worker — skipping')
            return
        with flask_app_instance.app_context():
            try:
                from extensions import db
                from blueprints.employees import run_ad_employee_sync
                res = run_ad_employee_sync()
                logger.info('Scheduled AD employee sync: %s', res)
                if not res.get('error'):
                    from blueprints.employees import verify_and_park_offboards
                    pres = verify_and_park_offboards()  # AD-verified: only positively-disabled park
                    logger.info('AD sync offboard parking (verified): %s', pres)
            except Exception:
                logger.exception('AD employee sync job crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_m365_employee_reconcile_job(flask_app_instance):
    """Daily: keep the employee page's M365 status honest.

    1. Refresh the authoritative M365User table from Graph (SOC2SyncService.sync_m365_users) —
       this table is otherwise only refreshed by a manual SOC2 sync, so it drifts.
    2. Resolve identity links so freshly-synced M365User rows get their employee_id FK.
    3. Reconcile employee.m365_account_enabled / m365_validated_at from the M365User table.

    Step 3 still runs even if step 1 (Graph) fails, so a Graph outage degrades to "reconcile
    off the last-known M365 data" rather than freezing the flags. Own file-lock + DISABLE flag,
    consistent with the other scheduler jobs. Does not touch AD sync behavior."""
    with _file_lock(M365_EMPLOYEE_RECONCILE_LOCK_PATH) as acquired:
        if not acquired:
            logger.debug('M365 employee reconcile already running in another worker — skipping')
            return
        with flask_app_instance.app_context():
            try:
                from extensions import db
                # 1. Refresh the authoritative M365User table from Graph (best-effort).
                try:
                    from soc2_sync_service import SOC2SyncService
                    from m365_config import m365_configured
                    if m365_configured():
                        ures = SOC2SyncService(flask_app_instance, db).sync_m365_users()
                        logger.info('M365 reconcile: M365User Graph sync: %s', ures)
                    else:
                        logger.info('M365 reconcile: M365 credentials not configured — '
                                    'reconciling off existing M365User data only')
                except Exception:
                    logger.exception('M365 reconcile: M365User Graph sync failed — '
                                     'continuing with existing M365User data')

                # 2. Link any newly-synced M365User rows to their employee.
                try:
                    from identity_graph import resolve_identity_links
                    lres = resolve_identity_links(commit=True)
                    logger.info('M365 reconcile: identity links: %s', lres)
                except Exception:
                    logger.exception('M365 reconcile: identity link resolution failed')

                # 3. Reconcile the cached employee flags from the M365User table.
                from blueprints.employees import reconcile_employee_m365_flags
                rres = reconcile_employee_m365_flags()
                logger.info('M365 reconcile: employee flag reconcile: %s', rres)
            except Exception:
                logger.exception('M365 employee reconcile job crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_employee_offboard_sweep_job(flask_app_instance):
    """Park an offboard (for 1-click review) for every employee that's still visible but
    disabled in AD/M365 — so disabled users get their devices+licenses released on approval.
    Idempotent via park_offboard's correlation-id guard."""
    with _file_lock(OFFBOARD_SWEEP_LOCK_PATH) as acquired:
        if not acquired:
            logger.debug('Offboard sweep already running in another worker — skipping')
            return
        with flask_app_instance.app_context():
            try:
                from extensions import db
                from blueprints.employees import verify_and_park_offboards
                pres = verify_and_park_offboards()  # AD-verified: park only positively-disabled
                logger.info('Employee offboard sweep (verified): %s', pres)
            except Exception:
                logger.exception('Employee offboard sweep crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_ad_delete_sweep_job(flask_app_instance):
    """PARK a permanent AD-delete (for 1-click human confirm) for every employee whose
    30-day offboard retention window has elapsed. PARKS ONLY — it never deletes directly;
    the irreversible delete runs only when a human approves the parked request at /approvals.

    Selection: offboarded_at IS NOT NULL AND offboarded_at <= (now - N days) AND
    ad_enabled == False (still disabled in AD) AND is_visible == False (still hidden) AND
    onboard_status != 'deleted' (still has an AD object) AND no pending delete_ad_user
    already parked AND no prior denied delete_ad_user for this employee (deny = stop).
    N comes from Setting('offboard_delete_after_days'), default 30.
    Idempotent via park_ad_delete's correlation-id + status guards."""
    from datetime import timedelta
    with _file_lock(AD_DELETE_SWEEP_LOCK_PATH) as acquired:
        if not acquired:
            logger.debug('AD-delete sweep already running in another worker — skipping')
            return
        with flask_app_instance.app_context():
            try:
                from extensions import db
                from models import Employee, Setting
                import workflow_engine

                # Retention window (default 30); read the Setting if present, else 30.
                days = 30
                try:
                    s = Setting.query.filter_by(key='offboard_delete_after_days').first()
                    if s and str(s.value).strip():
                        days = int(str(s.value).strip())
                except Exception:
                    logger.exception('AD-delete sweep: bad offboard_delete_after_days Setting — using 30')
                    days = 30
                if days < 0:
                    days = 30

                # Cutoff in the SAME local clock (_now) that stamped offboarded_at — tz-consistent.
                now_local = datetime.strptime(workflow_engine._now(), "%Y-%m-%d %H:%M:%S")
                cutoff = now_local - timedelta(days=days)

                # Only park a delete for an account that is STILL offboarded: disabled in AD
                # (ad_enabled == False) AND hidden (is_visible == False). A reactivated /
                # rehired / unhidden employee must NOT be a candidate even if a stale
                # offboarded_at lingers — those paths also clear offboarded_at (defense in depth).
                candidates = (Employee.query
                              .filter(Employee.offboarded_at.isnot(None))
                              .filter(Employee.offboarded_at <= cutoff)
                              .filter(Employee.ad_enabled == False)
                              .filter(Employee.is_visible == False)
                              .filter(db.func.coalesce(Employee.onboard_status, '') != 'deleted')
                              .all())

                parked = skipped = 0
                for emp in candidates:
                    ticket_id = None  # found by [OFFBOARD] subject at delete time
                    led = workflow_engine.park_ad_delete(
                        emp.id, emp.name, requested_by='ad-delete-sweep',
                        disabled_since=(emp.offboarded_at.strftime("%Y-%m-%d %H:%M:%S")
                                        if emp.offboarded_at else None),
                        ticket_id=ticket_id)
                    if led:
                        parked += 1
                    else:
                        skipped += 1  # already parked or already deleted
                logger.info('AD-delete sweep: retention=%sd cutoff=%s candidates=%s parked=%s skipped=%s',
                            days, cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                            len(candidates), parked, skipped)
            except Exception:
                logger.exception('AD-delete sweep crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_ad_asset_sync_job(flask_app_instance):
    """Run the on-prem AD computer sync with a cross-process lock (AD = source of truth)."""
    with _file_lock(AD_ASSET_SYNC_LOCK_PATH) as acquired:
        if not acquired:
            logger.debug('AD asset sync already running in another worker — skipping')
            return
        with flask_app_instance.app_context():
            try:
                from app import db, Asset, Setting, AssetHistory
                from ad_asset_service import sync_ad_computers
                res = sync_ad_computers(flask_app_instance, db, Asset, Setting, AssetHistory)
                logger.info('Scheduled AD asset sync complete: %s', res)
            except Exception:
                logger.exception('AD asset sync job crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_unifi_sync_job(flask_app_instance):
    """Run the UniFi device sync with a cross-process lock."""
    with _file_lock(UNIFI_SYNC_LOCK_PATH) as acquired:
        if not acquired:
            logger.debug('UniFi sync already running in another worker — skipping')
            return

        with flask_app_instance.app_context():
            try:
                from app import db, Asset, Setting, AssetHistory, MonitoringAlert
                from unifi_service import sync_unifi_assets
                sync_unifi_assets(flask_app_instance, db, Asset, Setting, AssetHistory, MonitoringAlert)
            except Exception:
                logger.exception('UniFi sync job crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_proxmox_sync_job(flask_app_instance):
    """Run the Proxmox backup/ZFS sync with a cross-process lock."""
    with _file_lock(PROXMOX_SYNC_LOCK_PATH) as acquired:
        if not acquired:
            logger.debug('Proxmox sync already running in another worker — skipping')
            return

    with flask_app_instance.app_context():
        try:
            from app import db, Setting, ProxmoxBackupJob, ProxmoxZfsPool, MonitoringAlert
            from datetime import datetime
            from proxmox_service import sync_proxmox

            result = sync_proxmox(flask_app_instance, db, ProxmoxBackupJob,
                                  ProxmoxZfsPool, Setting, MonitoringAlert)

            # Record last sync time
            row = Setting.query.filter_by(key='proxmox_last_sync').first()
            if row is None:
                row = Setting(key='proxmox_last_sync', value=datetime.utcnow().isoformat())
                db.session.add(row)
            else:
                row.value = datetime.utcnow().isoformat()
            db.session.commit()

            logger.info(
                'Proxmox sync complete: nodes=%d pools=%d vms=%d alerts=%d errors=%d',
                result.get('nodes_synced', 0), result.get('pools_synced', 0),
                result.get('vms_synced', 0), result.get('alerts_fired', 0),
                len(result.get('errors', [])),
            )
            if result.get('errors'):
                for err in result['errors']:
                    logger.warning('Proxmox sync error: %s', err)
        except Exception:
            logger.exception('Proxmox sync job crashed')
        finally:
            try:
                db.session.remove()
            except Exception:
                pass


def run_backup_scheduler_job(flask_app):
    """Check all enabled agent backup policies and trigger backups that are due.

    Runs every BACKUP_SCHEDULER_INTERVAL_MINUTES (default 60 min).

    Throttling rules to protect the NAS and network:
      - At most BACKUP_MAX_CONCURRENT_FULL full jobs may be *running* system-wide;
        if that ceiling is reached the cycle exits early.
      - At most BACKUP_MAX_INITIAL_PER_CYCLE *never-backed-up* agents are triggered
        per cycle.  The rest are deferred to the next cycle, naturally staggering
        the initial load across hours rather than seconds.
      - BACKUP_TRIGGER_STAGGER_SECONDS sleep between successive triggers prevents
        a simultaneous queue burst even within one cycle.
    """
    import time
    import urllib.request, json as _json
    from datetime import timedelta

    with _file_lock(BACKUP_SCHEDULER_LOCK_PATH) as acquired:
        if not acquired:
            logger.info('Backup scheduler skipped (lock held by another process)')
            return

    with flask_app.app_context():
        try:
            from app import db
            from sqlalchemy import text

            gateway_url = os.environ.get('RMM_GATEWAY_URL', 'http://127.0.0.1:8765')
            now = datetime.now(timezone.utc)
            incr_threshold = now - timedelta(hours=BACKUP_INCREMENTAL_INTERVAL_HOURS)

            # ── 1. Check global concurrent full-backup pressure ───────────────────
            running_full_count = db.session.execute(text("""
                SELECT COUNT(*) FROM rmm_backup_job
                WHERE status = 'running'
                  AND job_type IN ('full', 'auto')
            """)).scalar() or 0

            if running_full_count >= BACKUP_MAX_CONCURRENT_FULL:
                logger.info(
                    'Backup scheduler: %d full jobs already running (limit %d) — skipping cycle',
                    running_full_count, BACKUP_MAX_CONCURRENT_FULL
                )
                return

            # ── 2. Fetch all enabled assignments ──────────────────────────────────
            rows = db.session.execute(text("""
                SELECT abp.agent_id, p.full_backup_interval_days
                FROM rmm_agent_backup_policy abp
                JOIN rmm_backup_policy p ON p.id = abp.policy_id
                WHERE abp.enabled = true AND p.enabled = true
                ORDER BY abp.agent_id
            """)).fetchall()

            initial_triggered_this_cycle = 0

            for agent_id, full_interval_days in rows:

                # Re-check running count each iteration — another worker could
                # have started jobs while we loop.
                if running_full_count >= BACKUP_MAX_CONCURRENT_FULL:
                    logger.info(
                        'Backup scheduler: hit concurrent full-backup ceiling (%d/%d), '
                        'deferring remaining agents to next cycle',
                        running_full_count, BACKUP_MAX_CONCURRENT_FULL
                    )
                    break

                # Last successful backup for this agent
                last = db.session.execute(text("""
                    SELECT job_type, started_at
                    FROM rmm_backup_job
                    WHERE agent_id = :agent_id AND status = 'success'
                    ORDER BY started_at DESC
                    LIMIT 1
                """), {'agent_id': agent_id}).fetchone()

                if last is None:
                    # ── Never backed up (initial backup) ─────────────────────────
                    # Enforce per-cycle initial cap to avoid NAS flooding when a
                    # policy is bulk-assigned to many workstations at once.
                    if initial_triggered_this_cycle >= BACKUP_MAX_INITIAL_PER_CYCLE:
                        logger.debug(
                            'Backup scheduler: initial cap (%d) reached — '
                            'deferring %s to next cycle',
                            BACKUP_MAX_INITIAL_PER_CYCLE, agent_id
                        )
                        continue

                    # Also skip if agent already has a running job
                    already_running = db.session.execute(text("""
                        SELECT 1 FROM rmm_backup_job
                        WHERE agent_id = :aid AND status = 'running'
                        LIMIT 1
                    """), {'aid': agent_id}).fetchone()
                    if already_running:
                        logger.debug('Backup scheduler: %s has a running job, skipping', agent_id)
                        continue

                    job_type = 'full'
                    initial_triggered_this_cycle += 1
                    running_full_count += 1  # optimistic increment
                    logger.info('Backup scheduler: triggering INITIAL full for %s (%d/%d this cycle)',
                                agent_id, initial_triggered_this_cycle, BACKUP_MAX_INITIAL_PER_CYCLE)

                else:
                    # ── Has prior backup — check if due ───────────────────────────
                    last_ts = last.started_at
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                    if last_ts >= incr_threshold:
                        logger.debug('Backup for %s is current (last: %s)', agent_id, last_ts)
                        continue
                    job_type = 'auto'  # agent decides full vs incremental

                # ── 3. Send trigger via gateway ───────────────────────────────────
                payload = _json.dumps({
                    'type': 'backup_run',
                    'job_type': job_type,
                    'triggered_by': 'schedule',
                }).encode()
                try:
                    req = urllib.request.Request(
                        f"{gateway_url.rstrip('/')}/send-msg/{agent_id}",
                        data=payload,
                        headers={'Content-Type': 'application/json'},
                        method='POST',
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        result = _json.loads(resp.read())
                    logger.info('Backup triggered for %s (job_type=%s): %s', agent_id, job_type, result)
                except Exception as e:
                    logger.warning('Failed to trigger backup for %s: %s', agent_id, e)
                    if job_type == 'full':
                        running_full_count = max(0, running_full_count - 1)  # undo optimistic increment
                    continue

                # Stagger triggers within this cycle to avoid simultaneous NAS hits
                if BACKUP_TRIGGER_STAGGER_SECONDS > 0:
                    time.sleep(BACKUP_TRIGGER_STAGGER_SECONDS)

        except Exception:
            logger.exception('Backup scheduler job crashed')
        finally:
            try:
                db.session.remove()
            except Exception:
                pass


def run_defender_vuln_sync_job(flask_app):
    """Run a Defender vulnerability sync once daily with a cross-process lock."""
    with _file_lock(DEFENDER_SYNC_LOCK_PATH) as acquired:
        if not acquired:
            logger.info('Defender vuln sync skipped (lock held by another process)')
            return

        logger.info('Starting scheduled Defender vulnerability sync')
        with flask_app.app_context():
            try:
                from alert_service import sync_defender_vulnerabilities
                vc, dc, err = sync_defender_vulnerabilities()
                if err:
                    logger.error('Scheduled Defender vuln sync error: %s', err)
                else:
                    logger.info('Scheduled Defender vuln sync complete: %d CVEs, %d device exposures', vc, dc)
            except Exception:
                logger.exception('Scheduled Defender vuln sync crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass


def run_daily_vuln_email_job(flask_app):
    """Send a daily vulnerability and remediation digest email to all admin users."""
    with _file_lock(VULN_EMAIL_LOCK_PATH) as acquired:
        if not acquired:
            logger.info('Daily vuln email skipped (lock held by another process)')
            return

        logger.info('Starting daily vulnerability email digest')
        with flask_app.app_context():
            try:
                from extensions import db
                from sqlalchemy import text
                from utils import send_admin_notification
                from datetime import date

                # Active Critical/High CVEs with exposed devices
                cves = db.session.execute(text("""
                    SELECT cve_id, name, severity, cvss, exposed_machines
                    FROM vulnerability_cache
                    WHERE severity IN ('Critical', 'High') AND exposed_machines > 0
                    ORDER BY
                        CASE severity WHEN 'Critical' THEN 1 ELSE 2 END,
                        cvss DESC NULLS LAST
                    LIMIT 20
                """)).fetchall()

                # Open device-level remediations
                remediations = db.session.execute(text("""
                    SELECT dv.cve_id, dv.severity, a.name AS asset_name, a.asset_tag,
                           dv.synced_at
                    FROM device_vulnerability dv
                    LEFT JOIN asset a ON a.id = dv.asset_id
                    WHERE dv.status = 'Open'
                    ORDER BY
                        CASE dv.severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 ELSE 3 END,
                        dv.synced_at ASC NULLS LAST
                    LIMIT 30
                """)).fetchall()

                if not cves and not remediations:
                    logger.info('Daily vuln email: no active vulnerabilities, skipping')
                    return

                crit_count = sum(1 for r in cves if r[2] == 'Critical')
                high_count = sum(1 for r in cves if r[2] == 'High')
                today_str = date.today().strftime('%B %d, %Y')

                cve_rows_html = ''
                for r in cves:
                    color = '#dc3545' if r[2] == 'Critical' else '#fd7e14'
                    cve_rows_html += (
                        f'<tr>'
                        f'<td style="padding:6px;font-family:monospace;">{r[0]}</td>'
                        f'<td style="padding:6px;">{r[1] or "N/A"}</td>'
                        f'<td style="padding:6px;color:{color};font-weight:bold;">{r[2]}</td>'
                        f'<td style="padding:6px;">{r[3] or "N/A"}</td>'
                        f'<td style="padding:6px;">{r[4] or 0}</td>'
                        f'</tr>'
                    )

                rem_rows_html = ''
                for r in remediations:
                    color = '#dc3545' if r[1] == 'Critical' else ('#fd7e14' if r[1] == 'High' else '#6c757d')
                    since = str(r[4])[:10] if r[4] else 'N/A'
                    rem_rows_html += (
                        f'<tr>'
                        f'<td style="padding:6px;font-family:monospace;">{r[0]}</td>'
                        f'<td style="padding:6px;color:{color};font-weight:bold;">{r[1]}</td>'
                        f'<td style="padding:6px;">{r[2] or "Unknown"}</td>'
                        f'<td style="padding:6px;">{r[3] or ""}</td>'
                        f'<td style="padding:6px;">{since}</td>'
                        f'</tr>'
                    )

                th_style = 'padding:6px;text-align:left;background:#f0f0f0;'
                body_html = f"""
                <h3 style="margin-bottom:8px;">Vulnerability Digest — {today_str}</h3>
                <p>
                    <span style="background:#dc3545;color:#fff;padding:3px 10px;border-radius:4px;margin-right:6px;">{crit_count} Critical</span>
                    <span style="background:#fd7e14;color:#fff;padding:3px 10px;border-radius:4px;">{high_count} High</span>
                    &nbsp;active CVEs with exposed devices.
                </p>

                <h4 style="margin-top:20px;">Active CVEs (Critical &amp; High)</h4>
                <table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <thead><tr>
                        <th style="{th_style}">CVE ID</th>
                        <th style="{th_style}">Name</th>
                        <th style="{th_style}">Severity</th>
                        <th style="{th_style}">CVSS</th>
                        <th style="{th_style}">Exposed Devices</th>
                    </tr></thead>
                    <tbody>{cve_rows_html}</tbody>
                </table>

                <h4 style="margin-top:20px;">Open Remediations Needed</h4>
                <table style="width:100%;border-collapse:collapse;font-size:13px;">
                    <thead><tr>
                        <th style="{th_style}">CVE ID</th>
                        <th style="{th_style}">Severity</th>
                        <th style="{th_style}">Asset</th>
                        <th style="{th_style}">Tag</th>
                        <th style="{th_style}">Open Since</th>
                    </tr></thead>
                    <tbody>{rem_rows_html}</tbody>
                </table>

                <p style="margin-top:16px;">
                    <a href="https://tracker.corp.cirque.com/vulnerabilities"
                       style="background:#0d6efd;color:#fff;padding:8px 16px;text-decoration:none;border-radius:4px;">
                        View Full Vulnerability Dashboard
                    </a>
                </p>
                """

                send_admin_notification(
                    f'[Daily Vuln Digest] {crit_count} Critical, {high_count} High — {today_str}',
                    body_html
                )
                logger.info('Daily vuln email sent: %d CVEs, %d open remediations',
                            len(cves), len(remediations))
            except Exception:
                logger.exception('Daily vuln email job crashed')
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass
