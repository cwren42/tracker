#!/usr/bin/env python3
"""
Migration: add Exchange Online quarantine tables.

Tables created:
  quarantine_message  — cached quarantine message metadata
  quarantine_ioc      — indicators of compromise extracted per message
"""
import os
import sys
import psycopg2

DSN = os.environ.get('DATABASE_URL') or sys.exit('DATABASE_URL not set; run: set -a; . /var/www/tracker/.secrets.env; set +a')


def run():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    print("Creating quarantine_message …")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quarantine_message (
            id                      BIGSERIAL PRIMARY KEY,
            message_id              TEXT NOT NULL,
            internet_message_id     TEXT,
            sender_address          TEXT,
            sender_display_name     TEXT,
            sender_domain           TEXT,
            recipient_address       TEXT,
            subject                 TEXT,
            received_time           TIMESTAMPTZ,
            expiry_time             TIMESTAMPTZ,
            quarantine_reason       TEXT,
            policy_type             TEXT,
            threat_type             TEXT,
            spf_result              TEXT,
            dkim_result             TEXT,
            dmarc_result            TEXT,
            release_status          TEXT NOT NULL DEFAULT 'Quarantined',
            released_by             TEXT,
            released_at             TIMESTAMPTZ,
            url_count               INTEGER NOT NULL DEFAULT 0,
            attachment_count        INTEGER NOT NULL DEFAULT 0,
            urls_json               TEXT,
            attachments_json        TEXT,
            raw_headers             TEXT,
            campaign_id             TEXT,
            last_synced             TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT quarantine_message_id_unique UNIQUE (message_id)
        )
    """)

    print("Creating quarantine_ioc …")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quarantine_ioc (
            id          BIGSERIAL PRIMARY KEY,
            message_id  TEXT NOT NULL REFERENCES quarantine_message(message_id) ON DELETE CASCADE,
            ioc_type    TEXT NOT NULL,
            ioc_value   TEXT NOT NULL,
            threat_label TEXT,
            first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
            seen_count  INTEGER NOT NULL DEFAULT 1
        )
    """)

    print("Creating indexes …")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_sender_domain  ON quarantine_message(sender_domain)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_received_time  ON quarantine_message(received_time DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_threat_type    ON quarantine_message(threat_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_release_status ON quarantine_message(release_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qm_campaign_id    ON quarantine_message(campaign_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qioc_message_id   ON quarantine_ioc(message_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_qioc_ioc_value    ON quarantine_ioc(ioc_value)")

    conn.commit()
    print("Migration complete.")
    cur.close()
    conn.close()


if __name__ == '__main__':
    try:
        run()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
