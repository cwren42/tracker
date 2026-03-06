#!/usr/bin/env python3
"""Migration: extended RMM telemetry, metrics history, availability, patches."""
import sqlite3
DB = "/var/www/tracker/assets.db"

def col_exists(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def run():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # ── New columns on rmm_telemetry ───────────────────────────────────────
    new_cols = [
        ("timezone",         "TEXT"),
        ("last_login_user",  "TEXT"),
        ("last_login_time",  "TEXT"),
        ("vendor",           "TEXT"),
        ("model_name",       "TEXT"),
        ("serial_number",    "TEXT"),
        ("motherboard",      "TEXT"),
        ("bios_manufacturer","TEXT"),
        ("bios_version",     "TEXT"),
        ("bios_date",        "TEXT"),
        ("gpu_json",         "TEXT"),
        ("sound_card",       "TEXT"),
        ("os_edition",       "TEXT"),
        ("security_json",    "TEXT"),
    ]
    for col, typ in new_cols:
        if not col_exists(cur, "rmm_telemetry", col):
            cur.execute(f"ALTER TABLE rmm_telemetry ADD COLUMN {col} {typ}")
            print(f"  + rmm_telemetry.{col}")

    # ── rmm_metrics_history ────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rmm_metrics_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id    TEXT    NOT NULL,
            cpu_percent REAL,
            ram_percent REAL,
            captured_at TEXT    NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmm_metrics_agent_time
        ON rmm_metrics_history(agent_id, captured_at)
    """)
    print("  + rmm_metrics_history")

    # ── rmm_availability ───────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rmm_availability (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id    TEXT    NOT NULL,
            event       TEXT    NOT NULL,   -- 'online' | 'offline'
            occurred_at TEXT    NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmm_avail_agent_time
        ON rmm_availability(agent_id, occurred_at)
    """)
    print("  + rmm_availability")

    # ── rmm_patch ──────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rmm_patch (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id     TEXT    NOT NULL,
            hotfix_id    TEXT,
            description  TEXT,
            installed_on TEXT,
            captured_at  TEXT    NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmm_patch_agent
        ON rmm_patch(agent_id)
    """)
    print("  + rmm_patch")

    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    run()
