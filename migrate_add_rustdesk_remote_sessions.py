"""One-time migration: add RustDesk fields to asset table and create remote_session table.

Usage:
  /var/www/tracker/venv/bin/python migrate_add_rustdesk_remote_sessions.py

This is intentionally sqlite3-based (no Alembic) and safe to run multiple times.
"""

import sqlite3

DB_PATH = "/var/www/tracker/assets.db"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")

        # Add columns to asset (if missing)
        if not _column_exists(conn, "asset", "rustdesk_id"):
            conn.execute("ALTER TABLE asset ADD COLUMN rustdesk_id TEXT")
            print("Added asset.rustdesk_id")
        if not _column_exists(conn, "asset", "rustdesk_password"):
            conn.execute("ALTER TABLE asset ADD COLUMN rustdesk_password TEXT")
            print("Added asset.rustdesk_password")

        # Create remote_session table (if missing)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS remote_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool TEXT NOT NULL,
                asset_id INTEGER NOT NULL,
                started_by_user_id INTEGER NOT NULL,
                ended_by_user_id INTEGER,
                reason TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                FOREIGN KEY(asset_id) REFERENCES asset(id),
                FOREIGN KEY(started_by_user_id) REFERENCES user(id),
                FOREIGN KEY(ended_by_user_id) REFERENCES user(id)
            )
            """
        )
        print("Ensured remote_session table exists")

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
