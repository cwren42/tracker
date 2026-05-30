#!/usr/bin/env python3
"""
Migration: add Windows backup tables.

Tables created:
  rmm_backup_policy          — named, reusable backup policies
  rmm_agent_backup_policy    — per-agent policy assignment (1:1)
  rmm_backup_job             — one row per backup run
"""
import os
import sys
import psycopg2

DSN = os.environ.get('DATABASE_URL') or sys.exit('DATABASE_URL not set; run: set -a; . /var/www/tracker/.secrets.env; set +a')


def run():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    print("Creating rmm_backup_policy …")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rmm_backup_policy (
            id                      BIGSERIAL PRIMARY KEY,
            name                    TEXT NOT NULL,
            description             TEXT,
            enabled                 BOOLEAN NOT NULL DEFAULT true,
            nas_unc_path            TEXT NOT NULL DEFAULT '',
            nas_type                TEXT NOT NULL DEFAULT 'smb',
            include_paths           JSONB NOT NULL DEFAULT '[]',
            exclude_extensions      JSONB NOT NULL DEFAULT '[".tmp",".log",".iso",".vhd",".vmdk",".vhdx"]',
            exclude_folders         JSONB NOT NULL DEFAULT '["node_modules",".git","$RECYCLE.BIN","Windows","Program Files","Program Files (x86)"]',
            max_file_size_mb        INTEGER NOT NULL DEFAULT 500,
            full_backup_interval_days INTEGER NOT NULL DEFAULT 7,
            retention_days          INTEGER NOT NULL DEFAULT 30,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT rmm_backup_policy_name_unique UNIQUE (name)
        )
    """)

    print("Creating rmm_agent_backup_policy …")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rmm_agent_backup_policy (
            id          BIGSERIAL PRIMARY KEY,
            agent_id    TEXT NOT NULL,
            policy_id   BIGINT REFERENCES rmm_backup_policy(id) ON DELETE SET NULL,
            enabled     BOOLEAN NOT NULL DEFAULT true,
            extra_paths JSONB NOT NULL DEFAULT '[]',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT rmm_agent_backup_policy_agent_unique UNIQUE (agent_id)
        )
    """)

    print("Creating rmm_backup_job …")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rmm_backup_job (
            id                BIGSERIAL PRIMARY KEY,
            agent_id          TEXT NOT NULL,
            job_type          TEXT NOT NULL DEFAULT 'full',
            status            TEXT NOT NULL DEFAULT 'running',
            started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at      TIMESTAMPTZ,
            files_copied      INTEGER NOT NULL DEFAULT 0,
            files_skipped     INTEGER NOT NULL DEFAULT 0,
            files_failed      INTEGER NOT NULL DEFAULT 0,
            bytes_transferred BIGINT NOT NULL DEFAULT 0,
            snapshot_path     TEXT,
            errors_json       JSONB,
            triggered_by      TEXT NOT NULL DEFAULT 'scheduled'
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmm_backup_job_agent
        ON rmm_backup_job(agent_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmm_backup_job_started
        ON rmm_backup_job(started_at DESC)
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete.")


if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
