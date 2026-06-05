"""
Migration: add rmm_remediation_queue table for reconnect-triggered remediation
delivery of general (non-OS-patch) actions — e.g. winget app upgrades via run_script.

OS Windows-Update jobs already have a queue (rmm_patch_job); this table covers
arbitrary remediation actions that the reconnect flush dispatches to roaming agents
that are rarely online. Keep it tiny and generic.

Postgres (production runs PG via the pg_db shim). Idempotent — safe to re-run.
Run once: venv/bin/python migrate_add_remediation_queue.py
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rmm_remediation_queue (
            id            BIGSERIAL PRIMARY KEY,
            agent_id      TEXT NOT NULL,
            asset_id      BIGINT,
            action_type   TEXT NOT NULL,          -- e.g. 'run_script'
            payload       TEXT NOT NULL,          -- JSON message body sent to the agent
            status        TEXT NOT NULL DEFAULT 'queued',
            -- queued / deploying / completed / no_op / failed
            session_id    BIGINT,                 -- correlation id stamped on dispatch;
                                                  -- the agent echoes it back in script_result
            result_json   TEXT,
            notes         TEXT,
            created_by    BIGINT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deployed_at   TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ,
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # Fast lookup for the reconnect flush (agent_id + status) and result correlation.
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmm_remediation_queue_agent_status
            ON rmm_remediation_queue (agent_id, status)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmm_remediation_queue_agent_session
            ON rmm_remediation_queue (agent_id, session_id)
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete: rmm_remediation_queue table + indexes created.")


if __name__ == "__main__":
    migrate()
