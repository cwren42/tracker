"""One-time migration: create RMM gateway tables.

Tables:
- rmm_agent: enrollment + token hash + last seen
- rmm_session: audited technician sessions
- rmm_event: per-session event log (commands, output, etc.)

Usage:
  /var/www/tracker/venv/bin/python migrate_add_rmm_gateway.py

Safe to run multiple times.
"""

import sqlite3

DB_PATH = "/var/www/tracker/assets.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rmm_agent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL UNIQUE,
                asset_id INTEGER,
                agent_token_sha256 TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at TEXT,
                last_seen_at TEXT,
                FOREIGN KEY(asset_id) REFERENCES asset(id)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rmm_agent_asset_id ON rmm_agent(asset_id);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rmm_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id INTEGER,
                started_by_user_id INTEGER,
                reason TEXT,
                started_at TEXT,
                ended_at TEXT,
                FOREIGN KEY(asset_id) REFERENCES asset(id),
                FOREIGN KEY(started_by_user_id) REFERENCES user(id)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rmm_session_asset_id ON rmm_session(asset_id);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rmm_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                actor_type TEXT NOT NULL,
                event_type TEXT NOT NULL,
                data_json TEXT,
                created_at TEXT,
                FOREIGN KEY(session_id) REFERENCES rmm_session(id)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rmm_event_session_id ON rmm_event(session_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rmm_event_created_at ON rmm_event(created_at);")

        conn.commit()
        print("Ensured rmm_agent, rmm_session, rmm_event tables exist")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
