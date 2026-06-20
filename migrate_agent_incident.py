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

    # detect_count: how many times re-detection has refreshed this OPEN incident
    # (the idempotent dedup bumps this instead of inserting a duplicate row).
    # Added later — guard with IF NOT EXISTS so the migration stays re-runnable.
    cur.execute("""
        ALTER TABLE agent_incident
            ADD COLUMN IF NOT EXISTS detect_count INTEGER NOT NULL DEFAULT 1
    """)

    # Statuses that mean the incident is still OPEN (not in a terminal state).
    # The partial-unique index below prevents a second OPEN incident for the same
    # DEDUP_KEY — the dedup guarantee that stops the feed flooding.
    #
    # The open-incident identity is the dedup_key (NOT (asset_id, signal_type)),
    # because dedup_key already encodes the right granularity per signal:
    #   disk_low:{asset}:{drive}      — per (box, drive)
    #   service_down:{asset}:{svc}    — per (box, SERVICE)  ← a box can have many
    #   agent_offline:{asset}         — per box
    #   patch_failed:{job_id}         — per failed job
    #   defender_critical:{asset}     — per box
    # Keying on (asset_id, signal_type) wrongly collapsed multiple down services
    # into ONE service_down incident (a Spooler detection got absorbed into an
    # already-open BITS incident). Keying on dedup_key gives 1-per-SERVICE for
    # service_down while keeping 1-per-box for disk_low/defender/offline (their
    # dedup_key is stable per box) — so this does NOT reintroduce the 5x-duplicate
    # bug fixed in 2ede87b (that fix is the single-instance scan lock +
    # check-then-insert; the dedup_keys here are stable per scan).
    #
    # NOTE: Tier-0 auto incidents also enter 'remediating' (an OPEN status, in this
    # predicate) rather than going straight to a terminal 'auto_handled', so this
    # index covers them too. Terminal: resolved | dismissed | auto_handled |
    # escalated.
    #
    # MIGRATION: the index was previously on (asset_id, signal_type). Drop the old
    # definition and recreate on (dedup_key). Idempotent + careful: only drop if
    # the existing index is NOT already the dedup_key one (so re-running is a
    # no-op), and recreate inside the same transaction.
    old_def = None
    cur.execute("""
        SELECT indexdef FROM pg_indexes
        WHERE schemaname = current_schema()
          AND indexname = 'uq_agent_incident_open'
    """)
    _row = cur.fetchone()
    if _row:
        old_def = _row[0] or ''
    # Recreate only when the live index isn't already keyed on dedup_key.
    if (old_def is None) or ('(dedup_key)' not in old_def.replace(' ', '')):
        cur.execute("DROP INDEX IF EXISTS uq_agent_incident_open")
        cur.execute("""
            CREATE UNIQUE INDEX uq_agent_incident_open
                ON agent_incident (dedup_key)
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
    print("Migration complete: agent_incident table + detect_count + indexes "
          "created (uq_agent_incident_open keyed on dedup_key).")


if __name__ == "__main__":
    migrate()
