"""
Migration: give rmm_patch_job a STABLE asset_id and re-key OS-patch dispatch to it.

OS Windows-Update jobs (rmm_patch_job) were matched to agents by the drift-prone
agent_id string, so a renamed / re-enrolled / mis-cased box stranded its queued
jobs. The gateway now dual-reads (asset_id preferred, agent_id fallback for
not-yet-backfilled rows). This adds the asset_id column, backfills it from the
agent_id via rmm_agent, and adds a covering index for the (asset_id, status)
dispatch filter.

Idempotent — ADD COLUMN IF NOT EXISTS, backfill only where asset_id IS NULL, index
IF NOT EXISTS. Safe to re-run.

NOTE: CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so the index
is created on a separate autocommit connection.

Postgres (production runs PG via the pg_db shim).
Run once: venv/bin/python migrate_add_patchjob_asset.py
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
    # 1) Add column + backfill (transactional).
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    cur.execute(
        "ALTER TABLE rmm_patch_job ADD COLUMN IF NOT EXISTS asset_id BIGINT"
    )
    conn.commit()
    cur.execute(
        "UPDATE rmm_patch_job j "
        "SET asset_id = ra.asset_id "
        "FROM rmm_agent ra "
        "WHERE j.asset_id IS NULL AND ra.agent_id = j.agent_id"
    )
    backfilled = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    print(f"rmm_patch_job.asset_id ensured; backfilled {backfilled} row(s).")

    # 2) Create the covering index (autocommit — CONCURRENTLY can't run in a txn).
    conn = psycopg2.connect(_dsn())
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "idx_rmm_patch_job_asset_status "
        "ON rmm_patch_job (asset_id, status)"
    )
    cur.close()
    conn.close()
    print("Migration complete: idx_rmm_patch_job_asset_status ensured.")


if __name__ == "__main__":
    migrate()
