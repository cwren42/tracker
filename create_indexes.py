import sqlite3
c = sqlite3.connect('/var/www/tracker/assets.db')
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
for sql in indexes:
    try:
        c.execute(sql)
        idx = sql.split('idx_')[1].split(' ')[0]
        print(f"OK: idx_{idx}")
    except Exception as e:
        print(f"ERR: {sql[:40]} -> {e}")
c.commit()
c.close()
print("All done.")
