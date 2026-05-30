"""Regression tests for the week-1 security hardening.

These lock in the guarantees so they can't silently regress:
  - security headers (CSP/HSTS/X-Frame-Options) on every response
  - CSRF enforced on browser routes, exempted on agent/API routes
  - no hardcoded DB password left in source
  - background jobs guarded by a single-instance lock
"""
import multiprocessing
import os
import re
import subprocess
import time

import pytest


# ── Security headers ─────────────────────────────────────────────────────────
def test_security_headers_present(client):
    resp = client.get('/')  # 302 → login; headers added by after_request
    assert 'Content-Security-Policy' in resp.headers
    csp = resp.headers['Content-Security-Policy']
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "form-action 'self'" in csp
    assert resp.headers.get('X-Frame-Options') == 'SAMEORIGIN'
    assert 'Strict-Transport-Security' in resp.headers


# ── CSRF enforcement / exemptions ────────────────────────────────────────────
def test_csrf_enforced_on_browser_post(client):
    # No token → Flask-WTF rejects before the view runs (no DB hit).
    resp = client.post('/users/add', data={'username': 'x'})
    assert resp.status_code == 400


def _exempt_locations(flask_app):
    import app as app_module
    return app_module.csrf._exempt_views  # set of "module.qualname" strings


def _location_for_rule(flask_app, rule_path):
    for rule in flask_app.url_map.iter_rules():
        if rule.rule == rule_path:
            vf = flask_app.view_functions.get(rule.endpoint)
            return f"{vf.__module__}.{vf.__qualname__}" if vf else None
    return None


@pytest.mark.parametrize('rule_path', [
    '/api/linux-agent/heartbeat',
    '/api/linux-agent/check-result',
    '/api/rmm/agent/command_result',
    '/api/rmm/enroll',
    '/api/rmm/<agent_id>/software',
    '/api/rmm/rustdesk-sync/<agent_id>',
    '/api/rmm/telemetry',
    '/api/rmm/system-info',
    '/api/asset/<int:asset_id>/software',
    '/api/support-tickets',
])
def test_agent_endpoints_are_csrf_exempt(flask_app, rule_path):
    loc = _location_for_rule(flask_app, rule_path)
    assert loc is not None, f'route {rule_path} not found'
    assert loc in _exempt_locations(flask_app), f'{rule_path} should be CSRF-exempt'


@pytest.mark.parametrize('rule_path', [
    '/users/add',
    '/users/<int:user_id>/delete',
    '/users/<int:user_id>/view-as',
    '/login',
])
def test_browser_routes_are_csrf_protected(flask_app, rule_path):
    loc = _location_for_rule(flask_app, rule_path)
    assert loc is not None, f'route {rule_path} not found'
    assert loc not in _exempt_locations(flask_app), f'{rule_path} must stay CSRF-protected'


# ── No hardcoded DB password in source ───────────────────────────────────────
def test_no_hardcoded_db_password():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # The old leaked password must not reappear in tracked source.
    # Build the needle from parts so this test file is not itself a match.
    needle = 'tracker_secure' + '_2026'
    hits = subprocess.run(
        ['grep', '-rIl', needle, root,
         '--include=*.py', '--include=*.sh', '--include=*.service',
         '--exclude-dir=venv', '--exclude-dir=.venv', '--exclude-dir=.git',
         '--exclude-dir=tests'],
        capture_output=True, text=True,
    ).stdout.strip()
    leaked = [h for h in hits.splitlines() if h]
    assert not leaked, f'hardcoded DB password found in: {leaked}'


# ── Background-job single-instance lock ──────────────────────────────────────
def test_sla_pass_extracted_and_lock_available():
    import blueprints.tickets as tickets
    import license_service
    import sync_scheduler
    assert hasattr(tickets, '_do_sla_pass')
    assert hasattr(license_service.license_service, '_guarded_periodic_check')
    assert hasattr(sync_scheduler, '_file_lock')


def test_secret_store_roundtrip(monkeypatch):
    """UI-managed secrets encrypt/decrypt correctly and pass plaintext through."""
    from cryptography.fernet import Fernet
    import secret_store
    monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY', Fernet.generate_key().decode())
    enc = secret_store.encrypt_secret('s3cret-value')
    assert enc.startswith('enc:v1:') and enc != 's3cret-value'
    assert secret_store.decrypt_secret(enc) == 's3cret-value'
    assert secret_store.decrypt_secret('plaintext') == 'plaintext'   # transparent
    assert secret_store.encrypt_secret(enc) == enc                   # no double-encrypt
    assert secret_store.encrypt_if_secret('unifi_password', 'p').startswith('enc:v1:')
    assert secret_store.encrypt_if_secret('not_a_secret', 'p') == 'p'


def test_auth_events_are_audited():
    """Login (success + failure) and logout must write to the audit trail."""
    import inspect
    import blueprints.auth as auth
    assert hasattr(auth, '_audit_auth')
    cb = inspect.getsource(auth.login_microsoft_callback)
    assert "_audit_auth('login'" in cb, 'successful SSO login not audited'
    assert "_audit_auth('login_failed'" in cb, 'failed SSO login not audited'
    assert "_audit_auth('logout'" in inspect.getsource(auth.logout), 'logout not audited'


def _lock_worker(path, q):
    from sync_scheduler import _file_lock
    with _file_lock(path) as got:
        if got:
            q.put('won')
            time.sleep(0.4)
        else:
            q.put('skip')


def test_file_lock_admits_only_one_holder(tmp_path):
    path = str(tmp_path / 'contention.lock')
    q = multiprocessing.Queue()
    procs = [multiprocessing.Process(target=_lock_worker, args=(path, q)) for _ in range(5)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    results = [q.get() for _ in range(5)]
    assert results.count('won') == 1, f'expected exactly one lock holder, got {results}'
