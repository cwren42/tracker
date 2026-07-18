"""
Migration: network_client — the live LAN device inventory + rogue-device NAC state.

Backs the "Rogue Device" detection feature. A scheduled scan polls the UniFi
controller's active clients (stat/sta), reconciles each MAC against known assets
and the acknowledged-device allowlist, and upserts one row per MAC here. Unknown
MACs raise an alert (category='network') and can be blocked one-click via the
UniFi cmd/stamgr block-sta enforcement primitive.

Columns:
  * mac / mac_norm  — raw as-seen MAC + hex-only lowercase dedup key (UNIQUE)
  * sighting        — ip, hostname, oui_vendor, is_wired, vlan, network_name,
                      sw_mac/sw_port (wired uplink), ap_name (wireless uplink),
                      first_seen/last_seen (from UniFi epochs), online
  * classification  — known_asset | acknowledged | known_vendor | unknown
                      (recomputed each scan); asset_id set when known_asset
  * acknowledged    — sticky Tracker-side allowlist flag (survives scans)
  * blocked         — enforcement state (mirrors the UniFi block)
  * first_detected_at — when the Tracker first saw this MAC
  * alerted_at      — when the rogue alert last fired (re-arm dedup)

Idempotent — CREATE TABLE / COLUMN / INDEX IF NOT EXISTS. Safe to re-run.
Postgres (production runs PG via the pg_db shim).
Run once: venv/bin/python migrate_add_network_client.py
"""
import os
import psycopg2


def _dsn() -> str:
    dsn = os.environ.get('DATABASE_URL')
    if dsn:
        return dsn
    with open('/var/www/tracker/.secrets.env') as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('DATABASE_URL='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    raise RuntimeError('DATABASE_URL not set')


DDL = """
CREATE TABLE IF NOT EXISTS network_client (
    id                BIGSERIAL PRIMARY KEY,
    mac               TEXT NOT NULL,
    mac_norm          TEXT NOT NULL UNIQUE,
    ip                TEXT,
    hostname          TEXT,
    oui_vendor        TEXT,
    is_wired          BOOLEAN,
    vlan              INTEGER,
    network_name      TEXT,
    sw_mac            TEXT,
    sw_port           INTEGER,
    ap_name           TEXT,
    uplink_name       TEXT,
    classification    TEXT NOT NULL DEFAULT 'unknown',
    asset_id          BIGINT,
    acknowledged      BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_by   INTEGER,
    acknowledged_at   TIMESTAMPTZ,
    ack_note          TEXT,
    blocked           BOOLEAN NOT NULL DEFAULT FALSE,
    blocked_by        INTEGER,
    blocked_at        TIMESTAMPTZ,
    block_note        TEXT,
    first_seen        TIMESTAMPTZ,
    last_seen         TIMESTAMPTZ,
    online            BOOLEAN NOT NULL DEFAULT FALSE,
    first_detected_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW(),
    alerted_at        TIMESTAMPTZ
);
"""

INDEXES = [
    ("idx_network_client_class_online",
     "CREATE INDEX IF NOT EXISTS idx_network_client_class_online "
     "ON network_client (classification, online)"),
    ("idx_network_client_mac_norm",
     "CREATE INDEX IF NOT EXISTS idx_network_client_mac_norm "
     "ON network_client (mac_norm)"),
    ("idx_network_client_asset",
     "CREATE INDEX IF NOT EXISTS idx_network_client_asset "
     "ON network_client (asset_id)"),
]


def migrate():
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    cur.execute(DDL)
    for name, ddl in INDEXES:
        cur.execute(ddl)

    # Seed the rogue-device alert rule (idempotent). teams_notify + auto_ticket
    # on so a new unknown pushes to Teams and opens one evolving ticket.
    cur.execute(
        "SELECT 1 FROM alert_rule WHERE category='network' AND alert_type='rogue_device'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO alert_rule (category, alert_type, label, enabled, "
            "auto_ticket, ticket_priority, email_notify, teams_notify, "
            "cooldown_minutes, created_at) VALUES "
            "('network', 'rogue_device', 'Unknown Device on Network', TRUE, "
            "TRUE, 'High', TRUE, TRUE, 60, NOW())")
        print("Seeded alert_rule network/rogue_device.")
    else:
        print("alert_rule network/rogue_device already present.")

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete: network_client table + indexes + alert rule ensured.")


if __name__ == "__main__":
    migrate()
