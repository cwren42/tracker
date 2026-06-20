"""
Migration: add incident_message table — the per-incident CHAT thread behind the
AI Triage Chat layer (Phase 2 of Proactive AI Remediation).

Each row is one turn in a conversation: the AI's triage reasoning, the read-only
diagnostic tool-calls it made (and their output), the tech's replies, and the
gated change proposals + their execution results.

Also adds two columns to agent_incident so the triage loop is idempotent and
auditable:
  * triage_state   — 'pending' | 'running' | 'done' | 'error'  (lazy-vs-auto + lock)
  * proposed_fix   — JSONB: the AI's current gated change proposal awaiting Approve

Additive + idempotent (safe to re-run). Postgres via psycopg2 (mirrors
migrate_agent_incident.py).

Run once: venv/bin/python migrate_incident_message.py
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
        CREATE TABLE IF NOT EXISTS incident_message (
            id            BIGSERIAL PRIMARY KEY,
            incident_id   BIGINT NOT NULL REFERENCES agent_incident(id) ON DELETE CASCADE,
            role          TEXT NOT NULL,    -- user | assistant | tool | system
            content       TEXT,             -- rendered text (assistant prose, user reply, tool summary)
            tool_name     TEXT,             -- for role='assistant' tool requests / role='tool' results
            tool_call     JSONB,            -- the tool call arguments the AI requested
            tool_result   JSONB,            -- the tool's structured result (audit)
            proposed_fix  JSONB,            -- a gated CHANGE proposal carried on an assistant turn
            meta          JSONB,            -- {model, iterations, tokens, error, ...} audit
            created_by    BIGINT,           -- user id for role='user'; NULL for AI/tool
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_incident_message_incident
            ON incident_message (incident_id, created_at)
    """)

    # agent_incident augmentations (idempotent ADD COLUMN IF NOT EXISTS).
    cur.execute("""
        ALTER TABLE agent_incident
            ADD COLUMN IF NOT EXISTS triage_state TEXT,
            ADD COLUMN IF NOT EXISTS proposed_fix JSONB
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete: incident_message table + agent_incident.triage_state/proposed_fix.")


if __name__ == "__main__":
    migrate()
