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
