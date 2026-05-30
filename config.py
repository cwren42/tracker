"""Centralized Flask configuration.

Hardcoded values are now env-overridable; the three required secrets
(SECRET_KEY, DATABASE_URL, LINUX_AGENT_API_KEY) must be set in the environment
(/var/www/tracker/.secrets.env, loaded by the systemd EnvironmentFile).
"""
import os
from datetime import timedelta


def _required(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f'{name} environment variable is not set. Set it in /var/www/tracker/.secrets.env '
            f'(loaded by the systemd EnvironmentFile); a full `systemctl restart tracker` is '
            f'required to pick it up.'
        )
    return val


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name, default):
    return os.environ.get(name, str(default)).lower() in ('1', 'true', 'yes', 'on')


class Config:
    # ── Security / session ──
    SECRET_KEY = _required('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = _required('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'connect_args': {'options': '-c timezone=UTC'},
        'pool_size': _int('DB_POOL_SIZE', 10),
        'max_overflow': _int('DB_MAX_OVERFLOW', 20),
        'pool_pre_ping': True,
        'pool_recycle': _int('DB_POOL_RECYCLE', 1800),
    }
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_int('SESSION_LIFETIME_HOURS', 8))
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    PREFERRED_URL_SCHEME = 'https'
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', '/var/www/tracker/static/uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024            # 16 MB
    MAX_FORM_MEMORY_SIZE = 16 * 1024 * 1024          # 16 MB for large markdown/manual edits

    # ── Email ──
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'cirque-com.mail.protection.outlook.com')
    MAIL_PORT = _int('MAIL_PORT', 25)
    MAIL_USE_TLS = _bool('MAIL_USE_TLS', True)
    MAIL_USE_SSL = _bool('MAIL_USE_SSL', False)
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME') or None
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD') or None
    MAIL_DEFAULT_SENDER = ('Tracker', os.environ.get('MAIL_SENDER', 'tracker@cirque.com'))
    MAIL_DELIVERY_METHOD = 'smtp'
    SEND_EMPLOYEE_EMAILS = _bool('SEND_EMPLOYEE_EMAILS', False)

    # ── Linux agent ──
    LINUX_AGENT_API_KEY = _required('LINUX_AGENT_API_KEY')

    # ── CSRF (kill-switch: TRACKER_CSRF_ENABLED=0) ──
    WTF_CSRF_ENABLED = os.environ.get('TRACKER_CSRF_ENABLED', '1') != '0'
    WTF_CSRF_TIME_LIMIT = None  # token valid for the session lifetime
