"""
Migration: add rmm_pending_update and rmm_patch_job tables for patch management.
Run once: python migrate_add_patch_management.py
"""
import sqlite3

DB_PATH = "/var/www/tracker/assets.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS rmm_pending_update (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id        TEXT NOT NULL,
            update_id       TEXT NOT NULL,
            title           TEXT,
            kb_ids          TEXT,   -- JSON list e.g. ["KB5034441"]
            severity        TEXT,   -- Critical / Important / Moderate / Low / ""
            size_mb         REAL,
            reboot_required INTEGER DEFAULT 0,
            category        TEXT,
            recorded_at     TEXT DEFAULT (datetime('now')),
            UNIQUE(agent_id, update_id) ON CONFLICT REPLACE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS rmm_patch_job (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id        TEXT NOT NULL,
            update_ids      TEXT NOT NULL,  -- JSON list of Windows Update GUIDs
            kb_ids          TEXT,           -- JSON list of KB IDs (display only)
            titles          TEXT,           -- JSON list of patch titles
            status          TEXT DEFAULT 'queued',
            -- queued / deploying / installed / failed / deferred
            approved_by     INTEGER,        -- user.id who approved
            approved_at     TEXT,
            deployed_at     TEXT,
            completed_at    TEXT,
            result_json     TEXT,           -- JSON from agent after install
            reboot_required INTEGER DEFAULT 0,
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    print("Migration complete: rmm_pending_update, rmm_patch_job tables created.")


if __name__ == "__main__":
    migrate()
