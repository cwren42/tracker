"""
Add monitoring check results and Linux agent heartbeat tables
"""

import sqlite3
from datetime import datetime

def migrate():
    conn = sqlite3.connect('/var/www/tracker/assets.db')
    cursor = conn.cursor()
    
    print("Adding monitoring check result table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_check_result (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            check_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            value TEXT,
            message TEXT,
            checked_at TIMESTAMP NOT NULL,
            FOREIGN KEY (asset_id) REFERENCES assets(id),
            FOREIGN KEY (check_id) REFERENCES monitoring_check(id)
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_check_result_asset 
        ON monitoring_check_result(asset_id, checked_at)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_check_result_check 
        ON monitoring_check_result(check_id, checked_at)
    """)
    
    print("Adding Linux agent heartbeat table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS linux_agent_heartbeat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            asset_id INTEGER,
            system_info TEXT,
            metrics TEXT,
            disks TEXT,
            services_running INTEGER,
            updates_available INTEGER,
            security_updates INTEGER,
            timestamp TIMESTAMP NOT NULL,
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_heartbeat_asset 
        ON linux_agent_heartbeat(asset_id, timestamp)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_heartbeat_agent 
        ON linux_agent_heartbeat(agent_id, timestamp)
    """)
    
    print("Adding RMM commands table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rmm_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            command TEXT NOT NULL,
            command_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result TEXT,
            exit_code INTEGER,
            created_at TIMESTAMP NOT NULL,
            executed_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmm_commands_agent 
        ON rmm_commands(agent_id, status)
    """)
    
    print("Adding failure_count to monitoring_alert if not exists...")
    try:
        cursor.execute("""
            ALTER TABLE monitoring_alert ADD COLUMN failure_count INTEGER DEFAULT 1
        """)
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise
    
    try:
        cursor.execute("""
            ALTER TABLE monitoring_alert ADD COLUMN first_failed_at TIMESTAMP
        """)
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise
    
    try:
        cursor.execute("""
            ALTER TABLE monitoring_alert ADD COLUMN last_failed_at TIMESTAMP
        """)
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise
    
    conn.commit()
    conn.close()
    
    print("✓ Migration complete")

if __name__ == '__main__':
    migrate()
