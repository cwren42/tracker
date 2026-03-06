"""One-time migration: create support_ticket table.

Usage:
  /var/www/tracker/venv/bin/python migrate_add_support_tickets.py

Safe to run multiple times.
"""

import sqlite3

DB_PATH = "/var/www/tracker/assets.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS support_ticket (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'Open',
                priority TEXT DEFAULT 'Normal',
                source TEXT DEFAULT 'web',

                subject TEXT NOT NULL,
                description TEXT NOT NULL,

                reporter_name TEXT,
                reporter_email TEXT,

                asset_id INTEGER,
                asset_tag TEXT,
                hostname TEXT,

                created_by_user_id INTEGER,
                closed_by_user_id INTEGER,
                created_at TEXT,
                updated_at TEXT,
                closed_at TEXT,

                FOREIGN KEY(asset_id) REFERENCES asset(id),
                FOREIGN KEY(created_by_user_id) REFERENCES user(id),
                FOREIGN KEY(closed_by_user_id) REFERENCES user(id)
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_support_ticket_status ON support_ticket(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_support_ticket_asset_id ON support_ticket(asset_id);")
        conn.commit()
        print("Ensured support_ticket table exists")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
