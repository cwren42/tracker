"""
Convert bigint columns that should be boolean to proper PostgreSQL boolean type.
pgloader migrated SQLite boolean (0/1) columns as bigint instead of boolean.
Run once after migration: python3 fix_bool_columns.py
"""
import psycopg2

DSN = "dbname=tracker user=tracker_user password=tracker_secure_2026 host=localhost"

# (table, column) pairs that should be boolean
BOOL_COLUMNS = [
    # alert / notification
    ("alert_log",               "resolved"),
    ("alert_rule",              "auto_ticket"),
    ("alert_rule",              "email_notify"),
    ("alert_rule",              "enabled"),
    ("alert_rule",              "teams_notify"),
    ("notification_bell",       "read_flag"),
    ("notification_templates",  "enabled"),
    # api / integration
    ("ad_integration_config",   "enabled"),
    ("ad_integration_config",   "use_ssl"),
    ("api_keys",                "enabled"),
    ("email_config",            "enabled"),
    ("email_config",            "use_tls"),
    ("threat_intel_config",     "enabled"),
    ("webhooks",                "enabled"),
    ("webhook_log",             "success"),
    # automation / workflows
    ("automation_rules",        "enabled"),
    ("workflow_definitions",    "enabled"),
    # compliance / incidents
    ("compliance_frameworks",   "enabled"),
    ("compliance_requirements", "notification_required"),
    ("escalation_rules",        "active"),
    ("evidence_custody_chain",  "acknowledged"),
    ("incident_actions",        "rollback_possible"),
    ("incident_escalations",    "acknowledged"),
    ("incident_escalations",    "notification_sent"),
    ("incident_playbook_progress", "completed"),
    ("incident_playbooks",      "active"),
    ("incidents",               "regulatory_notification_required"),
    ("iocs",                    "is_active") if False else None,  # skip - check manually
    # monitoring
    ("profile_check",           "enabled"),
    # oncall / SLA
    ("oncall_schedule",         "active"),
    ("playbook_steps",          "required"),
    ("playbooks",               "active"),
    ("sla_rules",               "active"),
    # reports
    ("report_schedules",        "enabled"),
    ("report_templates",        "is_builtin"),
    # RMM
    ("rmm_agent",               "enabled"),
    ("rmm_connect_token",       "used"),
    ("rmm_eagle_alert_rule",    "email_notify"),
    ("rmm_eagle_alert_rule",    "enabled"),
    ("rmm_eagle_config",        "enabled"),
    ("rmm_eagle_current",       "is_idle"),
    ("rmm_patch_job",           "reboot_required"),
    ("rmm_pending_update",      "reboot_required"),
    ("rmm_telemetry",           "battery_charging"),
    ("rmm_telemetry",           "battery_present"),
    # cve / patch
    ("cve_patch_job",           "reboot_required"),
    # AI
    ("ai_ticket_suggestions",   "auto_mode"),
]

# Filter out None entries
BOOL_COLUMNS = [c for c in BOOL_COLUMNS if c is not None]


def main():
    con = psycopg2.connect(DSN)
    con.autocommit = True
    cur = con.cursor()

    # Get current bigint columns from DB
    cur.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND data_type = 'bigint'
    """)
    bigint_cols = {(r[0], r[1]) for r in cur.fetchall()}

    ok = 0
    skipped = 0
    errors = 0

    for table, col in BOOL_COLUMNS:
        if (table, col) not in bigint_cols:
            print(f"  SKIP  {table}.{col}  (not bigint — already correct or doesn't exist)")
            skipped += 1
            continue
        try:
            # Drop existing integer default, alter type, then restore boolean default
            cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} DROP DEFAULT")
            cur.execute(f"""
                ALTER TABLE {table}
                ALTER COLUMN {col} TYPE boolean
                USING CASE WHEN {col} = 0 THEN false ELSE true END
            """)
            # Re-apply a sensible boolean default based on common naming
            if col in ("enabled", "is_active", "active", "allow_patching",
                       "allow_reboots", "suppress_alerts", "is_current",
                       "automation_enabled"):
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT true")
            else:
                cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT false")
            print(f"  OK    {table}.{col}")
            ok += 1
        except Exception as e:
            print(f"  ERROR {table}.{col}: {e}")
            errors += 1

    cur.close()
    con.close()
    print(f"\nDone: {ok} converted, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
