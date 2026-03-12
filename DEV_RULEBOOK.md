# Tracker — Developer Rulebook

Non-negotiable rules for anyone working on this codebase. Breaking these causes hard-to-debug 500 errors and import cycles.

---

## 1. Blueprint Rules

### Always add new routes to the correct blueprint
Each domain has one file. Don't put a ticket route in `assets.py`, don't put an RMM route in `misc.py`.

| What you're building | Goes in |
|---|---|
| Auth, login, SSO, CSAT responses | `blueprints/auth.py` |
| Assets, loans, hardware scans | `blueprints/assets.py` |
| Dashboard widgets | `blueprints/dashboard.py` |
| Employees, org chart | `blueprints/employees.py` |
| Software licenses | `blueprints/licenses.py` |
| Monitoring, checks, alerts | `blueprints/monitoring.py` |
| RMM agents, Eagle Eyes, scripts | `blueprints/rmm.py` |
| Support tickets, SLA, CSAT scores | `blueprints/tickets.py` |
| CVEs, vulnerabilities | `blueprints/vulnerabilities.py` |
| SOC2, compliance, evidence, controls | `blueprints/soc2.py` |
| Reports, CSV export | `blueprints/reports.py` |
| App settings, integrations, admin | `blueprints/settings.py` |
| AI, workflows | `blueprints/ai.py` |
| Downloads, SSH terminal, misc APIs | `blueprints/misc.py` |

### Never register the same route in two blueprints
Duplicate routes cause Flask to silently pick one and shadow the other. If you copy a route from one blueprint to another, delete the original.

### Use `bp.route()` not `app.route()`
```python
# Correct
@bp.route('/assets')
def assets():
    ...

# Wrong — app is not available at module level in blueprints
@app.route('/assets')
```

### Use `bp.app_errorhandler()` not `@bp.errorhandler()`
```python
# Correct — for global error handlers in blueprints
@bp.app_errorhandler(404)
def not_found(e):
    ...
```

---

## 2. Import Rules

### Never import between blueprints
Blueprints must not import from each other. Shared logic belongs in `utils.py` or a service module.

### Import order within a blueprint
```python
# 1. stdlib
import os, json, threading, time as _time ...

# 2. third-party
import requests, msal ...
from werkzeug.utils import secure_filename

# 3. Flask
from flask import Blueprint, current_app, request, redirect, url_for, ...
from flask_login import current_user, login_required

# 4. SQLAlchemy
from sqlalchemy import func, or_, text

# 5. Internal — extensions first, then models, then utils
from extensions import db, limiter
from models import Asset, Employee, User, ... now_mst, allowed_file
from soc2_models import SOC2Control, ...    # only if this blueprint needs SOC2 models
from utils import admin_required, send_email, RMM_GATEWAY_INTERNAL, ...

# 6. Logger
import logging
logger = logging.getLogger(__name__)
```

### Never import `app` directly in blueprints
```python
# Wrong
from app import app
with app.app_context(): ...

# Correct
from flask import current_app
with current_app._get_current_object().app_context(): ...
```

### Wrap optional third-party imports
If a library might not be installed:
```python
try:
    import msal
except ImportError:
    msal = None
```
Then guard usage: `if msal is None: return jsonify({'error': 'msal not installed'}), 500`

---

## 3. Adding New Models

### Models go in `models.py` (or `soc2_models.py` for SOC2 domain)
```python
class MyNewModel(db.Model):
    __tablename__ = 'my_new_table'
    id = db.Column(db.BigInteger, primary_key=True)
    ...
```

### Run a migration script after adding columns/tables
Create a `migrate_add_<feature>.py` script that uses `db.session.execute(text(...))` to add the column with `ALTER TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`. Never use `db.create_all()` on a live database.

### Never rename existing columns or tables
Renames break the live database without a migration. Add new columns; deprecate old ones gradually.

### Export new model names from `models.py`
If a blueprint needs `MyNewModel`, it must be importable from `models`:
```python
from models import MyNewModel
```
The blueprint should NOT define models inline.

---

## 4. Database Access Rules

### Use SQLAlchemy ORM inside Flask routes
```python
# In a blueprint route — always use ORM
asset = Asset.query.get_or_404(asset_id)
db.session.add(new_thing)
db.session.commit()
```

### Use `pg_connect()` only in background services
`alert_service`, `ai_engine`, `workflow_engine`, `report_engine` use raw psycopg2 via `get_db()`. Don't mix patterns.

### Never call `db.create_all()` in production
It silently skips existing tables and won't apply changes to existing schemas.

### Always commit or rollback
```python
try:
    db.session.commit()
except Exception:
    db.session.rollback()
    raise
```

---

## 5. url_for Rules

### Always namespace with blueprint name
```python
# Correct
url_for('assets.view_asset', asset_id=1)
url_for('auth.login')
url_for('dashboard.index')

# Wrong — raises BuildError at runtime, not import time
url_for('view_asset', asset_id=1)
url_for('login')
```

### In templates, same rule applies
```jinja2
{# Correct #}
<a href="{{ url_for('tickets.view_ticket', ticket_id=t.id) }}">View</a>

{# Wrong #}
<a href="{{ url_for('view_ticket', ticket_id=t.id) }}">View</a>
```

---

## 6. Shared Helpers — Don't Reinvent Them

Before writing a new helper, check these locations:

| Need | Location |
|---|---|
| Auth decorators | `utils.admin_required`, `utils.manager_required`, etc. |
| Send email | `utils.send_email`, `utils.send_admin_notification` |
| RMM gateway URLs | `utils.RMM_GATEWAY_INTERNAL/PUBLIC` |
| Agent key validation | `utils._valid_agent_key(key)` |
| ISO timestamp formatting | `utils._dt_iso(dt)` |
| Site enrollment token | `utils._get_or_create_site_enrollment_token()` |
| RMM script library table | `utils._ensure_rmm_script_library_table()` |
| Current Mountain time | `models.now_mst()` |
| Allowed upload extensions | `models.allowed_file(filename)` |

If you need a helper that multiple blueprints will use, add it to `utils.py` — not to a blueprint file.

---

## 7. Error Handling

### Use `logger` not `print()`
Every blueprint has a module-level logger. Use it:
```python
import logging
logger = logging.getLogger(__name__)

# Then:
logger.info("Sync complete")
logger.warning("Agent not found: %s", agent_id)
logger.error("Failed to send email: %s", e)
```

### Use `current_app.logger` inside of lazy contexts
If you're in a context where `logger` isn't set up yet:
```python
current_app.logger.error("Something broke")
```

### Don't swallow exceptions silently
```python
# Wrong
try:
    do_thing()
except Exception:
    pass

# Correct
try:
    do_thing()
except Exception as e:
    logger.error("do_thing failed: %s", e)
    # then either re-raise, flash an error, or return 500
```

---

## 8. Templates

### Extend `base.html`
All full-page templates must start with:
```jinja2
{% extends "base.html" %}
{% block content %}
...
{% endblock %}
```

### Don't put business logic in templates
Templates should only format and display data. Compute values in the route handler, pass them via `render_template(context)`.

### Never use inline `<script>` for anything security-sensitive
Secrets, API keys, and tokens must never appear in rendered HTML.

---

## 9. Security Rules

### Rate-limit sensitive endpoints
```python
from extensions import limiter

@bp.route('/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    ...
```

### Validate all user input
Never trust form data, query parameters, or JSON body without validation. Use `request.form.get('key', '').strip()` — not `request.form['key']`.

### Parameterize all raw SQL
```python
# Correct
db.session.execute(text("SELECT * FROM asset WHERE id = :id"), {'id': asset_id})

# Wrong — SQL injection vulnerability
db.session.execute(text(f"SELECT * FROM asset WHERE id = {asset_id}"))
```

### Never log passwords, tokens, or keys
```python
# Wrong
logger.debug("User submitted password: %s", password)

# Correct
logger.debug("Login attempt for user: %s", username)
```

---

## 10. Deployment Workflow

### After any `.py` file edit:
```bash
# Clear stale bytecode
find /var/www/tracker -name '*.pyc' -delete

# Restart
echo "cirque" | sudo -S systemctl restart tracker

# Watch for errors
journalctl -u tracker -f --no-pager | grep -i 'error\|500\|NameError'
```

### After any template or static file edit:
No restart needed — Gunicorn serves templates dynamically.

### Before pushing any breaking change:
Test locally by importing the blueprint directly:
```bash
cd /var/www/tracker && source venv/bin/activate
python3 -c "from blueprints.<name> import bp; print('OK')"
```

### When adding a new blueprint:
1. Create `blueprints/<name>.py` with `bp = Blueprint('<name>', __name__)`
2. Register in `app.py`: `app.register_blueprint(<name>.bp)`
3. Clear pycache, restart, verify no import errors

---

## 11. What Goes Where — Quick Reference

| Thing | Location |
|---|---|
| Flask extension objects | `extensions.py` |
| SQLAlchemy models | `models.py` or `soc2_models.py` |
| Shared decorators & helpers | `utils.py` |
| Route handlers | `blueprints/<domain>.py` |
| App factory & blueprint registration | `app.py` |
| Gunicorn entry point + scheduler start | `wsgi.py` |
| One-time migration scripts | `migrate_add_<feature>.py` (run manually) |
| One-time data load scripts | `load_<thing>.py` (run manually) |
| Background sync services | `<service>_service.py` |
| Jinja2 templates | `templates/` |
| Static assets (CSS/JS/images) | `static/` |
| RMM agent code | `linux_agent/` |
| Environment secrets | `.secrets.env` (never commit) |
