"""
Migration: re-key the general remediation queue off the STABLE asset_id.

Command dispatch used to match the drift-prone agent_id string. Renamed /
re-enrolled / mis-cased boxes (asset "Ken-Lenovo" -> agent_id "KEN-DELL";
"ChrisHome" -> "CHRISHOME") stranded commands as undeliverable 'queued' orphans
because dispatch matched agent_id exactly. The gateway now keys dispatch on
rmm_remediation_queue.asset_id; this backfills asset_id for any legacy rows that
never had one (from their agent_id via rmm_agent) and adds a covering index for
the new (asset_id, status) dispatch filter.

Idempotent — safe to re-run. The backfill only touches rows where asset_id IS NULL,
and the index uses IF NOT EXISTS.

NOTE: CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so the index
is created on a separate autocommit connection.

Postgres (production runs PG via the pg_db shim).
Run once: venv/bin/python migrate_rekey_remediation_asset.py
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


def migrate():
    # 1) Backfill asset_id for legacy rows (transactional).
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    cur.execute(
        "UPDATE rmm_remediation_queue q "
        "SET asset_id = ra.asset_id "
        "FROM rmm_agent ra "
        "WHERE q.asset_id IS NULL AND ra.agent_id = q.agent_id"
    )
    backfilled = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    print(f"Backfilled asset_id on {backfilled} rmm_remediation_queue row(s).")

    # 2) Create the covering index (autocommit — CONCURRENTLY can't run in a txn).
    conn = psycopg2.connect(_dsn())
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "idx_rmm_remediation_queue_asset_status "
        "ON rmm_remediation_queue (asset_id, status)"
    )
    cur.close()
    conn.close()
    print("Migration complete: idx_rmm_remediation_queue_asset_status ensured.")


if __name__ == "__main__":
    migrate()
