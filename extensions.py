"""
extensions.py — Flask extension objects, initialised without an app.

Import these into models.py, utils.py, and blueprints.
Call <ext>.init_app(app) inside create_app() in app.py.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect

db = SQLAlchemy()

# CSRF protection. Bound to the app in app.py (gated by TRACKER_CSRF_ENABLED).
csrf = CSRFProtect()

login_manager = LoginManager()
login_manager.login_view = 'auth.login'

mail = Mail()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],        # No global limit; applied per-route
    storage_uri='memory://'
)
