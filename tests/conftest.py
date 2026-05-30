"""Shared pytest fixtures.

These tests are deliberately self-contained: they exercise import-time wiring,
security headers, and CSRF configuration WITHOUT touching the database, so they
run anywhere (CI included) without a live Postgres. Dummy env vars are set
before importing the app so app.py's required-env checks pass.
"""
import os
import sys

import pytest

# app.py requires these at import; set harmless test values if not already set.
# DATABASE_URL points at a DSN that is never actually connected to by these tests
# (no test here issues a query — they assert on routing/headers/config only).
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('LINUX_AGENT_API_KEY', 'test-agent-key')
os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test_unused')

# Ensure the project root is importable.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope='session')
def flask_app():
    import app as app_module
    application = app_module.app
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()
