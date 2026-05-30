"""Central accessor for integration secrets.

Secrets now live in the environment (/var/www/tracker/.secrets.env, loaded by the
systemd EnvironmentFile) instead of the database. A DB lookup is kept as a
backward-compatible fallback so a missing env var can never lock anyone out of
SSO during/after the migration.

M365/Azure credentials:  M365_TENANT_ID / M365_CLIENT_ID / M365_CLIENT_SECRET
Other integration secrets are exposed via secret_setting(key) which checks an
env var (TRACKER_SECRET_<UPPER_KEY>) before falling back to the Setting table.
"""
import os


def get_m365_credentials():
    """Return (tenant_id, client_id, client_secret) for the tracker M365 app.

    Order: environment → AzureIntegrationConfig(app_name='tracker') → Setting
    m365_* keys. Returns (None, None, None) if nothing is configured.
    """
    tid = os.environ.get('M365_TENANT_ID')
    cid = os.environ.get('M365_CLIENT_ID')
    sec = os.environ.get('M365_CLIENT_SECRET')
    if tid and cid and sec:
        return tid, cid, sec

    # Backward-compatible DB fallback (kept so SSO can't break if env is unset).
    try:
        from models import AzureIntegrationConfig, Setting
        cfg = AzureIntegrationConfig.query.filter_by(enabled=True, app_name='tracker').first()
        if cfg and cfg.tenant_id and cfg.client_id and cfg.client_secret:
            return (tid or cfg.tenant_id, cid or cfg.client_id, sec or cfg.client_secret)

        def _s(k):
            row = Setting.query.filter_by(key=k).first()
            return row.value if row and row.value else None
        return (tid or _s('m365_tenant_id'),
                cid or _s('m365_client_id'),
                sec or _s('m365_client_secret'))
    except Exception:
        return tid, cid, sec


def m365_configured():
    """True if a full set of M365 credentials is available (env or DB)."""
    return all(get_m365_credentials())


def secret_setting(key, default=None):
    """Return a secret-valued Setting, env-first.

    Checks TRACKER_SECRET_<UPPER_KEY> in the environment, then the Setting table.
    Used for non-M365 integration secrets (openai/teamviewer/unifi/ad_bind/...).
    """
    env_val = os.environ.get('TRACKER_SECRET_' + key.upper())
    if env_val:
        return env_val
    try:
        from models import Setting
        row = Setting.query.filter_by(key=key).first()
        if row and row.value:
            return row.value
    except Exception:
        pass
    return default
