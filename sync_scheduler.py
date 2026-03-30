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
UNIFI_SYNC_INTERVAL_MINUTES = int(os.environ.get('UNIFI_SYNC_INTERVAL_MINUTES', '5'))
DISABLE_UNIFI_SYNC = os.environ.get('DISABLE_UNIFI_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

DEFENDER_SYNC_LOCK_PATH = os.environ.get('TRACKER_DEFENDER_SYNC_LOCK_PATH', '/tmp/tracker_defender_vuln_sync.lock')
DEFENDER_SYNC_HOUR = int(os.environ.get('DEFENDER_SYNC_HOUR', '2'))  # 2 AM local time
DISABLE_DEFENDER_SYNC = os.environ.get('DISABLE_DEFENDER_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

PROXMOX_SYNC_LOCK_PATH = os.environ.get('TRACKER_PROXMOX_SYNC_LOCK_PATH', '/tmp/tracker_proxmox_sync.lock')
PROXMOX_SYNC_INTERVAL_MINUTES = int(os.environ.get('PROXMOX_SYNC_INTERVAL_MINUTES', '15'))
DISABLE_PROXMOX_SYNC = os.environ.get('DISABLE_PROXMOX_SYNC', '').strip() in ('1', 'true', 'yes', 'on')

BACKUP_SCHEDULER_LOCK_PATH = os.environ.get('TRACKER_BACKUP_SCHEDULER_LOCK_PATH', '/tmp/tracker_backup_scheduler.lock')
BACKUP_SCHEDULER_INTERVAL_MINUTES = int(os.environ.get('BACKUP_SCHEDULER_INTERVAL_MINUTES', '60'))
BACKUP_INCREMENTAL_INTERVAL_HOURS = int(os.environ.get('BACKUP_INCREMENTAL_INTERVAL_HOURS', '24'))
DISABLE_BACKUP_SCHEDULER = os.environ.get('DISABLE_BACKUP_SCHEDULER', '').strip() in ('1', 'true', 'yes', 'on')

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

    _scheduler.start()
    logger.info('Started sync scheduler')

    return _scheduler


def run_intune_asset_sync_job(flask_app):
    """Run the Intune asset sync with a cross-process lock."""
    with _file_lock(SYNC_LOCK_PATH) as acquired:
        if not acquired:
            logger.info('Intune sync skipped (lock held by another process)')
            return

        started_at = datetime.now(timezone.utc)
        logger.info('Starting scheduled Intune asset sync')

        with flask_app.app_context():
            from app import db, Setting, perform_intune_asset_sync

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

                tenant = Setting.query.filter_by(key='m365_tenant_id').first()
                client_id = Setting.query.filter_by(key='m365_client_id').first()
                client_secret = Setting.query.filter_by(key='m365_client_secret').first()

                if not (tenant and client_id and client_secret):
                    set_setting('m365_employee_photo_refresh_last_status', 'skipped')
                    set_setting('m365_employee_photo_refresh_last_message', 'M365 credentials not configured')
                    set_setting('m365_employee_photo_refresh_last_finished', datetime.now(timezone.utc).isoformat())
                    db.session.commit()
                    logger.warning('M365 employee photo refresh skipped (credentials not configured)')
                    return

                m365 = M365Service(tenant.value, client_id.value, client_secret.value)
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
