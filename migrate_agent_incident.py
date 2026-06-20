"""
Migration: add agent_incident table — the record behind the Proactive AI
Remediation feed (detect -> AI-diagnose -> propose fix -> approve -> remediate
-> verify -> close).

Additive only. Reuses the existing rmm_remediation_queue for execution; this
table is purely the incident/decision record + audit trail. Postgres (production
runs PG via the pg_db shim). Idempotent — safe to re-run.

Run once: venv/bin/python migrate_agent_incident.py
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
        CREATE TABLE IF NOT EXISTS agent_incident (
            id                  BIGSERIAL PRIMARY KEY,
            asset_id            BIGINT,
            agent_id            TEXT,
            signal_type         TEXT NOT NULL,
            -- disk_low | service_down | agent_offline_but_up | patch_failed | defender_critical
            severity            TEXT NOT NULL DEFAULT 'warning',  -- info|warning|critical
            dedup_key           TEXT NOT NULL,                    -- stable per-incident key
            status              TEXT NOT NULL DEFAULT 'new',
            -- new|diagnosed|awaiting_approval|remediating|resolved|escalated|dismissed|auto_handled
            diagnosis_text      TEXT,
            ai_confidence       REAL,
            ai_model            TEXT,
            proposed_actions    JSONB,   -- [{key,label,kind,risk_tier,run_payload}]
            chosen_action       TEXT,
            remediation_queue_id BIGINT REFERENCES rmm_remediation_queue(id),
            pushed_channel      TEXT,    -- 'in_app' now; 'teams' later (Phase 2)
            approved_by         BIGINT,
            approved_at         TIMESTAMPTZ,
            resolved_at         TIMESTAMPTZ,
            verify_result       TEXT,
            attempt_count       INTEGER NOT NULL DEFAULT 0,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # Statuses that mean the incident is still OPEN (not in a terminal state).
    # The partial-unique index below prevents a second OPEN incident for the same
    # (asset_id, signal_type) — the dedup guarantee that stops the feed flooding.
    # Terminal: resolved | dismissed | auto_handled | escalated.
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_incident_open
            ON agent_incident (asset_id, signal_type)
            WHERE status IN ('new','diagnosed','awaiting_approval','remediating')
    """)

    # Feed query: open incidents newest-first.
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_incident_status_created
            ON agent_incident (status, created_at DESC)
    """)
    # Verify pass: find remediating incidents by their queue row.
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_incident_remq
            ON agent_incident (remediation_queue_id)
            WHERE remediation_queue_id IS NOT NULL
    """)
    # Cooldown-after-resolve lookups per (asset, signal).
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_incident_asset_signal
            ON agent_incident (asset_id, signal_type, created_at DESC)
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete: agent_incident table + indexes created.")


if __name__ == "__main__":
    migrate()
