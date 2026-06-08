"""
Migration: add rmm_remediation_queue.ticket_id (nullable).

The disk-space loop dispatches a READ-ONLY "what's filling it" diagnostic
run_script when a disk_critical alert ticket is created, and wants the result
attached back to THAT ticket as a note. We correlate by stamping the originating
ticket_id onto the queue row; when the gateway receives the script_result it
posts a ticket_note (see rmm_gateway/main.py remediation-result block).

Nullable + idempotent — existing rows (winget CVE remediations etc.) keep
ticket_id NULL and behave exactly as before.

Postgres (production runs PG via the pg_db shim). Safe to re-run.
Run once: venv/bin/python migrate_add_remediation_ticket_id.py
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
    cur.execute(
        "ALTER TABLE rmm_remediation_queue "
        "ADD COLUMN IF NOT EXISTS ticket_id BIGINT"
    )
    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete: rmm_remediation_queue.ticket_id added (nullable).")


if __name__ == "__main__":
    migrate()
