"""Ensure performance indexes exist on the PostgreSQL database (idempotent).

Previously this targeted the pre-migration SQLite file (assets.db) and so had no
effect on the live Postgres DB. It now targets Postgres via DATABASE_URL.

Run:  set -a; . /var/www/tracker/.secrets.env; set +a; python3 create_indexes.py
"""
import os
import sys

import psycopg2

DSN = os.environ.get('DATABASE_URL')
if not DSN:
    sys.exit('DATABASE_URL not set; run: set -a; . /var/www/tracker/.secrets.env; set +a')

indexes = [
    "CREATE INDEX IF NOT EXISTS idx_device_vuln_severity ON device_vulnerability(severity, status)",
    "CREATE INDEX IF NOT EXISTS idx_device_vuln_asset ON device_vulnerability(asset_id, severity)",
    "CREATE INDEX IF NOT EXISTS idx_rmm_eagle_event_agent ON rmm_eagle_event(agent_id, captured_at)",
    "CREATE INDEX IF NOT EXISTS idx_rmm_metrics_agent ON rmm_metrics_history(agent_id, captured_at)",
    "CREATE INDEX IF NOT EXISTS idx_asset_online_state ON asset(online_state)",
    "CREATE INDEX IF NOT EXISTS idx_asset_employee ON asset(employee_id)",
    "CREATE INDEX IF NOT EXISTS idx_alert_log_rule ON alert_log(rule_id)",
    "CREATE INDEX IF NOT EXISTS idx_ticket_status ON support_ticket(status)",
    "CREATE INDEX IF NOT EXISTS idx_ticket_assigned ON support_ticket(assigned_to_user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_rmm_screenshot_agent ON rmm_screenshot(agent_id, captured_at)",
    "CREATE INDEX IF NOT EXISTS idx_intune_device_asset ON intune_device(asset_id)",
    "CREATE INDEX IF NOT EXISTS idx_policy_control_map ON policy_control_mapping(control_id)",
    "CREATE INDEX IF NOT EXISTS idx_strikegraph_control ON strikegraph_evidence(control_id)",
    "CREATE INDEX IF NOT EXISTS idx_vuln_cache_cve ON vulnerability_cache(cve_id)",
]

conn = psycopg2.connect(DSN)
conn.autocommit = True  # DDL each in its own tx so one failure doesn't abort the rest
cur = conn.cursor()
for sql in indexes:
    try:
        cur.execute(sql)
        idx = sql.split('idx_')[1].split(' ')[0]
        print(f"OK: idx_{idx}")
    except Exception as e:
        print(f"ERR: {sql[:50]} -> {e}")
cur.close()
conn.close()
print("All done.")
