# Tracker — Development Schema

Architecture and structural reference for the `/var/www/tracker` Flask application.

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Framework | Flask (Blueprints) |
| Database | PostgreSQL (`tracker` db, user `tracker_user`) |
| ORM | SQLAlchemy (via Flask-SQLAlchemy) |
| Auth | Flask-Login |
| Email | Flask-Mail |
| Rate limiting | Flask-Limiter (in-memory) |
| WSGI server | Gunicorn (port 8000) |
| Reverse proxy | Nginx |
| Service | systemd `tracker.service` |
| Venv | `/var/www/tracker/venv` |
| Secrets | `/var/www/tracker/.secrets.env` |

---

## File Load Order

The import chain is strict — each layer only imports from layers above it:

```
extensions.py       ← Flask extension objects (db, login_manager, mail, limiter)
    ↓
models.py           ← SQLAlchemy models, event listeners, now_mst(), allowed_file()
soc2_models.py      ← SOC2-specific models (SOC2Control, EvidenceSnapshot, IntuneDevice, M365User, etc.)
    ↓
utils.py            ← Decorators, shared helpers, RMM constants, email functions
    ↓
blueprints/*.py     ← Route handlers, grouped by domain
    ↓
app.py              ← App factory: registers extensions + all blueprints
    ↓
wsgi.py             ← Gunicorn entry point: starts scheduler + license service
```

**Rule:** Never import from a layer below you. Blueprints import from `models.py`, `utils.py`, `extensions.py` — never from each other or from `app.py`.

---

## Blueprint Map

Each blueprint lives in `/var/www/tracker/blueprints/<name>.py` and is registered in `app.py` as `app.register_blueprint(<name>.bp)`.

| Blueprint | Domain | Key URL prefixes |
|---|---|---|
| `auth` | Login, logout, SAML/MSAL SSO, CSAT | `/login`, `/logout`, `/csat/` |
| `dashboard` | Home dashboard, widgets | `/`, `/dashboard` |
| `assets` | Asset CRUD, loans, installed apps, QR | `/assets`, `/asset/<id>` |
| `employees` | Employee management, M365 import | `/employees`, `/employee/<id>` |
| `licenses` | License pool, assignments | `/licenses`, `/license/<id>` |
| `monitoring` | Monitoring profiles, alerts, checks | `/monitoring`, `/alerts/` |
| `rmm` | RMM agent management, Eagle Eyes, scripts | `/rmm/`, `/api/rmm/` |
| `tickets` | Support tickets, CSAT, SLA | `/tickets`, `/ticket/<id>` |
| `vulnerabilities` | CVE tracking, Defender sync | `/vulnerabilities` |
| `soc2` | SOC2 compliance, evidence, controls | `/soc2`, `/controls`, `/risks`, `/policies` |
| `reports` | Custom reports, exports | `/reports` |
| `settings` | App settings, integrations, users | `/settings` |
| `ai` | AI engine, workflows | `/workflows`, `/api/workflows/` |
| `misc` | Agent downloads, SSH terminal, alerts API | `/alerts/center`, `/agent/`, `/download/` |

---

## Database Access Patterns

There are **two** database access patterns in this codebase. Use the right one for the context:

### 1. SQLAlchemy ORM — use in Flask request handlers
```python
# In any blueprint route:
from extensions import db
from models import Asset

asset = Asset.query.get_or_404(asset_id)
db.session.add(asset)
db.session.commit()
```

### 2. Raw psycopg2 — use in background services only
```python
# In background services (ai_engine, alert_service, workflow_engine, report_engine):
from pg_db import pg_connect

def get_db():
    from pg_db import pg_connect
    return pg_connect()

con = get_db()
cur = con.cursor()
cur.execute("SELECT ...")
```

`pg_db.py` provides a thin shim that mimics the old sqlite3 API. Do **not** use `pg_connect()` inside Flask request handlers — use the ORM.

---

## Key Shared Modules

### `extensions.py`
Flask extension singletons. Import these everywhere — never instantiate extensions yourself.
```python
from extensions import db, login_manager, mail, limiter
```

### `models.py`
All SQLAlchemy models. Also exposes:
- `now_mst()` — current Mountain time datetime
- `allowed_file(filename)` — checks permitted upload extensions
- `AssetLoan`, `InstalledApp` — asset-related models
- `ProfileCheck`, `AssetMonitoringProfile` — SQLAlchemy Table objects (association tables)

### `soc2_models.py`
SOC2 compliance models:
- `SOC2Control`, `EvidenceSnapshot`, `M365User`, `IntuneDevice`
- `StrikeGraphEvidence`, `AuditLog`

### `utils.py`
Shared helpers. Imports only from `extensions.py` and `models.py`.
- **Decorators:** `admin_required`, `manager_required`, `eagle_eyes_required`, `ticket_access_required`, `license_required`
- **Email:** `send_email`, `send_admin_notification`, `send_asset_assignment_email`, `send_warranty_expiry_alert`, `send_lifecycle_alert`
- **RMM constants:** `RMM_GATEWAY_INTERNAL`, `RMM_GATEWAY_PUBLIC`, `RMM_TRACKER_URL`
- **RMM helpers:** `_valid_agent_key()`, `_dt_iso()`, `_get_or_create_site_enrollment_token()`, `_ensure_rmm_script_library_table()`

---

## Service Modules (Background / Integration)

These run outside the request cycle. They use `pg_connect()` not the ORM.

| Module | Purpose |
|---|---|
| `alert_service.py` | Alert processing, notification bell, `_get_db()` |
| `ai_engine.py` | OpenAI integration |
| `workflow_engine.py` | Automated workflow execution |
| `report_engine.py` | Report generation |
| `sync_scheduler.py` | APScheduler job orchestrator |
| `m365_service.py` | Microsoft 365 user/device sync |
| `azure_security_service.py` | Azure Defender / Sentinel sync |
| `defender_service.py` | Defender vulnerability sync |
| `unifi_service.py` | UniFi network device sync |
| `proxmox_service.py` | Proxmox VM/backup sync |
| `ldap_service.py` | LDAP/AD directory sync |
| `license_service.py` | License validation |
| `ssh_manager.py` | SSH session management |
| `evidence_file_service.py` | SOC2 evidence file storage |
| `soc2_sync_service.py` | SOC2 full sync orchestration |

---

## Environment & Configuration

Secrets are loaded from `/var/www/tracker/.secrets.env` by systemd. Key env vars:

| Variable | Purpose |
|---|---|
| `FLASK_SECRET_KEY` | Flask session signing |
| `LINUX_AGENT_API_KEY` | RMM agent authentication |
| `RMM_GATEWAY_INTERNAL` | Internal gateway URL (default: `http://127.0.0.1:8765`) |
| `RMM_GATEWAY_URL` | Public WebSocket URL for agents |
| `RMM_TRACKER_URL` | Public tracker URL |
| `MAIL_*` | SMTP configuration |
| `LICENSE_SERVER_URL` | License validation endpoint |

---

## Templates

All Jinja2 templates live in `/var/www/tracker/templates/`. Structure:

```
templates/
├── base.html                 ← Master layout (sidebar, nav, JS/CSS)
├── errors/                   ← 404, 500 pages
├── compliance/               ← SOC2-specific sub-templates
├── *.html                    ← One template per page/feature
```

Static assets (CSS, JS, images) live in `/var/www/tracker/static/`.

---

## URL Conventions

All `url_for()` calls must be **namespaced** with the blueprint name:

```python
# Correct
url_for('assets.view_asset', asset_id=1)
url_for('dashboard.index')
url_for('auth.login')

# Wrong — will raise BuildError
url_for('view_asset', asset_id=1)
url_for('index')
```

---

## Linux Agent

The RMM agent lives in `/var/www/tracker/linux_agent/agent.py`. It communicates with the tracker via:
- `POST /api/rmm/system-info` — hardware telemetry
- `POST /api/rmm/telemetry` — periodic heartbeat
- Authenticated with `LINUX_AGENT_API_KEY` header

---

## Deployment

```bash
# Restart service
echo "cirque" | sudo -S systemctl restart tracker

# Check logs
journalctl -u tracker -f

# Clear bytecode cache (always do this after editing .py files if stale cache suspected)
find /var/www/tracker -name '*.pyc' -delete
find /var/www/tracker -name '__pycache__' -type d -exec rm -rf {} +

# Check for errors
journalctl -u tracker --since "5 minutes ago" --no-pager | grep -i 'error\|500\|NameError'
```
