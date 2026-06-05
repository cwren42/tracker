"""
Migration: add an `attempts` counter to the reconnect-remediation queues so a job
that is picked up but never finishes (agent sleeps mid-run -> deploying ->
stale-reset -> queued -> re-flush) is NOT redelivered forever. The gateway
increments `attempts` on each dispatch and moves a row to a terminal 'abandoned'
status once it exceeds the cap.

Applies to:
  * rmm_remediation_queue.attempts  (general run_script remediation)
  * rmm_patch_job.attempts          (OS Windows-Update on-connect re-queue path)

Postgres (production runs PG via the pg_db shim). Idempotent — safe to re-run.
Run once: venv/bin/python migrate_add_remediation_attempts.py
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
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    # ADD COLUMN IF NOT EXISTS is idempotent in PG 9.6+. integer, default 0.
    cur.execute(
        "ALTER TABLE rmm_remediation_queue "
        "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
    )
    cur.execute(
        "ALTER TABLE rmm_patch_job "
        "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
    )
    conn.commit()
    # Verify the columns exist with the expected type/default.
    cur.execute(
        """SELECT table_name, column_name, data_type, column_default, is_nullable
             FROM information_schema.columns
            WHERE column_name='attempts'
              AND table_name IN ('rmm_remediation_queue', 'rmm_patch_job')
            ORDER BY table_name"""
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    print("Migration complete: attempts columns ->")
    for r in rows:
        print("  ", r)


if __name__ == "__main__":
    migrate()
