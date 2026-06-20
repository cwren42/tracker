"""
Migration: add device_software_update table (additive).

Backs the asset-page "Software Updates" card/pane — outdated 3rd-party
applications (Chrome, Zoom, Acrobat, …) that Microsoft Defender for Endpoint
flags with an "Update <software>" software-update recommendation, mapped to the
tracked asset.

This is DISTINCT from:
  - device_vulnerability  (the CVE list)
  - rmm_pending_update    (OS / driver Windows-Update patches)

so the headline "N apps have updates available" never double-counts those.

Mirrors device_vulnerability's structure + close-by-absence handling
(per-run seen-set anti-join with NOW()).

Run once:  venv/bin/python migrate_add_device_software_update.py
Idempotent (CREATE TABLE / INDEX IF NOT EXISTS).
"""
import os
import sys

sys.path.insert(0, '/var/www/tracker')


def migrate():
    # Load DATABASE_URL from .secrets.env if not already in env.
    if not os.environ.get('DATABASE_URL'):
        secrets = '/var/www/tracker/.secrets.env'
        if os.path.exists(secrets):
            with open(secrets) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    import psycopg2
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_software_update (
            id                  BIGSERIAL PRIMARY KEY,
            asset_id            BIGINT NOT NULL,
            agent_id            TEXT,
            software_name       TEXT NOT NULL,   -- display name e.g. "Google Chrome"
            product_key         TEXT NOT NULL,   -- Defender product id e.g. "chrome" (stable join key)
            vendor              TEXT,
            current_version     TEXT,            -- installed version (best-effort from inventory)
            recommended_version TEXT,            -- version Defender recommends upgrading to
            severity            TEXT,             -- weakness severity bucket (High/Medium/Low/Unknown)
            weaknesses          INTEGER DEFAULT 0,-- # of known weaknesses behind this update
            public_exploit      BOOLEAN DEFAULT FALSE,
            source              TEXT DEFAULT 'defender',
            status              TEXT DEFAULT 'Open',  -- Open / Updated (close-by-absence) / Accepted
            remediation_note    TEXT,
            synced_at           TIMESTAMPTZ DEFAULT NOW(),
            updated_at          TIMESTAMPTZ,
            updated_by          TEXT,
            CONSTRAINT uq_device_software_update UNIQUE (asset_id, product_key)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_device_software_update_asset
            ON device_software_update (asset_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS ix_device_software_update_status
            ON device_software_update (status)
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete: device_software_update table + indexes created (additive).")


if __name__ == "__main__":
    migrate()
