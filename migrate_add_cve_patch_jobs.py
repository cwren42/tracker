"""Migration: add cve_patch_job table for CVE-driven patch deployment."""
import sqlite3

DB = "/var/www/tracker/assets.db"


def run():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS cve_patch_job (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id        INTEGER,
            agent_id        TEXT NOT NULL,
            cve_id          TEXT NOT NULL,
            status          TEXT DEFAULT 'queued',
            deployed_by     TEXT,
            deployed_at     TEXT,
            completed_at    TEXT,
            result_json     TEXT,
            reboot_required INTEGER DEFAULT 0,
            updates_found   INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now', '-7 hours')),
            updated_at      TEXT DEFAULT (datetime('now', '-7 hours'))
        );

        CREATE INDEX IF NOT EXISTS ix_cve_patch_job_cve
            ON cve_patch_job(cve_id);
        CREATE INDEX IF NOT EXISTS ix_cve_patch_job_asset
            ON cve_patch_job(asset_id);
        CREATE INDEX IF NOT EXISTS ix_cve_patch_job_agent
            ON cve_patch_job(agent_id);
        CREATE INDEX IF NOT EXISTS ix_cve_patch_job_status
            ON cve_patch_job(status);
    """)

    conn.commit()
    conn.close()
    print("Migration complete: cve_patch_job table created.")


if __name__ == "__main__":
    run()
