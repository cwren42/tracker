"""
Migration: add incident_fix_outcome table — the lightweight LEARNING LOOP behind
Proactive AI Remediation (Feature 3).

Each row records the OUTCOME of a remediation attempt: which action was applied to
which signal on which asset, and whether it resolved the condition. The verify
pass writes one row when an incident reaches a terminal verdict (resolved /
auto_handled = success; escalated / parked-after-failure = failure).

Used to:
  (a) surface "this fix resolved N/M past cases of this signal" in the triage chat
      (triage_agent.tool_get_similar_past_fixes), and
  (b) nudge the AI's recommended-fix confidence with a real fleet success rate.

Additive + idempotent (safe to re-run). Postgres via psycopg2 (mirrors
migrate_agent_incident.py).

Run once: venv/bin/python migrate_incident_fix_outcome.py
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
        CREATE TABLE IF NOT EXISTS incident_fix_outcome (
            id            BIGSERIAL PRIMARY KEY,
            incident_id   BIGINT,        -- source incident (no FK: keep rows if incident purged)
            asset_id      BIGINT,
            signal_type   TEXT NOT NULL,
            chosen_action TEXT,          -- the action key that was applied (e.g. clear_caches)
            success       BOOLEAN NOT NULL,
            detail        TEXT,          -- short verify_result snapshot
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # Aggregate lookups: success rate per (signal, action).
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_incident_fix_outcome_signal_action
            ON incident_fix_outcome (signal_type, chosen_action)
    """)
    # Idempotency guard: one outcome row per incident (the verify pass may pass the
    # same incident more than once across runs — write once).
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_incident_fix_outcome_incident
            ON incident_fix_outcome (incident_id)
            WHERE incident_id IS NOT NULL
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete: incident_fix_outcome table + indexes created.")


if __name__ == "__main__":
    migrate()
